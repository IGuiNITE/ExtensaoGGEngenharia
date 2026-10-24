# -*- coding: utf-8 -*-
import math
from pyrevit import DB, UI, forms, revit

from geom_utils import *
from revit_mep import *
from ui_utils import *

def ramal_direto(lav, alvo, lav_conn_id, rota, sistema_id, nivel_id, tipo_id, doc):
    lav_conn = connector_by_id(lav, lav_conn_id)
    hit = rota[-1]
    origem = lav_conn.Origin
    dia = lav_conn.Radius * 2.0
    anterior = rota[-2]
    
    travel = DB.XYZ((hit.X - anterior.X), (hit.Y - anterior.Y), 0).Normalize()
    travel = DB.XYZ(travel.X, travel.Y, -DECLIVE).Normalize()
    
    with revit.Transaction(u"Junção Y no coletor (Direto)"):
        tee, theta = criar_juncao_y(alvo.Id, hit, travel, dia, sistema_id, nivel_id, [alvo.GetTypeId(), tipo_id])
        tee_id = tee.Id
        
    tee = doc.GetElement(tee_id)
    renomear_juncao_45(tee, theta)
    cb = ponta_livre(doc.GetElement(tee_id))
    
    z_chao = hit.Z + DECLIVE * comprimento_planta(rota)
    if origem.Z <= z_chao: raise Exception(u"Aparelho não tem altura para a queda.")
    
    pontos = [origem, DB.XYZ(rota[0].X, rota[0].Y, z_chao)]
    for p in rota[1:-1]:
        pontos.append(DB.XYZ(p.X, p.Y, hit.Z + DECLIVE * xy(p).DistanceTo(xy(hit))))
    pontos.append(cb.Origin)
    
    with revit.Transaction(u"Ramal direto"):
        criados = []
        for i in range(len(pontos) - 1): 
            criados.append(make_pipe(sistema_id, tipo_id, nivel_id, pontos[i], pontos[i + 1], dia))
        doc.Regenerate()
        
        # Atualiza rigorosamente o lavatório para evitar conector inválido
        lav_fresco = doc.GetElement(lav.Id)
        lav_conn = connector_by_id(lav_fresco, lav_conn_id)
        
        c_ini, _ = connector_near(criados[0], origem, mm(25))
        conectar(lav_conn, c_ini)
        
        for i in range(len(criados) - 1):
            place_elbow(criados[i], criados[i + 1], criados[i].Location.Curve.GetEndPoint(1))
            
        c_fim, _ = connector_near(criados[-1], pontos[-1], mm(25))
        conectar(cb, c_fim)
        
    log(u"Junção Y Direta: **{}** ({:.2f}°).".format(element_label(tee), math.degrees(theta)))
    return criados

def executar(doc, uidoc):
    try:
        lav = doc.GetElement(uidoc.Selection.PickObject(UI.Selection.ObjectType.Element, u"1/2: Selecione o aparelho"))
        alvo = doc.GetElement(uidoc.Selection.PickObject(UI.Selection.ObjectType.Element, u"2/2: Selecione o coletor"))
        
        curve = getattr(alvo.Location, "Curve", None)
        if not isinstance(curve, DB.Line):
            forms.alert(u"O destino precisa ser um tubo reto.")
            return
            
        lav_conn = escolher_saida_sanitaria(lav)
        if not lav_conn: return
        
        origem = lav_conn.Origin
        hdir = horizontal_dir(lav_conn)
        axis = pipe_axis(curve)
        if not axis: raise Exception(u"O coletor está em pé.")
        
        p0, p1, main_dir = axis
        fluxo = sentido_fluxo(curve)
        rotas = []
        
        if hdir is None:
            for turn in (45.0, -45.0, 135.0, -135.0):
                direction = rot2d(main_dir, turn)
                if direction.DotProduct(fluxo) < 0.2: continue
                hit = ray_hit(origem, direction, p0, p1, main_dir)
                if hit: rotas.append([origem, hit_point(curve, hit[0], origem.Z)])
        else:
            try: rotas = build_routes(origem, hdir, curve)
            except Exception:
                for turn in (45.0, -45.0, 135.0, -135.0):
                    direction = rot2d(main_dir, turn)
                    if direction.DotProduct(fluxo) < 0.2: continue
                    hit = ray_hit(origem, direction, p0, p1, main_dir)
                    if hit: rotas.append([origem, hit_point(curve, hit[0], origem.Z)])
                        
        if not rotas:
            forms.alert(u"Não foi possível achar caminho a 45° a favor do fluxo.")
            return
            
        rota = selecionar_rota(rotas, doc, uidoc)
        if not rota: return
        
        tipo_id = escolher_tipo_ramal(alvo.GetTypeId())
        if not tipo_id: return
        
        sistema_id = obter_sistema(alvo)
        nivel_id = nivel_de(alvo, doc)
        
        with Grupo(u"Ligação Esgoto: Direto -> Coletor"):
            criados = ramal_direto(lav, alvo, lav_conn.Id, rota, sistema_id, nivel_id, tipo_id, doc)
            
        uidoc.RefreshActiveView()
        log(u"**Pronto.** Tubos criados:\n\n{}".format(descrever(criados)))
        
    except Exception as exc:
        if not canceled(exc): forms.alert(u"Erro:\n{}".format(exc))