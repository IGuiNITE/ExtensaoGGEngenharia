# -*- coding: utf-8 -*-
import math
import re
from pyrevit import DB, UI, forms, revit, script

from geom_utils import *
from revit_mep import *
from ui_utils import *

ALTURA_BAIXA = 1000 / 304.8
ALTURA_ALTA = 1500 / 304.8
ALTURA_REGISTRO_GERAL = 2100 / 304.8
DESLOC_CHUVEIRO = 150 / 304.8
NOME_SISTEMA_AGUA = u"Água Fria Potável"
FAMILIA_REGISTRO_GERAL = u"RG_Registro de Gaveta c/ PVC"
FAMILIA_REGISTRO_PRESSAO = u"RP_Registro de Pressão C/ PVC"

BUCHAS_IDS = []
AVISOS = []
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


def conector_agua(elem):
    for c in end_piping(elem, only_open=True):
        if c.PipeSystemType in [DB.Plumbing.PipeSystemType.DomesticColdWater, DB.Plumbing.PipeSystemType.Global]:
            return c
    return None


def alinhar_pecas(doc, ref_elem, outras_pecas):
    c_ref = conector_agua(ref_elem)
    if not c_ref: return
    loc_ref = c_ref.Origin
    facing = ref_elem.FacingOrientation
    eixo_parede = DB.XYZ(-facing.Y, facing.X, 0).Normalize()

    for elem in outras_pecas:
        if elem.Category.Id.IntegerValue != int(DB.BuiltInCategory.OST_PlumbingFixtures): continue
        c_atual = conector_agua(elem)
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
    """Extrai o diâmetro em mm do nome do tipo, ex.: 'RP_Ø3/4" (25mm) com acabamento' -> 25.0"""
    try:
        m = re.search(r'(\d+(?:[.,]\d+)?)\s*mm', (nome or u"").lower())
        if m: return float(m.group(1).replace(',', '.'))
    except: pass
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
        try: c_fixo.ConnectTo(c_movel)
        except: pass
        return None
    elif abs(dot) < 0.15:
        try: return doc.Create.NewElbowFitting(c_fixo, c_movel)
        except:
            try: c_fixo.ConnectTo(c_movel)
            except: pass
            return None
    else:
        try: c_fixo.ConnectTo(c_movel)
        except: pass
        return None


def encaixar_tubo_no_conector(doc, tubo, pt_ref, c_dest):
    try:
        conns = [c for c in iter_connectors(tubo)
                 if c.Domain == DB.Domain.DomainPiping and c.ConnectorType == DB.ConnectorType.End]
        c_ponta = min(conns, key=lambda c: c.Origin.DistanceTo(pt_ref))
        c_outra = max(conns, key=lambda c: c.Origin.DistanceTo(pt_ref))
        alvo = DB.XYZ(c_ponta.Origin.X, c_ponta.Origin.Y, c_dest.Origin.Z)
        if c_ponta.Origin.DistanceTo(alvo) > mm(1):
            tubo.Location.Curve = DB.Line.CreateBound(c_outra.Origin, alvo)
            doc.Regenerate()
        c_novo = connector_near(tubo, alvo, mm(10))[0]
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
            if p.AsInteger() != 0:
                p.Set(0)
        except:
            pass
    return achou


def set_param_zero(elem, param_name=None):
    _zerar_param_inverter(elem)


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
        except: pass

    for c in conectores_pecas:
        try:
            for ref in c.AllRefs:
                owner = ref.Owner
                if owner is None: continue
                if owner.Category is None: continue
                if owner.Category.Id.IntegerValue != int(DB.BuiltInCategory.OST_PipeFitting): continue
                alvos.append(owner)
        except: pass

    vistos = set()
    for el in alvos:
        try:
            if el.Id.IntegerValue in vistos: continue
            vistos.add(el.Id.IntegerValue)
            _zerar_param_inverter(el)
        except: pass

    doc.Regenerate()

    for el in alvos:
        try:
            _zerar_param_inverter(el)
        except: pass


def aplicar_bucha(doc, elbow):
    if not elbow: return
    sym_bucha = obter_simbolo_conexao(doc, [u"joelho", u"bucha"])
    if not sym_bucha:
        _zerar_param_inverter(elbow)
        registrar_bucha(elbow)
        return

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
                        try: doc.Delete(eid)
                        except: pass
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
                            try: melhor_ext.ConnectTo(c_b)
                            except: pass
            except: pass

    if bucha_final:
        doc.Regenerate()
        _zerar_param_inverter(bucha_final)
        registrar_bucha(bucha_final)


def forcar_vertical(doc, inst):
    if not inst: return
    c_all = end_piping(inst, only_open=True)
    if not c_all: return
    dir_c = c_all[0].CoordinateSystem.BasisZ
    if abs(dir_c.Z) > 0.9: return
    eixo = dir_c.CrossProduct(DB.XYZ.BasisZ)
    if eixo.GetLength() < 1e-5: eixo = DB.XYZ.BasisX
    linha_eixo = DB.Line.CreateBound(inst.Location.Point, inst.Location.Point + eixo.Normalize())
    try: DB.ElementTransformUtils.RotateElement(doc, inst.Id, linha_eixo, math.pi / 2.0)
    except: pass


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

def criar_te_registro(doc, v_baixo, v_cima, tubo_h, p):
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
            try: c_far_cima.DisconnectFrom(r)
            except: pass

        v_baixo.Location.Curve = DB.Line.CreateBound(pt_ini, pt_fim)
        doc.Delete(v_cima.Id)
        doc.Regenerate()

        REG_INF[0] = v_baixo
        c_topo_novo = connector_near(v_baixo, pt_fim, mm(25))[0]
        for r in refs_externas:
            try: c_topo_novo.ConnectTo(r)
            except: pass

        c_h = connector_near(tubo_h, p, mm(25))[0]
        return doc.Create.NewTakeoffFitting(c_h, v_baixo)
    except Exception as e2:
        AVISOS.append(u"Não foi possível criar o Tê do registro geral. Erro 1: {} | Erro 2: {} | DIAGNÓSTICO: {}".format(ultimo_erro, e2, diag))
        return None


def executar(doc, uidoc):
    try:
        del BUCHAS_IDS[:]
        del AVISOS[:]
        REG_INF[0] = None

        ref_ref = uidoc.Selection.PickObject(UI.Selection.ObjectType.Element, FiltroPecas(), u"1. Clique na peça base (dita o alinhamento)")
        ref_elem = doc.GetElement(ref_ref)

        outras_refs = uidoc.Selection.PickObjects(UI.Selection.ObjectType.Element, FiltroPecas(), u"2. Selecione as peças e o registo geral (opcional)")
        outras_pecas = [doc.GetElement(r) for r in outras_refs]

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

        with Grupo(u"Ligação Água Fria"):
            with revit.Transaction(u"Alinhar e Modelar Água"):
                alinhar_pecas(doc, ref_elem, outras_pecas)
                doc.Regenerate()

                conexoes = []
                chuveiros = []
                max_z_normal = 0.0
                dia_principal = 0.0

                for peca in todas_pecas:
                    c = conector_agua(doc.GetElement(peca.Id))
                    if not c: continue
                    z_rel = c.Origin.Z - nivel_z
                    dia = c.Radius * 2.0
                    if dia > dia_principal: dia_principal = dia

                    if z_rel > mm(1800): chuveiros.append(c)
                    else:
                        conexoes.append(c)
                        if z_rel > max_z_normal: max_z_normal = z_rel

                todas_conexoes = conexoes + chuveiros
                if not todas_conexoes: raise Exception(u"Nenhuma peça possui conector de água livre.")

                z_ramal_relativo = ALTURA_BAIXA if max_z_normal <= mm(1000) else ALTURA_ALTA
                z_ramal_abs = nivel_z + z_ramal_relativo
                z_pressao_abs = nivel_z + (mm(1200) if z_ramal_relativo == ALTURA_BAIXA else mm(1600))

                for c in todas_conexoes:
                    if abs(c.Origin.Z - z_ramal_abs) < mm(25):
                        z_ramal_abs += mm(100)
                        break
                if z_pressao_abs - z_ramal_abs < mm(200):
                    z_pressao_abs = z_ramal_abs + mm(200)

                facing = ref_elem.FacingOrientation
                eixo_parede = DB.XYZ(-facing.Y, facing.X, 0).Normalize()
                todas_conexoes.sort(key=lambda c: (c.Origin - ref_elem.Location.Point).DotProduct(eixo_parede))

                tubos_verticais = []
                pontos_horizontais = []
                flags_chuveiro = []

                for c in todas_conexoes:
                    origem = c.Origin
                    pt_topo = DB.XYZ(origem.X, origem.Y, z_ramal_abs)
                    pontos_horizontais.append(pt_topo)

                    is_chuveiro = (origem.Z - nivel_z) > mm(1800)
                    flags_chuveiro.append(is_chuveiro)

                    if is_chuveiro:
                        pt_valvula = DB.XYZ(origem.X, origem.Y, z_pressao_abs)
                        tubo_v1 = make_pipe(sistema_id, tipo_tubo_id, nivel_id, pt_topo, pt_valvula, dia_principal)
                        tubo_v2 = make_pipe(sistema_id, tipo_tubo_id, nivel_id, pt_valvula, origem, dia_principal)
                        doc.Regenerate()

                        c_tubo2_topo = connector_near(tubo_v2, origem, mm(25))[0]
                        elbow_chuv = ligar_tubo_conector(doc, c, c_tubo2_topo)
                        aplicar_bucha(doc, elbow_chuv)

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
                        tubos_verticais.append(tubo_v1)
                    else:
                        tubo_v = make_pipe(sistema_id, tipo_tubo_id, nivel_id, origem, pt_topo, dia_principal)
                        tubos_verticais.append(tubo_v)
                        doc.Regenerate()
                        c_tubo = connector_near(tubo_v, origem, mm(25))[0]
                        elbow_normal = ligar_tubo_conector(doc, c, c_tubo)
                        aplicar_bucha(doc, elbow_normal)

                if registro_existente:
                    try:
                        dots = [(c.Origin - ref_elem.Location.Point).DotProduct(eixo_parede) for c in todas_conexoes]
                        d_reg = (registro_existente.Location.Point - ref_elem.Location.Point).DotProduct(eixo_parede)
                        lado_reg = u"Esquerda" if d_reg < (min(dots) + max(dots)) / 2.0 else u"Direita"
                    except: pass

                esq = (lado_reg == u"Esquerda")
                idx_reg = 0 if esq else len(pontos_horizontais) - 1
                ponta_ref = pontos_horizontais[idx_reg]
                z_valv_reg = nivel_z + ALTURA_REGISTRO_GERAL

                extensao = flags_chuveiro[idx_reg]
                if extensao:
                    sinal = -1.0 if esq else 1.0
                    ponta_reg = DB.XYZ(ponta_ref.X + eixo_parede.X * sinal * DESLOC_CHUVEIRO,
                                       ponta_ref.Y + eixo_parede.Y * sinal * DESLOC_CHUVEIRO,
                                       ponta_ref.Z)
                    p_ini_reg = ponta_reg
                    pt_valv_reg = DB.XYZ(ponta_reg.X, ponta_reg.Y, z_valv_reg)
                    pt_top_reg = DB.XYZ(ponta_reg.X, ponta_reg.Y, z_valv_reg + mm(200))
                else:
                    p_ini_reg, pt_valv_reg, pt_top_reg = alinhar_eixo_registro(
                        tubos_verticais[idx_reg], ponta_ref, z_valv_reg, mm(200))

                tubo_v_reg_inf = make_pipe(sistema_id, tipo_tubo_id, nivel_id, p_ini_reg, pt_valv_reg, dia_principal)
                tubo_v_reg_sup = make_pipe(sistema_id, tipo_tubo_id, nivel_id, pt_valv_reg, pt_top_reg, dia_principal)
                doc.Regenerate()

                nos = []
                for i in range(len(pontos_horizontais)):
                    nos.append({'p': pontos_horizontais[i], 'verts': [tubos_verticais[i]]})

                if extensao:
                    novo = {'p': p_ini_reg, 'verts': [tubo_v_reg_inf]}
                    if esq: nos.insert(0, novo)
                    else: nos.append(novo)
                else:
                    nos[idx_reg]['verts'].append(tubo_v_reg_inf)

                n = len(nos)
                h = [None] * max(n - 1, 0)
                for k in range(n - 1):
                    p1 = nos[k]['p']
                    p2 = nos[k + 1]['p']
                    if p1.DistanceTo(p2) < mm(5): continue
                    h[k] = make_pipe(sistema_id, tipo_tubo_id, nivel_id, p1, p2, dia_principal)
                doc.Regenerate()

                for k in range(n):
                    hp = h[k - 1] if k > 0 else None
                    hn = h[k] if k < n - 1 else None
                    hs = [x for x in (hp, hn) if x is not None]
                    verts = nos[k]['verts']
                    p = nos[k]['p']
                    if not hs: continue

                    if len(hs) == 2:
                        if len(verts) == 1:
                            try:
                                c_h1 = connector_near(hp, p, mm(25))[0]
                                c_h2 = connector_near(hn, p, mm(25))[0]
                                c_v = connector_near(verts[0], p, mm(25))[0]
                                doc.Create.NewTeeFitting(c_h1, c_h2, c_v)
                            except: pass
                    else:
                        # nó de ponta
                        if len(verts) == 1:
                            place_elbow(verts[0], hs[0], p)
                        elif len(verts) == 2:
                            criar_te_registro(doc, verts[0], verts[1], hs[0], p)

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
                        if c_reg_all[0].Origin.Z < c_reg_all[1].Origin.Z:
                            ligar_tubo_conector(doc, c_reg_all[0], c_tubo_inf_topo)
                            ligar_tubo_conector(doc, c_reg_all[1], c_tubo_sup_base)
                        else:
                            ligar_tubo_conector(doc, c_reg_all[1], c_tubo_inf_topo)
                            ligar_tubo_conector(doc, c_reg_all[0], c_tubo_sup_base)
                else:
                    AVISOS.append(u"Família do registro geral não encontrada; registro não foi criado.")

                forcar_buchas_normais(doc, todas_conexoes)

        uidoc.RefreshActiveView()
        if AVISOS:
            log(u"**Concluído com avisos:**\n" + u"\n".join(AVISOS))
        else:
            log(u"**Concluído.** Ligação de água fria modelada com sucesso.")

    except Exception as exc:
        if not canceled(exc): forms.alert(u"Erro:\n{}".format(exc))