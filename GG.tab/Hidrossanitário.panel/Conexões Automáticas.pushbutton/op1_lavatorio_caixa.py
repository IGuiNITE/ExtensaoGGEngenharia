# -*- coding: utf-8 -*-
import math
from pyrevit import DB, UI, forms, revit

from geom_utils import *
from revit_mep import *
from ui_utils import *

def planejar_lavatorio(lav_conn, caixa, min_len=TRECHO_MIN):
    ponto = lav_conn.Origin
    A = xy(ponto)
    cx = xy(lav_conn.CoordinateSystem.BasisX).Normalize()
    cy = xy(lav_conn.CoordinateSystem.BasisY).Normalize()
    
    p_caixa = next((c.Origin for c in end_piping(caixa)), A)
    vec_caixa = xy(p_caixa) - A
    vec_caixa = vec_caixa.Normalize() if vec_caixa.GetLength() > 1e-5 else DB.XYZ(1,0,0)
    
    if abs(cx.DotProduct(vec_caixa)) > abs(cy.DotProduct(vec_caixa)):
        v_out = cx if cx.DotProduct(vec_caixa) > 0 else cx.Negate()
    else:
        v_out = cy if cy.DotProduct(vec_caixa) > 0 else cy.Negate()
    
    melhor = None
    menor_caminho = 999999
    
    for conn in end_piping(caixa, only_open=True):
        if conn.Direction != DB.FlowDirectionType.In: continue
        d = horizontal_dir(conn)
        if d is None or not multiplo_45(d): continue
            
        B = xy(conn.Origin)
        v_in = DB.XYZ(-d.X, -d.Y, 0)
        vec = B - A
        along = (A - B).DotProduct(d)
        if along < min_len: continue
            
        n = DB.XYZ(-d.Y, d.X, 0)
        perp = (A - B).DotProduct(n)
        
        if abs(perp) < mm(5) and v_out.DotProduct(v_in) > 0.99:
            return conn, DB.XYZ.Zero, [A, B]
            
        u = v_out
        denom = u.X * v_in.Y - u.Y * v_in.X
        if abs(denom) < 1e-8: continue 
            
        t = (vec.X * v_in.Y - vec.Y * v_in.X) / denom
        s = (u.X * vec.Y - u.Y * vec.X) / denom
        
        if t > mm(40) and s > mm(40):
            ang_deg = round(math.degrees(u.AngleTo(v_in)))
            if ang_deg == 90 or ang_deg == 270:
                cut = mm(120)
                if t > cut + mm(40) and s > cut + mm(40):
                    peso = t + s
                    if peso < menor_caminho:
                        menor_caminho = peso
                        C = A + u.Multiply(t)
                        melhor = (conn, DB.XYZ.Zero, [A, C - u.Multiply(cut), C + v_in.Multiply(cut), B])
            else:
                peso = t + s
                if peso < menor_caminho:
                    menor_caminho = peso
                    melhor = (conn, DB.XYZ.Zero, [A, A + u.Multiply(t), B])

    if melhor is not None: return melhor[0], melhor[1], melhor[2]

    for conn in end_piping(caixa, only_open=True):
        if conn.Direction != DB.FlowDirectionType.In: continue
        d = horizontal_dir(conn)
        if d is None or not multiplo_45(d): continue
        B = xy(conn.Origin)
        if v_out.DotProduct(DB.XYZ(-d.X, -d.Y, 0)) > 0.99:
            n = DB.XYZ(-d.Y, d.X, 0)
            perp = (A - B).DotProduct(n)
            return conn, n.Multiply(perp), [A, B + n.Multiply(perp)]
            
    return None

def ligar_lavatorio(lav_conn, inlet, tipo_id, sistema_id, nivel_id, rota_xy, doc):
    p_lav = lav_conn.Origin
    p_in = inlet.Origin
    dia_l = lav_conn.Radius * 2.0
    if abs(to_mm(dia_l - inlet.Radius * 2.0)) > 1.0: raise Exception(u"Diâmetros diferentes.")
    
    distancias = [rota_xy[i].DistanceTo(rota_xy[i+1]) for i in range(len(rota_xy) - 1)]
    z_canto_lav = p_in.Z + DECLIVE * sum(distancias)
    if p_lav.Z - z_canto_lav < QUEDA_MINIMA: raise Exception(u"Não há altura para o trecho vertical.")
    
    tubos = []
    ponto_vertical = DB.XYZ(rota_xy[0].X, rota_xy[0].Y, z_canto_lav)
    vertical = make_pipe(sistema_id, tipo_id, nivel_id, p_lav, ponto_vertical, dia_l)
    tubos.append(vertical)
    
    z_atual = z_canto_lav
    tubo_anterior = vertical
    ponto_anterior = ponto_vertical
    
    for i in range(1, len(rota_xy)):
        z_atual -= DECLIVE * distancias[i-1]
        proximo = p_in if i == len(rota_xy) - 1 else DB.XYZ(rota_xy[i].X, rota_xy[i].Y, z_atual)
        tubo_horiz = make_pipe(sistema_id, tipo_id, nivel_id, ponto_anterior, proximo, dia_l)
        tubos.append(tubo_horiz)
        doc.Regenerate()
        
        if i == 1:
            c_v, _ = connector_near(vertical, p_lav, mm(25))
            conectar(lav_conn, c_v)
        place_elbow(tubo_anterior, tubo_horiz, ponto_anterior)
        
        tubo_anterior = tubo_horiz
        ponto_anterior = proximo
        
    doc.Regenerate()
    c_final, _ = connector_near(tubo_anterior, p_in, mm(25))
    conectar(inlet, c_final)
    return tubos

def executar(doc, uidoc):
    try:
        lav = doc.GetElement(uidoc.Selection.PickObject(UI.Selection.ObjectType.Element, u"1/2: Selecione o lavatório"))
        caixa = doc.GetElement(uidoc.Selection.PickObject(UI.Selection.ObjectType.Element, u"2/2: Selecione a caixa"))
        
        lav_conn = escolher_saida_lavatorio(lav)
        if not lav_conn: return
        
        achado = planejar_lavatorio(lav_conn, caixa)
        if not achado:
            forms.alert(u"Nenhuma entrada livre da caixa fica à frente do lavatório.\nGire a caixa.")
            return
            
        inlet, move1, rota_xy = achado
        if move1.GetLength() > DESLOC_MAX_CAIXA:
            if not forms.alert(u"Mover a caixa {:.0f} mm para alinhar?".format(to_mm(move1.GetLength())), yes=True, no=True):
                return
                
        tipo_id = escolher_tipo_ramal(caixa.GetTypeId())
        if not tipo_id: return
        
        sistema_id = obter_sistema(caixa)
        nivel_id = nivel_de(caixa, doc)
        
        criados = []
        with Grupo(u"Ligação Esgoto: Lavatório -> Caixa"):
            if move1.GetLength() > mm(0.5):
                with revit.Transaction(u"Alinhar caixa"):
                    DB.ElementTransformUtils.MoveElement(doc, caixa.Id, move1)
                doc.Regenerate()
                log(u"Caixa movida **{:.1f} mm**.".format(to_mm(move1.GetLength())))
                
            # Atualiza rigorosamente as referências (previne erro de Object is Invalid)
            lav_fresco = doc.GetElement(lav.Id)
            caixa_fresca = doc.GetElement(caixa.Id)
            lav_conn = connector_by_id(lav_fresco, lav_conn.Id)
            inlet = connector_by_id(caixa_fresca, inlet.Id)
                
            with revit.Transaction(u"Ramal do lavatório"):
                criados = ligar_lavatorio(lav_conn, inlet, tipo_id, sistema_id, nivel_id, rota_xy, doc)
                
        uidoc.RefreshActiveView()
        log(u"**Pronto.** Tubos criados:\n\n{}".format(descrever(criados)))
        
    except Exception as exc:
        if not canceled(exc): forms.alert(u"Erro:\n{}".format(exc))