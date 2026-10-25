# -*- coding: utf-8 -*-
import math
from pyrevit import DB, UI, forms, revit

from geom_utils import *
from revit_mep import *
from ui_utils import *

def ramal_coletor(caixa, alvo, outlet_id, rota, sistema_id, nivel_id, tipo_id, doc):
    outlet = connector_by_id(caixa, outlet_id)
    hit = rota[-1]
    dz = (hit.Z + DECLIVE * comprimento_planta(rota)) - outlet.Origin.Z
    
    if abs(dz) > mm(0.01):
        with revit.Transaction(u"Ajustar altura da caixa"):
            DB.ElementTransformUtils.MoveElement(doc, caixa.Id, DB.XYZ(0, 0, dz))
        doc.Regenerate()
        log(u"Caixa ajustada **{:+.1f} mm** na altura.".format(to_mm(dz)))
        
    caixa_fresca = doc.GetElement(caixa.Id)
    outlet = connector_by_id(caixa_fresca, outlet_id)
    dia = outlet.Radius * 2.0
    anterior = rota[-2]
    travel = DB.XYZ((hit.X - anterior.X), (hit.Y - anterior.Y), 0).Normalize()
    travel = DB.XYZ(travel.X, travel.Y, -DECLIVE).Normalize()
    
    projecao = alvo.Location.Curve.Project(hit)
    if projecao: hit = projecao.XYZPoint
    
    with revit.Transaction(u"Junção Y no coletor"):
        tee, theta = criar_juncao_y(alvo.Id, hit, travel, dia, sistema_id, nivel_id, [alvo.GetTypeId(), tipo_id])
        tee_id = tee.Id
        
    tee = doc.GetElement(tee_id)
    renomear_juncao_45(tee, theta)
    cb = ponta_livre(doc.GetElement(tee_id))
    
    pontos = [outlet.Origin]
    for p in rota[1:-1]:
        pontos.append(DB.XYZ(p.X, p.Y, hit.Z + DECLIVE * xy(p).DistanceTo(xy(hit))))
    pontos.append(cb.Origin)
    
    if xy(pontos[-2]).DistanceTo(xy(pontos[-1])) < TRECHO_FINAL_MIN: raise Exception(u"Sem folga pro coletor.")
        
    with revit.Transaction(u"Ramal da caixa até o coletor"):
        criados = []
        for i in range(len(pontos) - 1): 
            criados.append(make_pipe(sistema_id, tipo_id, nivel_id, pontos[i], pontos[i + 1], dia))
        doc.Regenerate()
        
        c_ini, _ = connector_near(criados[0], pontos[0], mm(25))
        c_fim, _ = connector_near(criados[-1], pontos[-1], mm(25))
        
        conectar(outlet, c_ini)
        for i in range(len(criados) - 1): place_elbow(criados[i], criados[i + 1], pontos[i + 1])
        conectar(cb, c_fim)
        
    log(u"Junção Y: **{}** ({:.2f}°).".format(element_label(tee), math.degrees(theta)))
    return criados

def executar(doc, uidoc):
    try:
        caixa = doc.GetElement(uidoc.Selection.PickObject(UI.Selection.ObjectType.Element, u"1/2: Selecione a caixa sifonada"))
        alvo = doc.GetElement(uidoc.Selection.PickObject(UI.Selection.ObjectType.Element, u"2/2: Selecione o coletor"))
        
        curve = getattr(alvo.Location, "Curve", None)
        if not isinstance(curve, DB.Line):
            forms.alert(u"O destino precisa ser um tubo reto.")
            return
            
        outlet = choose_outlet(caixa, curve, DB.XYZ.Zero)
        if not outlet: return
        
        hdir = horizontal_dir(outlet)
        if not hdir: raise Exception(u"Saída escolhida é vertical.")
        
        rotas = build_routes(outlet.Origin, hdir, curve)
        rota = selecionar_rota(rotas, doc, uidoc)
        if not rota: return
        
        tipo_id = escolher_tipo_ramal(alvo.GetTypeId())
        if not tipo_id: return
        
        sistema_id = obter_sistema(alvo)
        nivel_id = nivel_de(alvo, doc)
        
        with Grupo(u"Ligação Esgoto: Caixa -> Coletor"):
            criados = ramal_coletor(caixa, alvo, outlet.Id, rota, sistema_id, nivel_id, tipo_id, doc)
            
        uidoc.RefreshActiveView()
        log(u"**Pronto.** Tubos criados:\n\n{}".format(descrever(criados)))
        
    except Exception as exc:
        if not canceled(exc): forms.alert(u"Erro:\n{}".format(exc))