# -*- coding: utf-8 -*-
import math
from Autodesk.Revit.Exceptions import OperationCanceledException
from pyrevit import DB, UI, forms, revit

from geom_utils import *
from revit_mep import *
from ui_utils import *

DECLIVE_VENT = 0.01              # 1% subindo em direção à coluna
ALTURA_MIN_RAMAL = 300 / 304.8   # altura mínima (eixo do tronco - topo do tubo de esgoto) no ponto de cada ramal
DIST_MIN_PONTA = 150 / 304.8     # cruzamento precisa estar a pelo menos isso das pontas do tubo de esgoto
TOL_COLUNA_XY = 15 / 304.8       # tolerância para achar os trechos da coluna (mesmo XY)
SNAP_ANGULO = 45.0               # força o traçado a múltiplos de 45º (0, 45, 90, 135, etc)

AVISOS = []
INFO = []


class FiltroTubo(UI.Selection.ISelectionFilter):
    def AllowElement(self, elem):
        return isinstance(elem, DB.Plumbing.Pipe)
    def AllowReference(self, ref, pos): return False


def diametro_tubo(tubo):
    p = tubo.get_Parameter(DB.BuiltInParameter.RBS_PIPE_DIAMETER_PARAM)
    return p.AsDouble() if p else 0.0


def sistema_ventilacao(doc, col):
    try:
        p = col.get_Parameter(DB.BuiltInParameter.RBS_PIPING_SYSTEM_TYPE_PARAM)
        if p:
            eid = p.AsElementId()
            if eid and eid != DB.ElementId.InvalidElementId and doc.GetElement(eid) is not None:
                return eid
    except: pass
    sistemas = list(DB.FilteredElementCollector(doc).OfClass(DB.Plumbing.PipingSystemType))
    for st in sistemas:
        try:
            if st.SystemClassification == DB.MEPSystemClassification.Vent: return st.Id
        except: pass
    for st in sistemas:
        if u"ventila" in (st.Name or u"").lower(): return st.Id
    return None


def eh_esgoto(tubo):
    try:
        for c in iter_connectors(tubo):
            if c.Domain == DB.Domain.DomainPiping and c.PipeSystemType == DB.Plumbing.PipeSystemType.Sanitary:
                return True
    except: pass
    return False


def extremos(tubo):
    loc = tubo.Location
    if not isinstance(loc, DB.LocationCurve): return None
    crv = loc.Curve
    try: return crv.GetEndPoint(0), crv.GetEndPoint(1)
    except: return None


def tubos_da_coluna(doc, col_xy):
    res = []
    for t in DB.FilteredElementCollector(doc).OfClass(DB.Plumbing.Pipe):
        e = extremos(t)
        if not e: continue
        a, b = e
        if math.hypot(b.X - a.X, b.Y - a.Y) > mm(5): continue
        if abs(b.Z - a.Z) < mm(50): continue
        if math.hypot(a.X - col_xy.X, a.Y - col_xy.Y) > TOL_COLUNA_XY: continue
        res.append((t, min(a.Z, b.Z), max(a.Z, b.Z)))
    return res


def snap_ponto(prev, pt):
    vx, vy = pt.X - prev.X, pt.Y - prev.Y
    L = math.hypot(vx, vy)
    if SNAP_ANGULO <= 0 or L < 1e-6:
        return DB.XYZ(pt.X, pt.Y, 0)
    ang = math.degrees(math.atan2(vy, vx))
    a2 = round(ang / SNAP_ANGULO) * SNAP_ANGULO
    L2 = L * math.cos(math.radians(ang - a2))
    r = math.radians(a2)
    return DB.XYZ(prev.X + L2 * math.cos(r), prev.Y + L2 * math.sin(r), 0)


def montar_trecho_final(rota, col_xy):
    L = rota[-1]
    pen = rota[-2]
    dx, dy = L.X - pen.X, L.Y - pen.Y
    comp = math.hypot(dx, dy)
    
    if comp < 1e-6:
        rota.append(DB.XYZ(col_xy.X, col_xy.Y, 0))
        return rota
        
    vx, vy = dx / comp, dy / comp
    cx, cy = col_xy.X - pen.X, col_xy.Y - pen.Y
    
    proj = cx * vx + cy * vy
    p_proj_x = pen.X + vx * proj
    p_proj_y = pen.Y + vy * proj
    
    dist_n = math.hypot(col_xy.X - p_proj_x, col_xy.Y - p_proj_y)
    
    if dist_n < mm(10):
        rota[-1] = DB.XYZ(col_xy.X, col_xy.Y, 0)
        return rota
        
    p_curva_x = p_proj_x - vx * dist_n
    p_curva_y = p_proj_y - vy * dist_n
    
    rota[-1] = DB.XYZ(p_curva_x, p_curva_y, 0)
    rota.append(DB.XYZ(col_xy.X, col_xy.Y, 0))
    return rota


def achar_cruzamentos(doc, view, segs):
    resultados = []
    for tubo in DB.FilteredElementCollector(doc, view.Id).OfClass(DB.Plumbing.Pipe):
        if not eh_esgoto(tubo): continue
        e = extremos(tubo)
        if not e: continue
        A, B = e
        qx, qy = B.X - A.X, B.Y - A.Y
        if math.hypot(qx, qy) < mm(100): continue

        melhor = None
        for (P, Q, s_ini) in segs:
            rx, ry = Q.X - P.X, Q.Y - P.Y
            den = rx * qy - ry * qx
            if abs(den) < 1e-9: continue
            wx, wy = A.X - P.X, A.Y - P.Y
            t = (wx * qy - wy * qx) / den
            u = (wx * ry - wy * rx) / den
            if -1e-6 <= t <= 1 + 1e-6 and -1e-6 <= u <= 1 + 1e-6:
                s = s_ini + t * math.hypot(rx, ry)
                if melhor is None or s < melhor['s']:
                    melhor = {'tubo': tubo, 's': s, 'u': u, 'A': A, 'B': B, 'P': P, 'Q': Q, 's_ini': s_ini}
        if not melhor: continue

        A, B, u = melhor['A'], melhor['B'], melhor['u']
        comp3d = A.DistanceTo(B)
        if u * comp3d < DIST_MIN_PONTA or (1 - u) * comp3d < DIST_MIN_PONTA:
            AVISOS.append(u"Um tubo de esgoto cruza o traçado muito perto da ponta (fitting); foi ignorado.")
            continue
        melhor['x'] = A.X + u * (B.X - A.X)
        melhor['y'] = A.Y + u * (B.Y - A.Y)
        melhor['z_c'] = A.Z + u * (B.Z - A.Z)
        melhor['raio'] = diametro_tubo(tubo) / 2.0
        resultados.append(melhor)

    resultados.sort(key=lambda r: r['s'])
    return resultados


def criar_ramal_ventilacao(doc, tubo_esg, pt_c, pt_top, sistema_id, tipo_id, nivel_id, dia_vent):
    vert = make_pipe(sistema_id, tipo_id, nivel_id, pt_c, pt_top, dia_vent)
    doc.Regenerate()
    erros = []

    sa = DB.SubTransaction(doc)
    sa.Start()
    try:
        novo_id = DB.Plumbing.PlumbingUtils.BreakCurve(doc, tubo_esg.Id, pt_c)
        doc.Regenerate()
        novo = doc.GetElement(novo_id)
        c_a = connector_near(tubo_esg, pt_c, mm(15))[0]
        c_b = connector_near(novo, pt_c, mm(15))[0]
        c_v = connector_near(vert, pt_c, mm(15))[0]
        doc.Create.NewTeeFitting(c_a, c_b, c_v)
        sa.Commit()
        return vert, None
    except Exception as e:
        erros.append(e)
        try: sa.RollBack()
        except: pass

    sb = DB.SubTransaction(doc)
    sb.Start()
    try:
        c_v = connector_near(vert, pt_c, mm(15))[0]
        doc.Create.NewTakeoffFitting(c_v, tubo_esg)
        sb.Commit()
        return vert, None
    except Exception as e:
        erros.append(e)
        try: sb.RollBack()
        except: pass

    try: doc.Delete(vert.Id)
    except: pass
    return None, erros


def ligar_na_coluna(doc, tubo_final, pt_fim, tubo_coluna):
    erros = []

    sa = DB.SubTransaction(doc)
    sa.Start()
    try:
        novo_id = DB.Plumbing.PlumbingUtils.BreakCurve(doc, tubo_coluna.Id, pt_fim)
        doc.Regenerate()
        novo = doc.GetElement(novo_id)
        c_a = connector_near(tubo_coluna, pt_fim, mm(15))[0]
        c_b = connector_near(novo, pt_fim, mm(15))[0]
        c_t = connector_near(tubo_final, pt_fim, mm(15))[0]
        doc.Create.NewTeeFitting(c_a, c_b, c_t)
        sa.Commit()
        return True
    except Exception as e:
        erros.append(e)
        try: sa.RollBack()
        except: pass

    sb = DB.SubTransaction(doc)
    sb.Start()
    try:
        c_t = connector_near(tubo_final, pt_fim, mm(15))[0]
        doc.Create.NewTakeoffFitting(c_t, tubo_coluna)
        sb.Commit()
        return True
    except Exception as e:
        erros.append(e)
        try: sb.RollBack()
        except: pass

    AVISOS.append(u"Não foi possível ligar o tronco na coluna. Erro Tê: {} | Erro derivação: {}".format(erros[0], erros[1]))
    return False


def executar(doc, uidoc):
    try:
        del AVISOS[:]
        del INFO[:]

        view = uidoc.ActiveView
        if not isinstance(view, DB.ViewPlan):
            forms.alert(u"Abra uma vista de PLANTA para traçar a ventilação.")
            return

        try:
            ref_col = uidoc.Selection.PickObject(
                UI.Selection.ObjectType.Element, FiltroTubo(), u"1/2: Clique na coluna de ventilação (tubo vertical)")
            col = doc.GetElement(ref_col)
        except OperationCanceledException:
            return

        e_col = extremos(col)
        if not e_col: raise Exception(u"Não foi possível ler a geometria da coluna.")
        a, b = e_col
        if math.hypot(b.X - a.X, b.Y - a.Y) > mm(5) or abs(b.Z - a.Z) < mm(50):
            forms.alert(u"A coluna precisa ser um tubo VERTICAL.")
            return
        col_xy = DB.XYZ(a.X, a.Y, 0)

        sistema_id = sistema_ventilacao(doc, col)
        if sistema_id is None:
            forms.alert(u"Não encontrei o sistema de Ventilação no projeto.")
            return
        tipo_id = col.GetTypeId()
        try: nivel_id = col.ReferenceLevel.Id
        except: nivel_id = nivel_de(col, doc)
        dia_vent = diametro_tubo(col)
        col_pipes = tubos_da_coluna(doc, col_xy)
        if not any(t.Id == col.Id for t, _z1, _z2 in col_pipes):
            col_pipes.append((col, min(a.Z, b.Z), max(a.Z, b.Z)))

        pontos = []
        while True:
            msg = u"2/2: Clique o 1º ponto do traçado" if not pontos else u"Próximo ponto (ESC para concluir)"
            try:
                pt = uidoc.Selection.PickPoint(msg)
            except OperationCanceledException:
                break
                
            p = DB.XYZ(pt.X, pt.Y, 0)
            if pontos: p = snap_ponto(pontos[-1], p)
            if pontos and math.hypot(p.X - pontos[-1].X, p.Y - pontos[-1].Y) < mm(50): continue
            pontos.append(p)

        if len(pontos) < 2:
            return

        d_ini = math.hypot(pontos[0].X - col_xy.X, pontos[0].Y - col_xy.Y)
        d_fim = math.hypot(pontos[-1].X - col_xy.X, pontos[-1].Y - col_xy.Y)
        if d_ini < d_fim: pontos.reverse()

        n_user = len(pontos)
        rota = montar_trecho_final(list(pontos), col_xy)

        s_rota = [0.0]
        for i in range(1, len(rota)):
            s_rota.append(s_rota[-1] + math.hypot(rota[i].X - rota[i-1].X, rota[i].Y - rota[i-1].Y))

        segs = []
        for i in range(n_user - 1):
            segs.append((rota[i], rota[i + 1], s_rota[i]))

        ramais = achar_cruzamentos(doc, view, segs)
        if not ramais:
            forms.alert(u"O traçado não cruza nenhum tubo de esgoto nesta vista.")
            return

        z0 = max([r['z_c'] + r['raio'] + ALTURA_MIN_RAMAL - DECLIVE_VENT * r['s'] for r in ramais])
        S_total = s_rota[-1]
        z_fim = z0 + DECLIVE_VENT * S_total

        tubo_col_alvo = None
        for t, zmin, zmax in col_pipes:
            if zmin + mm(80) <= z_fim <= zmax - mm(80):
                tubo_col_alvo = t
                break
        if tubo_col_alvo is None:
            raise Exception(u"A coluna não alcança a cota de ligação do tronco ({:.0f} mm). Verifique a coluna.".format(to_mm(z_fim)))

        with Grupo(u"Ventilação"):
            with revit.Transaction(u"Ventilação: tronco e ramais"):

                ok = []
                for r in ramais:
                    P_seg, Q_seg, s_ini = r['P'], r['Q'], r['s_ini']
                    
                    P3D = DB.XYZ(P_seg.X, P_seg.Y, z0 + DECLIVE_VENT * s_ini)
                    dist_PQ = math.hypot(Q_seg.X - P_seg.X, Q_seg.Y - P_seg.Y)
                    Q3D = DB.XYZ(Q_seg.X, Q_seg.Y, z0 + DECLIVE_VENT * (s_ini + dist_PQ))
                    
                    V_t = (Q3D - P3D).Normalize()
                    pt_A = r['A']
                    V_s = (r['B'] - r['A']).Normalize()
                    
                    w0 = pt_A - P3D
                    a = 1.0
                    b = V_s.DotProduct(V_t)
                    c = 1.0
                    d = V_s.DotProduct(w0)
                    e = V_t.DotProduct(w0)
                    denom = a*c - b*b
                    
                    if denom > 1e-6:
                        sc = (b*e - c*d) / denom
                        tc = (a*e - b*d) / denom
                        C1 = pt_A + V_s.Multiply(sc)
                        C2 = P3D + V_t.Multiply(tc)
                        
                        v2d_x = Q_seg.X - P_seg.X
                        v2d_y = Q_seg.Y - P_seg.Y
                        l2d = math.hypot(v2d_x, v2d_y)
                        if l2d > 1e-6:
                            s_exact = s_ini + (C2.X - P_seg.X) * (v2d_x / l2d) + (C2.Y - P_seg.Y) * (v2d_y / l2d)
                        else:
                            s_exact = s_ini
                    else:
                        C1 = DB.XYZ(r['x'], r['y'], r['z_c'])
                        C2 = DB.XYZ(r['x'], r['y'], z0 + DECLIVE_VENT * r['s'])
                        s_exact = r['s']
                    
                    vert, erros = criar_ramal_ventilacao(doc, r['tubo'], C1, C2, sistema_id, tipo_id, nivel_id, dia_vent)
                    if vert is None:
                        AVISOS.append(u"Ramal de ventilação não criado num tubo de esgoto. Erro Tê: {} | Erro derivação: {}".format(erros[0], erros[1]))
                        continue
                    
                    r['vert'] = vert
                    r['C2'] = C2
                    r['s_exact'] = s_exact
                    ok.append(r)

                if not ok:
                    raise Exception(u"Nenhum ramal de ventilação pôde ser ligado aos tubos de esgoto.\n" + u"\n".join(AVISOS))

                nos = []
                for r in ok:
                    nos.append({'tipo': 'ramal', 'pt': r['C2'], 's': r['s_exact'], 'vert': r['vert']})
                s_primeiro = ok[0]['s_exact']

                for k in range(1, len(rota) - 1):
                    if s_rota[k] <= s_primeiro + mm(60): continue
                    P0, P1, P2 = rota[k - 1], rota[k], rota[k + 1]
                    d1 = (P1.X - P0.X, P1.Y - P0.Y)
                    d2 = (P2.X - P1.X, P2.Y - P1.Y)
                    l1, l2 = math.hypot(*d1), math.hypot(*d2)
                    if l1 < 1e-6 or l2 < 1e-6: continue
                    cosang = max(-1.0, min(1.0, (d1[0] * d2[0] + d1[1] * d2[1]) / (l1 * l2)))
                    if math.degrees(math.acos(cosang)) < 2.0: continue
                    
                    if any(abs(s_rota[k] - r['s_exact']) < mm(80) for r in ok):
                        AVISOS.append(u"Uma curva do traçado ficou muito perto de um ramal e foi ignorada.")
                        continue
                        
                    pt_curva = DB.XYZ(P1.X, P1.Y, z0 + DECLIVE_VENT * s_rota[k])
                    nos.append({'tipo': 'curva', 'pt': pt_curva, 's': s_rota[k]})

                pt_col = DB.XYZ(col_xy.X, col_xy.Y, z0 + DECLIVE_VENT * S_total)
                nos.append({'tipo': 'coluna', 'pt': pt_col, 's': S_total})
                nos.sort(key=lambda n: n['s'])

                tronco = [None] * (len(nos) - 1)
                for k in range(len(nos) - 1):
                    p1, p2 = nos[k]['pt'], nos[k + 1]['pt']
                    if p1.DistanceTo(p2) < mm(5): continue
                    tronco[k] = make_pipe(sistema_id, tipo_id, nivel_id, p1, p2, dia_vent)
                doc.Regenerate()

                for k, n in enumerate(nos):
                    tp = tronco[k - 1] if k > 0 else None
                    tn = tronco[k] if k < len(nos) - 1 else None
                    try:
                        if n['tipo'] == 'ramal':
                            if tp is not None and tn is not None:
                                c1 = connector_near(tp, n['pt'], mm(25))[0]
                                c2 = connector_near(tn, n['pt'], mm(25))[0]
                                c3 = connector_near(n['vert'], n['pt'], mm(25))[0]
                                try:
                                    doc.Create.NewTeeFitting(c1, c2, c3)
                                except:
                                    try: 
                                        c3.ConnectTo(c1)
                                        c3.ConnectTo(c2)
                                    except: pass
                            else:
                                outro = tp if tp is not None else tn
                                if outro is not None:
                                    try: place_elbow(n['vert'], outro, n['pt'])
                                    except:
                                        c1 = connector_near(n['vert'], n['pt'], mm(25))[0]
                                        c2 = connector_near(outro, n['pt'], mm(25))[0]
                                        try: c1.ConnectTo(c2)
                                        except: pass
                        elif n['tipo'] == 'curva':
                            if tp is not None and tn is not None:
                                place_elbow(tp, tn, n['pt'])
                        else:
                            if tp is not None:
                                ligar_na_coluna(doc, tp, n['pt'], tubo_col_alvo)
                    except Exception as ex_no:
                        AVISOS.append(u"Falha ao criar conexão ({}) no ponto {} do tronco: {}".format(n['tipo'], k + 1, ex_no))

                INFO.append(u"{} ramal(is) de ventilação ligado(s) aos tubos de esgoto.".format(len(ok)))
                INFO.append(u"Tronco com {:.0f}% de inclinação subindo até à coluna (cota de ligação: {:.0f} mm).".format(DECLIVE_VENT * 100, to_mm(z_fim)))

        uidoc.RefreshActiveView()
        msg = u"\n".join(INFO)
        if AVISOS:
            log(u"**Concluído com avisos:**\n" + msg + u"\n\n" + u"\n".join(AVISOS))
        else:
            log(u"**Ventilação criada.**\n" + msg)

    except OperationCanceledException:
        pass
    except Exception as exc:
        forms.alert(u"Erro:\n{}".format(exc))