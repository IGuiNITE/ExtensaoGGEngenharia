# -*- coding: utf-8 -*-
import math
import re
from pyrevit import DB, UI, forms, revit, script

from geom_utils import *
from revit_mep import *
from ui_utils import *
import config_modelo

ALTURA_BAIXA = 1000 / 304.8
ALTURA_ALTA = 1500 / 304.8
ALTURA_REGISTRO_GERAL = 2100 / 304.8
DESLOC_CHUVEIRO = 150 / 304.8   
TOL_ANGULO = 2.0                
PASSO_ANGULO = 15.0             
TOL_COINC = 40 / 304.8          
DESNIVEL_CANTO = 100 / 304.8    
ALTURA_STUB = 200 / 304.8       
COMPRIMENTO_TUBINHO = 14.1 / 304.8
NOME_SISTEMA_AGUA = config_modelo.obter("SISTEMA_AGUA_FRIA", u"Água Fria Potável")
FAMILIA_REGISTRO_GERAL = config_modelo.obter("FAMILIA_REGISTRO_GERAL", u"RG_Registro de Gaveta c/ PVC")
FAMILIA_REGISTRO_PRESSAO = config_modelo.obter("FAMILIA_REGISTRO_PRESSAO", u"RP_Registro de Pressão C/ PVC")

INVERTER_BUCHA = 0

BUCHAS_IDS = []
AVISOS = []
INFO = []
REG_INF = [None]


class FiltroPecas(UI.Selection.ISelectionFilter):
    def AllowElement(self, elem):
        cat_id = elem.Category.Id.IntegerValue
        return cat_id in [int(DB.BuiltInCategory.OST_PlumbingFixtures), int(DB.BuiltInCategory.OST_PipeAccessory)]
    def AllowReference(self, ref, pos): return False


def obter_sistema_agua():
    achado = achar_por_nome(DB.Plumbing.PipingSystemType, NOME_SISTEMA_AGUA)
    if not achado: raise Exception(u"Sistema '{}' não encontrado.".format(NOME_SISTEMA_AGUA))
    return achado.Id


def escolher_tipo_agua(doc):
    tipos = sorted(DB.FilteredElementCollector(doc).OfClass(DB.Plumbing.PipeType), key=type_name)
    for t in tipos:
        nome_tipo = type_name(t).lower()
        if (u"marrom" in nome_tipo or u"soldável" in nome_tipo or u"soldavel" in nome_tipo) and u"esgoto" not in nome_tipo:
            return t.Id
    for t in tipos:
        nome_tipo = type_name(t).lower()
        if (u"água" in nome_tipo or u"agua" in nome_tipo) and u"esgoto" not in nome_tipo:
            return t.Id
    nomes = [type_name(t) for t in tipos]
    picked = forms.SelectFromList.show(nomes, title=u"Tipo de Tubo (Água)", button_name=u"Usar este tipo", multiselect=False)
    return tipos[nomes.index(picked)].Id if picked else None


def conector_agua(elem, click_pt=None):
    conns = []
    for c in end_piping(elem, only_open=True):
        if c.PipeSystemType in [DB.Plumbing.PipeSystemType.DomesticColdWater, DB.Plumbing.PipeSystemType.Global]:
            conns.append(c)
    if not conns: 
        for c in end_piping(elem, only_open=True):
            conns.append(c)

    if not conns:
        return None

    if click_pt and len(conns) > 1:
        try:
            conns.sort(key=lambda c: c.Origin.DistanceTo(click_pt))
        except:
            pass

    return conns[0]


def obter_vetores_parede(conector):
    bz = conector.CoordinateSystem.BasisZ
    n_base = DB.XYZ(-bz.X, -bz.Y, 0)

    if n_base.GetLength() < 0.1: 
        n_base = DB.XYZ(0, 1, 0)

    n_base = n_base.Normalize()
    eixo = DB.XYZ(-n_base.Y, n_base.X, 0).Normalize()

    return n_base, eixo


def eixo_peca_conector(conector, e0):
    if not conector: return e0
    bz = conector.CoordinateSystem.BasisZ
    n_in = DB.XYZ(-bz.X, -bz.Y, 0)
    if n_in.GetLength() < 0.1: return e0
    n_in = n_in.Normalize()
    return DB.XYZ(-n_in.Y, n_in.X, 0).Normalize()


def alinhar_pecas(doc, ref_elem, outras_pecas, click_pts):
    c_ref = conector_agua(ref_elem, click_pts.get(ref_elem.Id.IntegerValue))
    if not c_ref: return
    loc_ref = c_ref.Origin
    n0, eixo_parede = obter_vetores_parede(c_ref)

    for elem in outras_pecas:
        if elem.Category.Id.IntegerValue != int(DB.BuiltInCategory.OST_PlumbingFixtures): continue
        c_atual = conector_agua(elem, click_pts.get(elem.Id.IntegerValue))
        if not c_atual: continue

        pt_atual = c_atual.Origin
        vec = pt_atual - loc_ref
        dist_proj = vec.DotProduct(eixo_parede)
        pt_alvo = loc_ref + eixo_parede.Multiply(dist_proj)

        move_vec = DB.XYZ(pt_alvo.X - pt_atual.X, pt_alvo.Y - pt_atual.Y, 0)
        if move_vec.GetLength() > mm(1):
            DB.ElementTransformUtils.MoveElement(doc, elem.Id, move_vec)


def obter_simbolo_conexao(doc, palavras_chave):
    simbolos = DB.FilteredElementCollector(doc).OfClass(DB.FamilySymbol).OfCategory(DB.BuiltInCategory.OST_PipeFitting)
    for sym in simbolos:
        nome_completo = ((sym.FamilyName or "") + " " + type_name(sym)).lower()
        if all(p.lower() in nome_completo for p in palavras_chave):
            if not sym.IsActive: sym.Activate()
            return sym
    return None


def _mm_do_nome(nome):
    try:
        m = re.search(r'(\d+(?:[.,]\d+)?)\s*mm', (nome or u"").lower())
        if m: return float(m.group(1).replace(',', '.'))
    except Exception:
        pass
    return None


def obter_simbolo_por_diametro(doc, nome_familia, dia_ft, preferir=None):
    simbolos = DB.FilteredElementCollector(doc).OfClass(DB.FamilySymbol).OfCategory(DB.BuiltInCategory.OST_PipeAccessory)
    nome_busca = nome_familia.strip().lower()
    candidatos = [s for s in simbolos if nome_busca in (s.FamilyName or u"").strip().lower()]
    if not candidatos: return None

    dia_mm = dia_ft * 304.8

    def pontuacao(sym):
        nome = (type_name(sym) or u"").lower()
        pref_ok = 0
        for palavra in (preferir or []):
            p = palavra.lower()
            if (p in nome) and ((u"sem " + p) not in nome):
                pref_ok -= 1
        d = _mm_do_nome(nome)
        diff = abs(d - dia_mm) if d is not None else 999.0
        return (pref_ok, diff)

    melhor = min(candidatos, key=pontuacao)
    if not melhor.IsActive: melhor.Activate()
    return melhor


def ligar_tubo_conector(doc, c_fixo, c_movel):
    if not c_fixo or not c_movel: return None
    dot = c_fixo.CoordinateSystem.BasisZ.DotProduct(c_movel.CoordinateSystem.BasisZ)
    if dot < -0.85:
        try:
            c_fixo.ConnectTo(c_movel)
        except Exception:
            pass
        return None
    elif abs(dot) < 0.15:
        try:
            return doc.Create.NewElbowFitting(c_fixo, c_movel)
        except:
            try:
                c_fixo.ConnectTo(c_movel)
            except Exception:
                pass
            return None
    else:
        try:
            c_fixo.ConnectTo(c_movel)
        except Exception:
            pass
        return None


def encaixar_tubo_no_conector(doc, tubo, pt_ref, c_dest):
    try:
        conns = _conectores_tubo(tubo)
        if len(conns) < 2:
            raise Exception(u"tubo sem 2 pontas")
        c_ponta = min(conns, key=lambda c: c.Origin.DistanceTo(pt_ref))
        c_outra = max(conns, key=lambda c: c.Origin.DistanceTo(pt_ref))
        p_outra = c_outra.Origin
        alvo = DB.XYZ(c_ponta.Origin.X, c_ponta.Origin.Y, c_dest.Origin.Z)

        ref_outra = None
        try:
            if c_outra.IsConnected:
                for r in list(c_outra.AllRefs):
                    if r.Owner.Id != tubo.Id and r.ConnectorType == DB.ConnectorType.End:
                        ref_outra = r
                        break
        except:
            ref_outra = None

        if c_ponta.Origin.DistanceTo(alvo) > mm(1):
            _desconectar_conector(c_ponta)
            if ref_outra is not None:
                try:
                    c_outra.DisconnectFrom(ref_outra)
                except Exception:
                    pass
            tubo.Location.Curve = DB.Line.CreateBound(p_outra, alvo)
            doc.Regenerate()
            if ref_outra is not None:
                try:
                    c_back = _ponta_proxima(tubo, p_outra)
                    if c_back and not c_back.IsConnected:
                        c_back.ConnectTo(ref_outra)
                except Exception:
                    pass

        c_novo = _ponta_proxima(tubo, alvo)
        if c_novo and not c_novo.IsConnected:
            c_novo.ConnectTo(c_dest)
    except Exception as e:
        try:
            c_fb = connector_near(tubo, pt_ref, mm(60))[0]
            c_fb.ConnectTo(c_dest)
        except:
            AVISOS.append(u"Falha ao ligar tubo ao registro de pressão: {}".format(e))


def _nome_eh_inverter_bucha(nome):
    n = (nome or u"").strip().lower()
    return (u"inverter" in n) and (u"bucha" in n)


def _zerar_param_inverter(elem):
    if not elem: return False
    achou = False
    try:
        params = list(elem.Parameters)
    except:
        return False
    for p in params:
        try:
            if not _nome_eh_inverter_bucha(p.Definition.Name): continue
            achou = True
            if p.IsReadOnly: continue
            if p.StorageType != DB.StorageType.Integer: continue
            if p.AsInteger() != INVERTER_BUCHA:
                p.Set(INVERTER_BUCHA)
        except:
            pass
    return achou


def registrar_bucha(elem):
    try:
        if elem and elem.Id not in BUCHAS_IDS:
            BUCHAS_IDS.append(elem.Id)
    except:
        pass


def forcar_buchas_normais(doc, conectores_pecas):
    doc.Regenerate()
    alvos = []

    for eid in BUCHAS_IDS:
        try:
            el = doc.GetElement(eid)
            if el: alvos.append(el)
        except Exception:
            pass

    for c in conectores_pecas:
        try:
            for ref in c.AllRefs:
                owner = ref.Owner
                if owner is None: continue
                if owner.Category is None: continue
                if owner.Category.Id.IntegerValue != int(DB.BuiltInCategory.OST_PipeFitting): continue
                alvos.append(owner)
        except Exception:
            pass

    vistos = set()
    for el in alvos:
        try:
            if el.Id.IntegerValue in vistos: continue
            vistos.add(el.Id.IntegerValue)
            _zerar_param_inverter(el)
        except Exception:
            pass

    doc.Regenerate()

    for el in alvos:
        try:
            _zerar_param_inverter(el)
        except Exception:
            pass


def aplicar_bucha(doc, elbow):
    if not elbow: return None
    sym_bucha = obter_simbolo_conexao(doc, [u"joelho", u"bucha"])
    if not sym_bucha:
        _zerar_param_inverter(elbow)
        registrar_bucha(elbow)
        return elbow

    bucha_final = elbow
    try:
        eid = elbow.ChangeTypeId(sym_bucha.Id)
        doc.Regenerate()
        bucha_final = doc.GetElement(eid)
    except:
        try:
            elbow.Symbol = sym_bucha
            doc.Regenerate()
        except:
            try:
                pt = elbow.Location.Point
                conexoes_externas = []
                elementos_a_apagar = [elbow.Id]

                for c in iter_connectors(elbow):
                    if c.IsConnected:
                        for ref in c.AllRefs:
                            if ref.Owner.Id == elbow.Id or ref.ConnectorType != DB.ConnectorType.End: continue
                            owner = ref.Owner
                            is_reducer = False

                            if owner.Category.Id.IntegerValue == int(DB.BuiltInCategory.OST_PipeFitting):
                                radii = [conn.Radius for conn in iter_connectors(owner) if conn.ConnectorType == DB.ConnectorType.End]
                                if len(radii) == 2 and abs(radii[0] - radii[1]) > 0.001: is_reducer = True

                            if is_reducer:
                                elementos_a_apagar.append(owner.Id)
                                for cr in iter_connectors(owner):
                                    if cr.IsConnected:
                                        for refr in cr.AllRefs:
                                            if refr.Owner.Id not in [owner.Id, elbow.Id] and refr.ConnectorType == DB.ConnectorType.End:
                                                conexoes_externas.append(refr)
                            else:
                                conexoes_externas.append(ref)

                if len(conexoes_externas) >= 2:
                    for eid in elementos_a_apagar:
                        try:
                            doc.Delete(eid)
                        except Exception:
                            pass
                    doc.Regenerate()

                    nova_bucha = doc.Create.NewFamilyInstance(pt, sym_bucha, DB.Structure.StructuralType.NonStructural)
                    doc.Regenerate()
                    bucha_final = nova_bucha

                    c_bucha = [c for c in iter_connectors(nova_bucha) if c.Domain == DB.Domain.DomainPiping and c.ConnectorType == DB.ConnectorType.End]
                    for c_b in c_bucha:
                        melhor_ext = None
                        menor_diff = 999
                        for c_ext in conexoes_externas:
                            diff = abs(c_ext.Radius - c_b.Radius)
                            if diff < menor_diff:
                                menor_diff = diff
                                melhor_ext = c_ext
                        if melhor_ext and menor_diff < mm(3):
                            try:
                                melhor_ext.ConnectTo(c_b)
                            except Exception:
                                pass
            except Exception:
                pass

    if bucha_final:
        doc.Regenerate()
        _zerar_param_inverter(bucha_final)
        registrar_bucha(bucha_final)
    return bucha_final


def forcar_vertical(doc, inst):
    if not inst: return
    c_all = end_piping(inst, only_open=True)
    if not c_all: return
    dir_c = c_all[0].CoordinateSystem.BasisZ
    if abs(dir_c.Z) > 0.9: return
    eixo = dir_c.CrossProduct(DB.XYZ.BasisZ)
    if eixo.GetLength() < 1e-5: eixo = DB.XYZ.BasisX
    linha_eixo = DB.Line.CreateBound(inst.Location.Point, inst.Location.Point + eixo.Normalize())
    try:
        DB.ElementTransformUtils.RotateElement(doc, inst.Id, linha_eixo, math.pi / 2.0)
    except Exception:
        pass


def alinhar_eixo_registro(tubo_base, ponta_ref, z_valv, comp_sup):
    try:
        conns = [c for c in iter_connectors(tubo_base)
                 if c.Domain == DB.Domain.DomainPiping and c.ConnectorType == DB.ConnectorType.End]
        topo = min(conns, key=lambda c: c.Origin.DistanceTo(ponta_ref))
        base = max(conns, key=lambda c: c.Origin.DistanceTo(ponta_ref))
        d = topo.Origin - base.Origin
        if d.GetLength() < 1e-6: raise Exception("tubo sem comprimento")
        d = d.Normalize()
        if d.Z < 0.9: raise Exception("tubo base nao e vertical")
        p0 = topo.Origin
        t = (z_valv - p0.Z) / d.Z
        pv = p0 + d.Multiply(t)
        pt = pv + d.Multiply(comp_sup / d.Z)
        return p0, pv, pt
    except Exception as e:
        AVISOS.append(u"Alinhamento do eixo do registro não aplicado ({}); usando vertical perfeito.".format(e))
        pv = DB.XYZ(ponta_ref.X, ponta_ref.Y, z_valv)
        pt = DB.XYZ(ponta_ref.X, ponta_ref.Y, z_valv + comp_sup)
        return ponta_ref, pv, pt


def criar_te_registro(doc, v_baixo, v_cima, tubo_h, p, permitir_fallback=True):
    c_h = connector_near(tubo_h, p, mm(25))[0]
    c_baixo = connector_near(v_baixo, p, mm(25))[0]
    c_cima = connector_near(v_cima, p, mm(25))[0]

    ultimo_erro = None

    def _ang(a, b):
        try:
            d = max(-1.0, min(1.0, a.CoordinateSystem.BasisZ.DotProduct(b.CoordinateSystem.BasisZ)))
            return math.degrees(math.acos(d))
        except: return -1.0
    try:
        comp_baixo = v_baixo.Location.Curve.Length * 304.8
    except: comp_baixo = -1.0
    try:
        comp_h = tubo_h.Location.Curve.Length * 304.8
    except: comp_h = -1.0
    try:
        diag = u"ang(cima,baixo)={:.1f} | ang(h,baixo)={:.1f} | ang(h,cima)={:.1f} | dist(cima,baixo)={:.1f}mm | dist(h,baixo)={:.1f}mm | compr.vertical baixo={:.0f}mm | compr.horizontal={:.0f}mm".format(
            _ang(c_cima, c_baixo), _ang(c_h, c_baixo), _ang(c_h, c_cima),
            c_cima.Origin.DistanceTo(c_baixo.Origin) * 304.8,
            c_h.Origin.DistanceTo(c_baixo.Origin) * 304.8,
            comp_baixo, comp_h)
    except Exception as ed:
        diag = u"(diagnóstico indisponível: {})".format(ed)

    for a, b in [(c_cima, c_baixo), (c_baixo, c_cima)]:
        try:
            return doc.Create.NewTeeFitting(a, b, c_h)
        except Exception as e:
            ultimo_erro = e

    if not permitir_fallback:
        AVISOS.append(u"Não foi possível criar o Tê da prumada do canto. Erro: {} | DIAGNÓSTICO: {}".format(ultimo_erro, diag))
        return None

    try:
        c_far_cima = None
        for c in iter_connectors(v_cima):
            if c.Domain == DB.Domain.DomainPiping and c.ConnectorType == DB.ConnectorType.End:
                if c.Origin.DistanceTo(p) > mm(25):
                    c_far_cima = c
                    break

        c_far_baixo = None
        for c in iter_connectors(v_baixo):
            if c.Domain == DB.Domain.DomainPiping and c.ConnectorType == DB.ConnectorType.End:
                if c.Origin.DistanceTo(p) > mm(25):
                    c_far_baixo = c
                    break

        if c_far_cima is None or c_far_baixo is None:
            raise Exception(u"Conectores das pontas dos tubos verticais não encontrados.")

        pt_fim = c_far_cima.Origin
        pt_ini = c_far_baixo.Origin

        refs_externas = []
        for r in c_far_cima.AllRefs:
            if r.Owner.Id != v_cima.Id and r.ConnectorType == DB.ConnectorType.End:
                refs_externas.append(r)

        for r in refs_externas:
            try:
                c_far_cima.DisconnectFrom(r)
            except Exception:
                pass

        v_baixo.Location.Curve = DB.Line.CreateBound(pt_ini, pt_fim)
        doc.Delete(v_cima.Id)
        doc.Regenerate()

        REG_INF[0] = v_baixo
        c_topo_novo = connector_near(v_baixo, pt_fim, mm(25))[0]
        for r in refs_externas:
            try:
                c_topo_novo.ConnectTo(r)
            except Exception:
                pass

        c_h = connector_near(tubo_h, p, mm(25))[0]
        return doc.Create.NewTakeoffFitting(c_h, v_baixo)
    except Exception as e2:
        AVISOS.append(u"Não foi possível criar o Tê do registro geral. Erro 1: {} | Erro 2: {} | DIAGNÓSTICO: {}".format(ultimo_erro, e2, diag))
        return None


def _conector_atual(doc, c):
    try:
        el = doc.GetElement(c.Owner.Id)
        return connector_by_id(el, c.Id)
    except:
        return c


def _conector_do_elemento(el, conn_id):
    if el is None or conn_id is None:
        return None
    try:
        for c in iter_connectors(el):
            if c.Id == conn_id:
                return c
    except:
        pass
    return None


def _conector_da_peca(doc, info):
    try:
        el = doc.GetElement(info.get('owner_id'))
        c = _conector_do_elemento(el, info.get('conn_id'))
        if c is not None:
            return c
    except:
        pass
    try:
        return _conector_atual(doc, info.get('c'))
    except:
        return None


def _origem_peca(info):
    try:
        return info['c'].Origin
    except:
        pass
    p = info.get('origem_xyz')
    if p:
        return DB.XYZ(p[0], p[1], p[2])
    ax = info.get('axis')
    if ax:
        return ax
    return DB.XYZ.Zero


def _conectado_a(c, owner_id):
    try:
        for r in c.AllRefs:
            if r.Owner.Id == owner_id:
                return True
    except:
        pass
    return False


def _desconectar_conector(c):
    try:
        refs = list(c.AllRefs)
    except:
        return
    for r in refs:
        try:
            if r.ConnectorType != DB.ConnectorType.End and r.ConnectorType != DB.ConnectorType.Curve:
                continue
            if r.Owner.Id == c.Owner.Id:
                continue
            c.DisconnectFrom(r)
        except:
            pass


def _desconectar_elemento(el):
    if el is None:
        return
    try:
        for c in list(iter_connectors(el)):
            _desconectar_conector(c)
    except:
        pass


def _conectores_tubo(el):
    saida = []
    try:
        for c in iter_connectors(el):
            if c.Domain == DB.Domain.DomainPiping and c.ConnectorType == DB.ConnectorType.End:
                saida.append(c)
    except:
        pass
    return saida


def _ponta_proxima(el, pt, tol=None):
    conns = _conectores_tubo(el)
    if not conns:
        return None
    return min(conns, key=lambda c: c.Origin.DistanceTo(pt))


def _parece_lado_bucha(c):
    try:
        n = (c.Description or u"").lower()
    except:
        return False
    return (u"bucha" in n) or (u"latão" in n) or (u"latao" in n) or (u"rosca" in n)


def _horizontal(v):
    h = DB.XYZ(v.X, v.Y, 0)
    if h.GetLength() < 1e-6:
        return None
    return h.Normalize()


def _girar_elemento(doc, elem, origem, direcao_atual, direcao_alvo):
    if direcao_atual.GetLength() < 1e-9 or direcao_alvo.GetLength() < 1e-9:
        return
    a = direcao_atual.Normalize()
    b = direcao_alvo.Normalize()
    dot = max(-1.0, min(1.0, a.DotProduct(b)))
    if dot > 0.9999:
        return
    if dot < -0.9999:
        axis = a.CrossProduct(DB.XYZ.BasisZ)
        if axis.GetLength() < 1e-6:
            axis = a.CrossProduct(DB.XYZ.BasisX)
        ang = math.pi
    else:
        axis = a.CrossProduct(b)
        ang = math.acos(dot)
    axis = axis.Normalize()
    linha = DB.Line.CreateBound(origem, origem + axis)
    DB.ElementTransformUtils.RotateElement(doc, elem.Id, linha, ang)


def _angulo_rolagem(eixo, atual, desejado):
    if eixo.GetLength() < 1e-9:
        return 0.0
    eixo = eixo.Normalize()
    a = atual - eixo.Multiply(atual.DotProduct(eixo))
    d = desejado - eixo.Multiply(desejado.DotProduct(eixo))
    if a.GetLength() < 1e-8 or d.GetLength() < 1e-8:
        return 0.0
    a = a.Normalize()
    d = d.Normalize()
    return math.atan2(a.CrossProduct(d).DotProduct(eixo), max(-1.0, min(1.0, a.DotProduct(d))))


def obter_joelho_bucha(doc, dia_ft):
    simbolos = DB.FilteredElementCollector(doc).OfClass(DB.FamilySymbol).OfCategory(DB.BuiltInCategory.OST_PipeFitting)
    dia_mm = (dia_ft or 0) * 304.8
    candidatos = []
    tem_45 = False
    for sym in simbolos:
        nome = ((sym.FamilyName or u"") + u" " + (type_name(sym) or u"")).lower()
        if u"joelho" not in nome or u"bucha" not in nome:
            continue
        if u"45" in nome:
            tem_45 = True
            continue
        candidatos.append(sym)
    if not candidatos:
        if tem_45:
            AVISOS.append(u"Só existe joelho com bucha de 45° no projeto. Carregue o joelho 90° com bucha — o de 45° não foi usado.")
        return None

    def nota(sym):
        nome = ((sym.FamilyName or u"") + u" " + (type_name(sym) or u"")).lower()
        d = _mm_do_nome(type_name(sym))
        if d is None:
            d = _mm_do_nome(sym.FamilyName)
        diff = abs(d - dia_mm) if d is not None else 999.0
        pref90 = 0 if u"90" in nome else 1
        return (pref90, diff)

    melhor = min(candidatos, key=nota)
    if not melhor.IsActive:
        melhor.Activate()
    return melhor


def trocar_para_bucha(doc, elbow, dia=None):
    sym_bucha = obter_joelho_bucha(doc, dia) if dia else obter_joelho_bucha(doc, 0)
    if not sym_bucha:
        AVISOS.append(u"Joelho 90° com bucha não encontrado; mantido o joelho comum.")
        return elbow
    bucha = elbow
    try:
        eid = elbow.ChangeTypeId(sym_bucha.Id)
        doc.Regenerate()
        el = doc.GetElement(eid)
        if el is not None:
            bucha = el
    except Exception as e1:
        try:
            elbow.Symbol = sym_bucha
            doc.Regenerate()
        except Exception as e2:
            AVISOS.append(u"Não foi possível trocar o joelho pelo joelho com bucha: {} | {}".format(e1, e2))
            return elbow
    _zerar_param_inverter(bucha)
    registrar_bucha(bucha)
    return bucha


def _id_lado_peca(bucha, owner_id, origem):
    conns = _conectores_tubo(bucha)
    if not conns:
        return None
    marcados = [c for c in conns if _parece_lado_bucha(c)]
    if len(marcados) == 1:
        return marcados[0].Id
    if owner_id is not None:
        for c in conns:
            if _conectado_a(c, owner_id):
                return c.Id
    return min(conns, key=lambda c: c.Origin.DistanceTo(origem)).Id


def _direcao_saida(conector):
    bz = conector.CoordinateSystem.BasisZ
    if bz.GetLength() < 1e-9:
        return DB.XYZ(1, 0, 0)
    d = bz.Normalize()
    h = _horizontal(d)
    if h is not None and abs(d.Z) <= 0.20:
        return h
    return d


def _conector_contra(el, direcao):
    conns = _conectores_tubo(el)
    if not conns:
        return None
    alvo = direcao.Negate()
    return max(conns, key=lambda c: c.CoordinateSystem.BasisZ.DotProduct(alvo))

def _definir_fluxo_tubo(tubo, pt_inicio, pt_fim):
    """
    Força a direção do fluxo de um tubo baseado nos pontos.
    Assume que o fluxo vai de pt_inicio para pt_fim.
    """
    if not tubo: return
    try:
        conns = _conectores_tubo(tubo)
        if len(conns) < 2: return
        
        c_ini = min(conns, key=lambda c: c.Origin.DistanceTo(pt_inicio))
        c_fim = max(conns, key=lambda c: c.Origin.DistanceTo(pt_inicio))
        
        pass
    except Exception:
        pass

def preparar_ponto_consumo(doc, info, dir_v, sistema_id, tipo_id, nivel_id, dia):
    c = _conector_da_peca(doc, info)
    if c is None:
        AVISOS.append(u"Ponto de consumo sem conector: {}".format(info['elem'].Name))
        info['pipe'] = None
        info['tubinho'] = None
        info['bucha'] = None
        info['base'] = _origem_peca(info)
        info['axis'] = DB.XYZ(info['base'].X, info['base'].Y, 0)
        return

    O = c.Origin
    d = _direcao_saida(c)
    L = COMPRIMENTO_TUBINHO
    up = DB.XYZ(0, 0, 1.0 if dir_v >= 0 else -1.0)

    # 1. Tubos provisórios
    p_canto = O + d.Multiply(L + mm(150))
    tubo_h_temp = make_pipe(sistema_id, tipo_id, nivel_id, O, p_canto, dia)
    p_v0 = p_canto + up.Multiply(mm(15))
    tubo_v_temp = make_pipe(sistema_id, tipo_id, nivel_id, p_v0, p_v0 + up.Multiply(ALTURA_STUB), dia)
    doc.Regenerate()

    elbow = None
    try:
        c_h = _ponta_proxima(tubo_h_temp, p_canto)
        c_v = _ponta_proxima(tubo_v_temp, p_canto)
        elbow = doc.Create.NewElbowFitting(c_h, c_v)
        doc.Regenerate()
    except Exception as e_elbow:
        AVISOS.append(u"Não criei o joelho 90° de {}: {}".format(info['elem'].Name, e_elbow))

    bucha = elbow
    if elbow is not None:
        bucha = trocar_para_bucha(doc, elbow, dia)
        doc.Regenerate()
        _desconectar_elemento(bucha)
        doc.Regenerate()

        c_lado = _conector_contra(bucha, d)
        if c_lado is not None:
            alvo = O + d.Multiply(L)
            delta = alvo - c_lado.Origin
            if delta.GetLength() > mm(0.2):
                try:
                    DB.ElementTransformUtils.MoveElement(doc, bucha.Id, delta)
                    doc.Regenerate()
                except Exception as e_mv:
                    AVISOS.append(u"Não consegui encostar o joelho no tubinho de {}: {}".format(info['elem'].Name, e_mv))

    c_lado = _conector_contra(bucha, d) if bucha is not None else None
    p_fim = c_lado.Origin if c_lado is not None else O + d.Multiply(L)

    try:
        doc.Delete(tubo_h_temp.Id)
        doc.Delete(tubo_v_temp.Id)
    except:
        pass
    doc.Regenerate()

    tubo_h = make_pipe(sistema_id, tipo_id, nivel_id, p_fim, O, dia)
    doc.Regenerate()
    c_peca = _conector_da_peca(doc, info)
    c_h0 = _ponta_proxima(tubo_h, O)
    
    if c_peca is not None and c_h0 is not None:
        try:
             
            c_h0.ConnectTo(c_peca) 
        except Exception:
            pass

    id_vert = None
    p_base = p_fim
    if bucha is not None and c_lado is not None:
        c_lado_bucha = _conector_do_elemento(bucha, c_lado.Id)
        c_h1 = _ponta_proxima(tubo_h, p_fim)
        if c_lado_bucha is not None and c_h1 is not None:
            try:
                c_h1.ConnectTo(c_lado_bucha)
            except Exception:
                pass

        conns = _conectores_tubo(bucha)
        outras = [cn for cn in conns if c_lado is None or cn.Id != c_lado.Id]
        c_vert = outras[0] if outras else None
        if c_vert is not None:
            id_vert = c_vert.Id
            p_base = c_vert.Origin

    if bucha is not None:
        _zerar_param_inverter(bucha)

    info['pipe'] = None 
    info['tubinho'] = tubo_h
    info['bucha'] = bucha
    info['base'] = p_base
    info['axis'] = DB.XYZ(p_base.X, p_base.Y, 0)
    info['dir_v'] = dir_v
    info['id_lado_tubo'] = id_vert
    info['id_lado_peca'] = c_lado.Id if c_lado is not None else None


def criar_vertical(doc, info, z_topo, sistema_id, tipo_id, nivel_id, dia):
    bucha = info.get('bucha')
    p_base = info.get('base')
    id_lado_tubo = info.get('id_lado_tubo')

    if p_base is None:
        raise Exception(u"sem base")
    if abs(z_topo - p_base.Z) < mm(20):
        raise Exception(u"trecho vertical curto")

    if bucha is not None and id_lado_tubo is not None:
        c_side = _conector_do_elemento(bucha, id_lado_tubo)
        if c_side is not None:
            p_base = c_side.Origin

    pt_topo = DB.XYZ(p_base.X, p_base.Y, z_topo)

    tubo = make_pipe(sistema_id, tipo_id, nivel_id, pt_topo, p_base, dia)
    doc.Regenerate()
    if bucha is not None and id_lado_tubo is not None:
        c_side = _conector_do_elemento(bucha, id_lado_tubo)
        c_p = _ponta_proxima(tubo, p_base)
        try:
            if c_side is not None and c_p is not None:
                c_p.ConnectTo(c_side)
        except Exception as e:
            AVISOS.append(u"Tubo não conectou à bucha: {}".format(e))

    info['pipe'] = tubo
    info['base'] = p_base
    return p_base


def garantir_pontos_consumo(doc, infos):
    for info in infos:
        c_peca = _conector_da_peca(doc, info)
        tubinho = info.get('tubinho')
        if c_peca is not None and tubinho is not None:
            try:
                ligado = False
                if c_peca.IsConnected:
                    ligado = _conectado_a(c_peca, tubinho.Id)
                if not ligado:
                    c_h = _ponta_proxima(tubinho, c_peca.Origin)
                    if c_h is not None and c_h.Origin.DistanceTo(c_peca.Origin) < mm(8) and not c_h.IsConnected:
                        c_h.ConnectTo(c_peca)
                    elif c_h is None or c_h.Origin.DistanceTo(c_peca.Origin) >= mm(8):
                        AVISOS.append(u"Tubinho afastado da pecinha {}.".format(info['elem'].Name))
            except Exception as e:
                AVISOS.append(u"Não religou o tubinho de {}: {}".format(info['elem'].Name, e))

        bucha = info.get('bucha')
        tubo = info.get('pipe')
        if bucha is None or tubo is None:
            continue
        c_t = _conector_do_elemento(bucha, info.get('id_lado_tubo'))
        if c_t is None:
            continue
        try:
            if c_t.IsConnected:
                continue
            c_p = _ponta_proxima(tubo, c_t.Origin)
            if c_p is not None and (not c_p.IsConnected) and c_p.Origin.DistanceTo(c_t.Origin) < mm(8):
                c_p.ConnectTo(c_t)
        except:
            pass


def girar(v, graus):
    r = math.radians(graus)
    return DB.XYZ(v.X * math.cos(r) - v.Y * math.sin(r),
                  v.X * math.sin(r) + v.Y * math.cos(r), 0).Normalize()


def chave_angulo(e_i, e0):
    a0 = math.degrees(math.atan2(e0.Y, e0.X))
    ai = math.degrees(math.atan2(e_i.Y, e_i.X))
    d = (ai - a0 + 90.0) % 180.0 - 90.0
    if abs(d) <= TOL_ANGULO:
        return 0, d
    snapped = int(round(abs(d) / PASSO_ANGULO)) * int(PASSO_ANGULO)
    if snapped < PASSO_ANGULO: snapped = int(PASSO_ANGULO)
    if snapped >= 90: return 90, d
    return (snapped if d > 0 else -snapped), d


def alinhar_grupo(doc, origem_ref, eixo, pecas, click_pts):
    for elem in pecas:
        c_atual = conector_agua(elem, click_pts.get(elem.Id.IntegerValue))
        if not c_atual: continue
        pt_atual = c_atual.Origin
        vec = pt_atual - origem_ref
        dist_proj = vec.X * eixo.X + vec.Y * eixo.Y
        pt_alvo = DB.XYZ(origem_ref.X + eixo.X * dist_proj, origem_ref.Y + eixo.Y * dist_proj, pt_atual.Z)
        move_vec = DB.XYZ(pt_alvo.X - pt_atual.X, pt_alvo.Y - pt_atual.Y, 0)
        if move_vec.GetLength() > mm(1):
            DB.ElementTransformUtils.MoveElement(doc, elem.Id, move_vec)


def intersecao_2d(P, d, Q, e):
    cruz = d.X * e.Y - d.Y * e.X
    if abs(cruz) < 1e-6: return None
    t = ((Q.X - P.X) * e.Y - (Q.Y - P.Y) * e.X) / cruz
    return DB.XYZ(P.X + t * d.X, P.Y + t * d.Y, 0)


def z_com_desnivel(z, z_conectores):
    zz = z + DESNIVEL_CANTO
    for _ in range(10):
        if any(abs(cz - zz) < 25 / 304.8 for cz in z_conectores): zz += DESNIVEL_CANTO
        else: break
    return zz


def novo_no_peca(info, z):
    o = _origem_peca(info)
    ax = info['axis']
    return {'c': info.get('c'), 'info': info, 'chuv': info['chuv'],
            'p': DB.XYZ(ax.X, ax.Y, 0), 'z': z, 'origem_c': o}


def estender_cadeia(no_fim, dir_out, restantes, z_atual, z_conectores, sentido):
    cadeia = []
    atual = no_fim
    while restantes:
        P = atual['p']
        melhor = None
        for key, g in list(restantes.items()):
            C = intersecao_2d(P, dir_out, g['anchor_pt'], g['e'])
            if C is None: continue
            t = (C.X - P.X) * dir_out.X + (C.Y - P.Y) * dir_out.Y
            if t < -TOL_COINC: continue
            if melhor is None or t < melhor[0]:
                melhor = (t, key, C)
        if melhor is None: break

        t, key, C = melhor
        g = restantes.pop(key)
        e = g['e']

        itens = []
        for info in g['infos']:
            ax = info['axis']
            itens.append(((ax.X - C.X) * e.X + (ax.Y - C.Y) * e.Y, info))
        s_far = max(itens, key=lambda x: abs(x[0]))[0]
        sgn = 1.0 if s_far >= 0 else -1.0
        arm_dir = DB.XYZ(e.X * sgn, e.Y * sgn, 0)

        validos = []
        for s, info in itens:
            if s * sgn < -TOL_COINC:
                AVISOS.append(u"Uma peça da parede de {}° ficou antes do canto e não foi ligada.".format(key))
            else:
                validos.append((s * sgn, info))
        if not validos: continue
        validos.sort(key=lambda x: x[0])

        coin_end = math.hypot(C.X - P.X, C.Y - P.Y) < TOL_COINC
        coin_first = validos[0][0] < TOL_COINC

        z_in = z_atual
        novos = []

        if coin_end:
            if atual.get('chuv') or atual.get('canto_fix') or atual.get('c') is None:
                AVISOS.append(u"Canto da parede de {}° coincide com uma peça que não permite derivação; grupo ignorado.".format(key))
                continue
            atual['canto_fix'] = True
            atual['out_dir'] = sentido
            atual['z_out'] = z_com_desnivel(z_in, z_conectores)
            z_atual = atual['z_out']
        elif not coin_first:
            novos.append({'c': None, 'canto': True, 'p': DB.XYZ(C.X, C.Y, 0), 'z': z_in})

        for idx, (s, info) in enumerate(validos):
            no = novo_no_peca(info, z_atual)
            if idx == 0 and coin_first and not coin_end:
                if info['chuv']:
                    AVISOS.append(u"Chuveiro no canto da parede de {}°: curva sem derivação para a peça.".format(key))
                else:
                    no['canto_fix'] = True
                    no['out_dir'] = sentido
                    no['z'] = z_atual
                    no['z_out'] = z_com_desnivel(z_atual, z_conectores)
                    z_atual = no['z_out']
            novos.append(no)

        cadeia.extend(novos)
        atual = novos[-1]
        dir_out = arm_dir
        INFO.append(u"Curva de {}° criada (parede a {}° da peça base).".format(abs(key), key))

    if cadeia and cadeia[-1].get('canto_fix'):
        cadeia[-1]['canto_fix'] = False
        cadeia[-1]['z_out'] = cadeia[-1]['z']
    return cadeia


def ponto_perna(no, idx, idx_viz):
    if no.get('canto_fix') and idx_viz == idx + no['out_dir']:
        return no['pt_out']
    return no['pt']


def executar(doc, uidoc):
    try:
        del BUCHAS_IDS[:]
        del AVISOS[:]
        del INFO[:]
        REG_INF[0] = None

        ref_ref = uidoc.Selection.PickObject(UI.Selection.ObjectType.Element, FiltroPecas(), u"1. Clique na peça base (CLIQUE PERTO da conexão desejada)")
        ref_elem = doc.GetElement(ref_ref)
        click_pts = {ref_elem.Id.IntegerValue: ref_ref.GlobalPoint}

        outras_refs = uidoc.Selection.PickObjects(UI.Selection.ObjectType.Element, FiltroPecas(), u"2. Selecione as peças e o registo geral (CLIQUE PERTO das conexões)")
        outras_pecas = []
        for r in outras_refs:
            el = doc.GetElement(r)
            outras_pecas.append(el)
            if el.Id.IntegerValue not in click_pts:
                try:
                    click_pts[el.Id.IntegerValue] = r.GlobalPoint
                except Exception:
                    pass

        ids_vistos = set()
        pecas_unicas = []
        for p in [ref_elem] + outras_pecas:
            if p.Id not in ids_vistos and p.Category.Id.IntegerValue == int(DB.BuiltInCategory.OST_PlumbingFixtures):
                ids_vistos.add(p.Id)
                pecas_unicas.append(p)
        todas_pecas = pecas_unicas

        registros = [p for p in outras_pecas if p.Category.Id.IntegerValue == int(DB.BuiltInCategory.OST_PipeAccessory)]
        if len(registros) > 1:
            forms.alert(u"Selecione no máximo 1 registo geral.")
            return

        registro_existente = registros[0] if registros else None

        lado_reg = u"Esquerda"
        if not registro_existente:
            lado_reg = forms.CommandSwitchWindow.show([u"Esquerda", u"Direita"], message=u"Onde deseja criar o registo geral?")
            if not lado_reg: return

        tipo_tubo_id = escolher_tipo_agua(doc)
        if not tipo_tubo_id: return

        sistema_id = obter_sistema_agua()
        nivel_id = nivel_de(ref_elem, doc)
        nivel_z = doc.GetElement(nivel_id).Elevation

        c_ref0 = conector_agua(ref_elem, click_pts.get(ref_elem.Id.IntegerValue))
        if not c_ref0: raise Exception(u"A peça base não possui conector de água livre.")

        n0, e0 = obter_vetores_parede(c_ref0)

        chaves = {}
        grupos_elem = {}
        for p in todas_pecas:
            c_p = conector_agua(p, click_pts.get(p.Id.IntegerValue))
            e_p = eixo_peca_conector(c_p, e0)
            chave, _d = chave_angulo(e_p, e0)
            chaves[p.Id.IntegerValue] = chave
            grupos_elem.setdefault(chave, []).append(p)

        with Grupo(u"Ligação Água Fria"):
            with revit.Transaction(u"Alinhar e Modelar Água"):

                origem_ref = c_ref0.Origin
                alinhar_grupo(doc, origem_ref, e0, [p for p in grupos_elem.get(0, []) if p.Id != ref_elem.Id], click_pts)
                grupos_ancora = {}
                for key, elems in grupos_elem.items():
                    if key == 0: continue
                    eg = girar(e0, key)
                    cand = []
                    for el in elems:
                        cc = conector_agua(el, click_pts.get(el.Id.IntegerValue))
                        if cc:
                            vv = cc.Origin - origem_ref
                            cand.append((abs(vv.X * n0.X + vv.Y * n0.Y), el, cc))
                    if not cand: continue
                    cand.sort(key=lambda t: t[0])
                    alinhar_grupo(doc, cand[0][2].Origin, eg, [t[1] for t in cand[1:]], click_pts)
                    grupos_ancora[key] = cand[0][1]
                doc.Regenerate()

                infos = []
                max_z_normal = 0.0
                dia_principal = 0.0
                for peca in todas_pecas:
                    el = doc.GetElement(peca.Id)
                    c = conector_agua(el, click_pts.get(el.Id.IntegerValue))
                    if not c: continue
                    z_rel = c.Origin.Z - nivel_z
                    dia = c.Radius * 2.0
                    if dia > dia_principal: dia_principal = dia
                    is_ch = z_rel > mm(1800)
                    if not is_ch and z_rel > max_z_normal: max_z_normal = z_rel

                    key = chaves[peca.Id.IntegerValue]
                    infos.append({
                        'elem': el, 'c': c, 'key': key, 'chuv': is_ch,
                        'owner_id': el.Id, 'conn_id': c.Id,
                        'origem_xyz': (c.Origin.X, c.Origin.Y, c.Origin.Z),
                    })

                if not infos: raise Exception(u"Nenhuma peça possui conector de água livre.")
                todas_conexoes = [i['c'] for i in infos]
                z_conectores = [c.Origin.Z for c in todas_conexoes]

                z_ramal_relativo = ALTURA_BAIXA if max_z_normal <= mm(1000) else ALTURA_ALTA
                z_ramal_abs = nivel_z + z_ramal_relativo
                z_pressao_abs = nivel_z + (mm(1200) if z_ramal_relativo == ALTURA_BAIXA else mm(1600))

                for cz in z_conectores:
                    if abs(cz - z_ramal_abs) < mm(25):
                        z_ramal_abs += mm(100)
                        break
                if z_pressao_abs - z_ramal_abs < mm(200):
                    z_pressao_abs = z_ramal_abs + mm(200)

                for i in infos:
                    O_i = i['c'].Origin
                    if i['chuv']: dir_v = -1
                    else: dir_v = 1 if z_ramal_abs > O_i.Z else -1
                    preparar_ponto_consumo(doc, i, dir_v, sistema_id, tipo_tubo_id, nivel_id, dia_principal)
                doc.Regenerate()

                principais = [i for i in infos if i['key'] == 0]
                principais.sort(key=lambda i: (_origem_peca(i) - ref_elem.Location.Point).DotProduct(e0))
                main = [novo_no_peca(i, z_ramal_abs) for i in principais]

                restantes = {}
                for key in set([i['key'] for i in infos if i['key'] != 0]):
                    membros = [i for i in infos if i['key'] == key]
                    eg = girar(e0, key)
                    membros_ord = sorted(membros, key=lambda i: abs((i['axis'] - origem_ref).X * n0.X + (i['axis'] - origem_ref).Y * n0.Y))
                    anc = membros_ord[0]['axis']
                    restantes[key] = {'infos': membros, 'e': eg, 'anchor_pt': DB.XYZ(anc.X, anc.Y, 0)}
                    INFO.append(u"Parede a {}° detectada: {} peça(s).".format(key, len(membros)))

                dir_dir = e0
                dir_esq = DB.XYZ(-e0.X, -e0.Y, 0)
                cad_dir = estender_cadeia(main[-1], dir_dir, restantes, z_ramal_abs, z_conectores, +1)
                cad_esq = estender_cadeia(main[0], dir_esq, restantes, z_ramal_abs, z_conectores, -1)
                for key in restantes.keys():
                    AVISOS.append(u"A parede de {}° não encaixou no ramal (o canto cairia no meio das peças); suas peças não foram ligadas.".format(key))

                path = list(reversed(cad_esq)) + main + cad_dir

                for no in path:
                    if no.get('c') is None:
                        no['pt'] = DB.XYZ(no['p'].X, no['p'].Y, no['z'])
                        continue

                    info = no['info']
                    base = info['base']
                    pt_topo = DB.XYZ(base.X, base.Y, no['z'])
                    no['pt'] = pt_topo

                    if abs(no['z'] - base.Z) < mm(60):
                        AVISOS.append(u"Trecho vertical muito curto em uma peça ({}): ramal quase na altura do ponto.".format(info['elem'].Name))

                    if no['chuv']:
                        z_press_n = max(z_pressao_abs, no['z'] + mm(200))
                        pt_valvula = DB.XYZ(base.X, base.Y, z_press_n)
                        tubo_v1 = make_pipe(sistema_id, tipo_tubo_id, nivel_id, pt_topo, pt_valvula, dia_principal)
                        doc.Regenerate()
                        
                        tubo_v2 = make_pipe(sistema_id, tipo_tubo_id, nivel_id, pt_valvula, base, dia_principal)
                        info['pipe'] = tubo_v2
                        doc.Regenerate()
                        
                        bucha = info.get('bucha')
                        id_lado_tubo = info.get('id_lado_tubo')
                        if bucha and id_lado_tubo:
                            c_side = _conector_do_elemento(bucha, id_lado_tubo)
                            c_p2 = _ponta_proxima(tubo_v2, base)
                            if c_side and c_p2:
                                try:
                                    c_p2.ConnectTo(c_side)
                                except Exception:
                                    pass

                        sym_rp = obter_simbolo_por_diametro(doc, FAMILIA_REGISTRO_PRESSAO, dia_principal, [u"acabamento"])
                        if sym_rp:
                            rp_inst = doc.Create.NewFamilyInstance(pt_valvula, sym_rp, doc.GetElement(nivel_id), DB.Structure.StructuralType.NonStructural)
                            doc.Regenerate()
                            forcar_vertical(doc, rp_inst)
                            doc.Regenerate()

                            c_rp = end_piping(rp_inst, only_open=True)
                            if len(c_rp) >= 2:
                                c_rp = sorted(c_rp, key=lambda x: x.Origin.Z)
                                encaixar_tubo_no_conector(doc, tubo_v1, pt_valvula, c_rp[0])
                                encaixar_tubo_no_conector(doc, tubo_v2, pt_valvula, c_rp[-1])
                            else:
                                AVISOS.append(u"Registro de pressão sem 2 conectores livres.")
                        else:
                            AVISOS.append(u"Família do registro de pressão não encontrada.")
                        no['vert'] = tubo_v1
                    else:
                        try:
                            base = criar_vertical(doc, info, no['z'], sistema_id, tipo_tubo_id, nivel_id, dia_principal)
                            doc.Regenerate()
                            no['pt'] = DB.XYZ(base.X, base.Y, no['z'])
                        except Exception as e_v:
                            AVISOS.append(u"Não foi possível criar o vertical de uma peça: {}".format(e_v))
                        
                        tubo_pre = info['pipe']
                        no['vert'] = tubo_pre

                        if no.get('canto_fix') and tubo_pre:
                            p0, pv, _x = alinhar_eixo_registro(tubo_pre, pt_topo, no['z_out'], mm(100))
                            no['vert_up'] = make_pipe(sistema_id, tipo_tubo_id, nivel_id, p0, pv, dia_principal)
                            no['pt_out'] = pv

                    if no.get('canto_fix') and no.get('vert_up') is None:
                        no['canto_fix'] = False

                if registro_existente:
                    try:
                        loc = registro_existente.Location.Point
                        d_ini = math.hypot(loc.X - path[0]['pt'].X, loc.Y - path[0]['pt'].Y)
                        d_fim = math.hypot(loc.X - path[-1]['pt'].X, loc.Y - path[-1]['pt'].Y)
                        lado_reg = u"Esquerda" if d_ini <= d_fim else u"Direita"
                    except Exception:
                        pass

                esq = (lado_reg == u"Esquerda")
                idx_reg = 0 if esq else len(path) - 1
                fim = path[idx_reg]
                ponta_ref = fim['pt']
                z_valv_reg = nivel_z + ALTURA_REGISTRO_GERAL

                extensao = bool(fim.get('chuv'))
                if extensao:
                    if len(path) > 1:
                        viz = path[idx_reg + 1] if esq else path[idx_reg - 1]
                        vx = fim['pt'].X - viz['pt'].X
                        vy = fim['pt'].Y - viz['pt'].Y
                        comp = math.hypot(vx, vy)
                        if comp > 1e-6:
                            ox, oy = vx / comp, vy / comp
                        else:
                            ox, oy = (-e0.X, -e0.Y) if esq else (e0.X, e0.Y)
                    else:
                        ox, oy = (-e0.X, -e0.Y) if esq else (e0.X, e0.Y)
                    ponta_reg = DB.XYZ(ponta_ref.X + ox * DESLOC_CHUVEIRO, ponta_ref.Y + oy * DESLOC_CHUVEIRO, ponta_ref.Z)
                    p_ini_reg = ponta_reg
                    pt_valv_reg = DB.XYZ(ponta_reg.X, ponta_reg.Y, z_valv_reg)
                    pt_top_reg = DB.XYZ(ponta_reg.X, ponta_reg.Y, z_valv_reg + mm(200))
                else:
                    p_ini_reg, pt_valv_reg, pt_top_reg = alinhar_eixo_registro(
                        fim['vert'], ponta_ref, z_valv_reg, mm(200))

                tubo_v_reg_sup = make_pipe(sistema_id, tipo_tubo_id, nivel_id, pt_top_reg, pt_valv_reg, dia_principal)
                tubo_v_reg_inf = make_pipe(sistema_id, tipo_tubo_id, nivel_id, pt_valv_reg, p_ini_reg, dia_principal)
                doc.Regenerate()

                if extensao:
                    no_ext = {'c': None, 'ext': True, 'p': DB.XYZ(p_ini_reg.X, p_ini_reg.Y, 0),
                              'z': ponta_ref.Z, 'pt': p_ini_reg, 'vert': tubo_v_reg_inf}
                    if esq: path.insert(0, no_ext)
                    else: path.append(no_ext)
                else:
                    fim['extra_verts'] = [tubo_v_reg_inf]

                n = len(path)
                h = [None] * max(n - 1, 0)
                for k in range(n - 1):
                    p1 = ponto_perna(path[k], k, k + 1)
                    p2 = ponto_perna(path[k + 1], k + 1, k)
                    p2 = DB.XYZ(p2.X, p2.Y, p1.Z)
                    if p1.DistanceTo(p2) < mm(5): continue
                    
                    if esq:
                        h[k] = make_pipe(sistema_id, tipo_tubo_id, nivel_id, p1, p2, dia_principal)
                    else:
                        h[k] = make_pipe(sistema_id, tipo_tubo_id, nivel_id, p2, p1, dia_principal)
                doc.Regenerate()

                for k in range(n):
                    no = path[k]
                    hp = h[k - 1] if k > 0 else None
                    hn = h[k] if k < n - 1 else None
                    hs = [x for x in (hp, hn) if x is not None]
                    if not hs: continue

                    try:
                        if no.get('canto_fix'):
                            h_saida = hn if no['out_dir'] == +1 else hp
                            h_main = hp if no['out_dir'] == +1 else hn
                            if h_main is None or h_saida is None: continue
                            criar_te_registro(doc, no['vert'], no['vert_up'], h_main, no['pt'], False)
                            place_elbow(no['vert_up'], h_saida, no['pt_out'])

                        elif no.get('vert') is None:
                            if len(hs) == 2:
                                place_elbow(hp, hn, no['pt'])

                        else:
                            verts = [no['vert']] + no.get('extra_verts', [])
                            p = no['pt']
                            if len(hs) == 2:
                                if len(verts) == 1:
                                    c_h1 = connector_near(hp, p, mm(25))[0]
                                    c_h2 = connector_near(hn, p, mm(25))[0]
                                    c_v = connector_near(verts[0], p, mm(25))[0]
                                    try:
                                        doc.Create.NewTeeFitting(c_h1, c_h2, c_v)
                                    except:
                                        try:
                                            doc.Create.NewTeeFitting(c_h2, c_h1, c_v)
                                        except Exception:
                                            pass
                            else:
                                if len(verts) == 1:
                                    place_elbow(verts[0], hs[0], p)
                                elif len(verts) == 2:
                                    criar_te_registro(doc, verts[0], verts[1], hs[0], p)
                    except Exception as ex_no:
                        AVISOS.append(u"Falha ao criar conexão no ponto {} do ramal: {}".format(k + 1, ex_no))

                reg_final = None
                if registro_existente:
                    dz = pt_valv_reg.Z - registro_existente.Location.Point.Z
                    dx = pt_valv_reg.X - registro_existente.Location.Point.X
                    dy = pt_valv_reg.Y - registro_existente.Location.Point.Y
                    if abs(dx) > mm(1) or abs(dy) > mm(1) or abs(dz) > mm(1):
                        DB.ElementTransformUtils.MoveElement(doc, registro_existente.Id, DB.XYZ(dx, dy, dz))
                    reg_final = doc.GetElement(registro_existente.Id)
                else:
                    sym_reg = obter_simbolo_por_diametro(doc, FAMILIA_REGISTRO_GERAL, dia_principal)
                    if sym_reg:
                        reg_final = doc.Create.NewFamilyInstance(pt_valv_reg, sym_reg, doc.GetElement(nivel_id), DB.Structure.StructuralType.NonStructural)

                doc.Regenerate()

                if reg_final:
                    forcar_vertical(doc, reg_final)
                    doc.Regenerate()

                    c_reg_all = end_piping(reg_final, only_open=True)
                    tubo_inf_ef = REG_INF[0] if REG_INF[0] is not None else tubo_v_reg_inf
                    c_tubo_inf_topo = connector_near(tubo_inf_ef, pt_valv_reg, mm(25))[0]
                    c_tubo_sup_base = connector_near(tubo_v_reg_sup, pt_valv_reg, mm(25))[0]

                    if len(c_reg_all) >= 2:
                        c_reg_all = sorted(c_reg_all, key=lambda x: x.Origin.Z)
                        
                        ligar_tubo_conector(doc, c_reg_all[0], c_tubo_inf_topo)
                        ligar_tubo_conector(doc, c_reg_all[1], c_tubo_sup_base)
                else:
                    AVISOS.append(u"Família do registro geral não encontrada; registro não foi criado.")

                forcar_buchas_normais(doc, todas_conexoes)
                garantir_pontos_consumo(doc, infos)

                ligados = 0
                for i in infos:
                    try:
                        c_vivo = _conector_da_peca(doc, i)
                        if c_vivo is not None and c_vivo.IsConnected:
                            ligados += 1
                        else:
                            AVISOS.append(u"Ponto de consumo SEM conexão com o joelho: {}".format(i['elem'].Name))
                    except:
                        AVISOS.append(u"Ponto de consumo SEM conexão com o joelho: {}".format(i['elem'].Name))
                INFO.append(u"{} de {} ponto(s) de consumo conectado(s) ao joelho com bucha.".format(ligados, len(infos)))

        uidoc.RefreshActiveView()
        msg = u""
        if INFO:
            msg += u"\n".join(INFO) + u"\n\n"
        if AVISOS:
            log(u"**Concluído com avisos:**\n" + msg + u"\n".join(AVISOS))
        else:
            log(u"**Concluído.** Ligação de água fria modelada com sucesso.\n" + msg)

    except Exception as exc:
        if not canceled(exc): forms.alert(u"Erro:\n{}".format(exc))