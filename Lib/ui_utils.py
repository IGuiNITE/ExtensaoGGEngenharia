# -*- coding: utf-8 -*-
from pyrevit import forms, DB, UI, revit, script

from geom_utils import *
from revit_mep import *

MOSTRAR_PREVIAS = False
TOLERANCIA_PREVIA = 0.50
CORES_PREVIA = [(u"vermelha", DB.Color(255, 0, 0)),
                (u"verde", DB.Color(0, 160, 0)),
                (u"azul", DB.Color(0, 90, 255))]

def log(message):
    script.get_output().print_md(message)

def canceled(exc):
    return "OperationCanceled" in exc.__class__.__name__

def colorir_linha(doc, linha, cor):
    ogs = DB.OverrideGraphicSettings()
    ogs.SetProjectionLineColor(cor)
    ogs.SetProjectionLineWeight(8)
    doc.ActiveView.SetElementOverrides(linha.Id, ogs)

def selecionar_rota(rotas, doc, uidoc):
    rotas_ordenadas = sorted(rotas, key=comprimento_planta)
    if not MOSTRAR_PREVIAS: return rotas_ordenadas[0]
    
    limite_comp = comprimento_planta(rotas_ordenadas[0]) * (1.0 + TOLERANCIA_PREVIA)
    rotas_filtradas = [r for r in rotas_ordenadas if comprimento_planta(r) <= limite_comp][:3]

    opcoes = []
    cache = {}
    preview_ids = []
    
    try:
        with revit.Transaction(doc, u"Gerar prévias"):
            for idx, route in enumerate(rotas_filtradas):
                nome_cor, cor = CORES_PREVIA[idx]
                linhas = []
                for i in range(len(route) - 1):
                    a, b = route[i], route[i + 1]
                    chave = tuple(sorted([(round(to_mm(a.X), 1), round(to_mm(a.Y), 1)), (round(to_mm(b.X), 1), round(to_mm(b.Y), 1))]))
                    if chave not in cache: cache[chave] = draw_preview(doc, a, b)
                    if cache[chave]: linhas.append(cache[chave])
                if linhas:
                    colorir_linha(doc, linhas[-1], cor)
                    opcoes.append((linhas[-1], nome_cor, route, linhas))
            preview_ids = [ln.Id for ln in cache.values() if ln]
            
        uidoc.RefreshActiveView()
        if not opcoes:
            forms.alert(u"Não foi possível desenhar a prévia.")
            return None

        if len(opcoes) == 1:
            motivo = u"Só existe 1 rota possível." if len(rotas_ordenadas) == 1 else u"Com tolerância, só restou 1 opção."
            if forms.alert(motivo + u"\nA opção está destacada em {}. Criar o tubo?".format(opcoes[0][1]), yes=True, no=True):
                return opcoes[0][2]
            return None

        nomes = u", ".join(u"{} = {}".format(i + 1, o[1]) for i, o in enumerate(opcoes))
        ref_line = uidoc.Selection.PickObject(UI.Selection.ObjectType.Element, u"Clique na diagonal da rota desejada ({})".format(nomes))
        
        for linha_final, nome_cor, route, linhas in opcoes:
            if any(ln.Id == ref_line.ElementId for ln in linhas):
                return route
                
        forms.alert(u"Clique na prévia, não no tubo nem na caixa.")
        return None
    finally:
        delete_ids(doc, preview_ids)
        uidoc.RefreshActiveView()

def escolher_saida_lavatorio(elem):
    livres = [c for c in end_piping(elem, only_open=True) if c.PipeSystemType == DB.Plumbing.PipeSystemType.Sanitary and c.CoordinateSystem.BasisZ.Z <= -0.9]
    if not livres:
        forms.alert(u"O lavatório não tem saída de esgoto livre apontando para baixo.")
        return None
    if len(livres) == 1: return livres[0]
    rotulos = [u"{:02d}. {} | Ø {:.0f} mm".format(i + 1, flow_label(c), to_mm(c.Radius * 2.0)) for i, c in enumerate(livres)]
    picked = forms.SelectFromList.show(rotulos, title=u"Qual saída do lavatório?", button_name=u"Usar esta saída", multiselect=False)
    return livres[int(picked.split(".")[0]) - 1] if picked else None

def escolher_saida_sanitaria(elem):
    livres = [c for c in end_piping(elem, only_open=True) if c.PipeSystemType == DB.Plumbing.PipeSystemType.Sanitary]
    if not livres:
        forms.alert(u"O aparelho não tem saída de esgoto livre.")
        return None
    if len(livres) == 1: return livres[0]
    rotulos = [u"{:02d}. {} | Ø {:.0f} mm".format(i + 1, flow_label(c), to_mm(c.Radius * 2.0)) for i, c in enumerate(livres)]
    picked = forms.SelectFromList.show(rotulos, title=u"Qual saída do aparelho?", button_name=u"Usar esta saída", multiselect=False)
    return livres[int(picked.split(".")[0]) - 1] if picked else None

def escolher_tipo_ramal(fallback_id):
    import revit_mep
    tipo = achar_por_nome(DB.Plumbing.PipeType, NOME_TIPO_RAMAL)
    if tipo is not None: return tipo.Id
    if fallback_id is not None: return fallback_id
    tipos = sorted(DB.FilteredElementCollector(revit.doc).OfClass(DB.Plumbing.PipeType), key=type_name)
    nomes = [type_name(t) for t in tipos]
    picked = forms.SelectFromList.show(nomes, title=u"Tipo de tubo dos ramais", button_name=u"Usar este tipo", multiselect=False)
    return tipos[nomes.index(picked)].Id if picked else None

def choose_outlet(elem, curve, offset):
    conns = end_piping(elem, only_open=True)
    if not conns:
        forms.alert(u"A caixa não tem conector de tubulação livre.")
        return None
        
    saidas = [c for c in conns if c.Direction == DB.FlowDirectionType.Out]
    if saidas: conns = saidas
    
    ranked = []
    for conn in conns:
        kind, score = connector_kind(conn, curve, offset)
        ranked.append((score, kind, conn))
        
    ranked.sort(key=lambda item: item[0], reverse=True)
    
    diretos = [item for item in ranked if item[1] == "direto"]
    joelhos = [item for item in ranked if item[1] == "joelho"]
    pool = diretos if diretos else joelhos
    
    if not pool:
        forms.alert(u"Nenhuma saída livre está a 45° do coletor (a favor do fluxo), nem paralela, nem perpendicular.\nGire a caixa para uma dessas posições.")
        return None
        
    if len(pool) == 1: return pool[0][2]
    
    labels = [u"{:02d}. {} | Ø {:.0f} mm".format(i + 1, flow_label(c[2]), to_mm(c[2].Radius * 2.0)) for i, c in enumerate(pool)]
    picked = forms.SelectFromList.show(labels, title=u"Qual saída usar?", button_name=u"Usar este conector", multiselect=False)
    
    return pool[int(picked.split(".")[0]) - 1][2] if picked else None