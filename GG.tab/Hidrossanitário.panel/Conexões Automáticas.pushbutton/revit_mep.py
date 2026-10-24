# -*- coding: utf-8 -*-
import math
from pyrevit import DB, revit

from geom_utils import *

def type_name(elem):
    if elem is None: return u""
    for bip in (DB.BuiltInParameter.SYMBOL_NAME_PARAM, DB.BuiltInParameter.ALL_MODEL_TYPE_NAME):
        try:
            param = elem.get_Parameter(bip)
            if param is not None and param.HasValue:
                value = param.AsString()
                if value: return value
        except Exception: pass
    try: return DB.Element.Name.GetValue(elem)
    except Exception: return u"(sem nome)"

def element_label(elem):
    symbol = getattr(elem, "Symbol", None)
    return u"{} : {}".format(symbol.FamilyName, type_name(symbol)) if symbol else type_name(elem)

def achar_por_nome(classe, nome):
    for elem in DB.FilteredElementCollector(revit.doc).OfClass(classe):
        if type_name(elem) == nome: return elem
    return None

def iter_connectors(elem):
    manager = None
    if isinstance(elem, DB.MEPCurve): manager = elem.ConnectorManager
    else:
        mep = getattr(elem, "MEPModel", None)
        if mep is not None: manager = mep.ConnectorManager
    if manager is None: return
    for conn in manager.Connectors: yield conn

def end_piping(elem, only_open=False):
    return [c for c in iter_connectors(elem) if c.ConnectorType == DB.ConnectorType.End and c.Domain == DB.Domain.DomainPiping and (not only_open or not c.IsConnected)]

def connector_near(elem, pt, tol):
    best, best_d = None, None
    for conn in end_piping(elem):
        dist = conn.Origin.DistanceTo(pt)
        if dist <= tol and (best_d is None or dist < best_d): best, best_d = conn, dist
    return best, best_d

def connector_by_id(elem, cid):
    for conn in iter_connectors(elem):
        if conn.Id == cid: return conn
    return None

def ponta_livre(fitting):
    abertos = [c for c in iter_connectors(fitting) if c.Domain == DB.Domain.DomainPiping and not c.IsConnected]
    if len(abertos) != 1: raise Exception(u"A junção ficou com {} pontas livres.".format(len(abertos)))
    return abertos[0]

def flow_label(conn):
    if conn.Direction == DB.FlowDirectionType.Out: return u"Saída"
    if conn.Direction == DB.FlowDirectionType.In: return u"Entrada"
    return u"Bidirecional"

def set_diameter(pipe, diameter):
    param = pipe.get_Parameter(DB.BuiltInParameter.RBS_PIPE_DIAMETER_PARAM)
    if param is None or param.IsReadOnly: raise Exception(u"O diâmetro do tubo está bloqueado.")
    param.Set(diameter)

def make_pipe(system_type_id, pipe_type_id, level_id, p1, p2, diameter):
    pipe = DB.Plumbing.Pipe.Create(revit.doc, system_type_id, pipe_type_id, level_id, p1, p2)
    try: set_diameter(pipe, diameter)
    except Exception:
        revit.doc.Delete(pipe.Id)
        raise
    return pipe

def conectar(fixo, ponta):
    dist = to_mm(fixo.Origin.DistanceTo(ponta.Origin))
    dot = fixo.CoordinateSystem.BasisZ.DotProduct(ponta.CoordinateSystem.BasisZ)
    if dist > 15: raise Exception(u"A ponta do tubo ficou a {:.1f} mm do conector.".format(dist))
    if dot > -0.85: raise Exception(u"O tubo não chega na direção do conector (produto escalar {:.2f}).".format(dot))
    fixo.ConnectTo(ponta)

def place_elbow(pipe_a, pipe_b, pt):
    revit.doc.Regenerate()
    c1, d1 = connector_near(pipe_a, pt, mm(35))
    c2, d2 = connector_near(pipe_b, pt, mm(35))
    if not c1 or not c2: raise Exception(u"Não achei as pontas da curva.")
    try: return revit.doc.Create.NewElbowFitting(c1, c2)
    except Exception as exc: raise Exception(u"A curva entre os trechos falhou.\n{}".format(exc))

def achar_param_angulo(fitting):
    nomes = (u"angle", u"ângulo", u"angulo")
    for param in fitting.Parameters:
        try:
            if param.StorageType == DB.StorageType.Double and not param.IsReadOnly and param.Definition.Name.strip().lower() in nomes and abs(param.AsDouble() - math.pi / 2.0) < 0.02:
                return param
        except Exception: pass
    return None

def criar_juncao_y(main_id, hit, travel, dia, system_type_id, level_id, tipos_guia):
    doc = revit.doc
    main = doc.GetElement(main_id)
    curve = main.Location.Curve
    p0, p1 = curve.GetEndPoint(0), curve.GetEndPoint(1)
    axis = (p1 - p0).Normalize()
    if p0.Z < p1.Z - 1e-9: axis = axis.Negate()
    cosang = axis.DotProduct(travel)
    if cosang < 0.2: raise Exception(u"O ramal chegaria contra o fluxo do coletor.")
    
    theta = math.acos(min(1.0, cosang))
    guia_dir = (travel - axis.Multiply(cosang)).Normalize()
    inicio = hit - guia_dir.Multiply(COMPRIMENTO_GUIA)
    
    guia, erros = None, []
    for tipo_id in tipos_guia:
        try:
            guia = make_pipe(system_type_id, tipo_id, level_id, inicio, hit, dia)
            break
        except Exception as exc: erros.append(u"{}".format(exc))
    if not guia: raise Exception(u"Não consegui criar o tubo-guia da junção:\n{}".format(u"\n".join(erros)))
    
    new_id = DB.Plumbing.PlumbingUtils.BreakCurve(doc, main_id, hit)
    doc.Regenerate()
    
    c1, _ = connector_near(doc.GetElement(main_id), hit, mm(40))
    c2, _ = connector_near(doc.GetElement(new_id), hit, mm(40))
    cg, _ = connector_near(guia, hit, mm(40))
    if not c1 or not c2 or not cg: raise Exception(u"Conectores fora do ponto da junção depois de partir o coletor.")
    
    tee, erros = None, []
    for ordem in ((c1, c2), (c2, c1)):
        try:
            tee = doc.Create.NewTeeFitting(ordem[0], ordem[1], cg)
            break
        except Exception as exc: erros.append(u"{}".format(exc))
    if not tee: raise Exception(u"O tê a 90° não foi criado.")
    
    tee_id = tee.Id
    doc.Delete(guia.Id)
    doc.Regenerate()
    
    tee = doc.GetElement(tee_id)
    param = achar_param_angulo(tee)
    if not param: raise Exception(u"A família da junção ({}) não tem parâmetro de ângulo editável.".format(element_label(tee)))
    param.Set(theta)
    doc.Regenerate()
    
    if ponta_livre(tee).CoordinateSystem.BasisZ.DotProduct(travel.Negate()) < 0.9995: raise Exception(u"A perna do ramal da junção ficou virada para o lado errado.")
    return tee, theta

def renomear_juncao_45(tee, theta):
    doc = revit.doc
    try:
        if abs(math.degrees(theta) - 45.0) > 3.0: return
        alvo = None
        for sid in tee.Symbol.Family.GetFamilySymbolIds():
            sym = doc.GetElement(sid)
            nome = type_name(sym)
            if u"45" in nome and u"90" not in nome: alvo = sym
        if not alvo or alvo.Id == tee.GetTypeId(): return
        
        trans = DB.Transaction(doc, u"Tipo da junção 45°")
        trans.Start()
        try:
            if not alvo.IsActive:
                alvo.Activate()
                doc.Regenerate()
            tee.Symbol = alvo
            doc.Regenerate()
            if len([c for c in iter_connectors(tee) if c.IsConnected]) != 2: raise Exception(u"desconectou")
            trans.Commit()
        except Exception: trans.RollBack()
    except Exception: pass

def connector_kind(conn, curve, offset):
    axis = pipe_axis(curve)
    hdir = horizontal_dir(conn)
    if not axis or not hdir: return None, 0.0
    p0, p1, main_dir = axis
    ang = folded_deg(hdir, main_dir)
    origem = conn.Origin + offset
    if abs(ang - 45.0) <= TOL_45 and hdir.DotProduct(sentido_fluxo(curve)) > 0.2 and ray_hit(origem, hdir, p0, p1, main_dir): 
        return "direto", 100.0 - abs(ang - 45.0)
    if 18.0 < ang < 72.0: 
        return "joelho", 40.0 - min(ang, 90.0 - ang)
    return None, 0.0

def build_routes(origin, hdir, curve):
    axis = pipe_axis(curve)
    if not axis: raise Exception(u"O coletor está em pé.")
    p0, p1, main_dir = axis
    fluxo = sentido_fluxo(curve)
    ang = folded_deg(hdir, main_dir)
    
    if ray_hit(origin, hdir, p0, p1, main_dir) and abs(ang - 45.0) <= TOL_45:
        if hdir.DotProduct(fluxo) < 0.2: raise Exception(u"A saída aponta contra o fluxo do coletor.")
        return [[origin, hit_point(curve, ray_hit(origin, hdir, p0, p1, main_dir)[0], origin.Z)]]
        
    if 18.0 < ang < 72.0: raise Exception(u"A saída faz {:.0f}° com o coletor. Ela precisa estar paralela ou perpendicular.".format(ang))
    
    toward = xy(p0) + main_dir * ((xy(origin) - xy(p0)).DotProduct(main_dir))
    dist0 = xy(origin).DistanceTo(toward)
    stub_len = min(mm(150), max(mm(50), dist0 - mm(80))) if hdir.DotProduct((toward - xy(origin)).Normalize()) > 0.2 else mm(150)
    
    rotas = []
    for extra in (0.0, mm(150), mm(300)):
        stub_end = origin + DB.XYZ(hdir.X, hdir.Y, 0) * (stub_len + extra)
        for turn in (45.0, -45.0, 135.0, -135.0):
            direction = rot2d(main_dir, turn)
            if abs(folded_deg(direction, hdir) - 45.0) > 15.0 or direction.DotProduct(fluxo) < 0.2: continue
            hit = ray_hit(stub_end, direction, p0, p1, main_dir)
            if hit: rotas.append([origin, stub_end, hit_point(curve, hit[0], origin.Z)])
            
    if not rotas: raise Exception(u"O joelho de 45° não encontra o coletor. Estenda o coletor.")
    return rotas

def sistema_do(elem):
    param = elem.get_Parameter(DB.BuiltInParameter.RBS_PIPING_SYSTEM_TYPE_PARAM)
    return param.AsElementId() if param and param.HasValue and param.AsElementId() != DB.ElementId.InvalidElementId else None

def obter_sistema(elem):
    sis_id = sistema_do(elem)
    if sis_id: return sis_id
    achado = achar_por_nome(DB.Plumbing.PipingSystemType, NOME_SISTEMA)
    if achado: return achado.Id
    raise Exception(u"Não achei o sistema '{}'.".format(NOME_SISTEMA))

def nivel_de(elem, doc):
    nivel = getattr(elem, "ReferenceLevel", None)
    if nivel: return nivel.Id
    try:
        if elem.LevelId != DB.ElementId.InvalidElementId: return elem.LevelId
    except Exception: pass
    return doc.ActiveView.GenLevel.Id

def descrever(pipes):
    linhas = []
    for pipe in pipes:
        c = pipe.Location.Curve
        a, b = c.GetEndPoint(0), c.GetEndPoint(1)
        hl = xy(a).DistanceTo(xy(b))
        if hl < mm(1): linhas.append(u"- Ø {:.0f} mm | {:.0f} mm | vertical".format(to_mm(pipe.Diameter), to_mm(c.Length)))
        else: linhas.append(u"- Ø {:.0f} mm | {:.0f} mm | planta {:.1f}° | queda {:.2f}%".format(to_mm(pipe.Diameter), to_mm(c.Length), math.degrees(math.atan2(b.Y - a.Y, b.X - a.X)), (a.Z - b.Z) / hl * 100.0))
    return u"\n".join(linhas)

def draw_preview(doc, pt1, pt2):
    view_z = doc.ActiveView.Origin.Z
    a, b = DB.XYZ(pt1.X, pt1.Y, view_z), DB.XYZ(pt2.X, pt2.Y, view_z)
    if a.DistanceTo(b) < mm(5): return None
    return doc.Create.NewDetailCurve(doc.ActiveView, DB.Line.CreateBound(a, b))

def delete_ids(doc, ids):
    pending = [eid for eid in ids if eid and eid != DB.ElementId.InvalidElementId]
    if not pending: return
    with revit.Transaction(doc, u"Limpar prévias"):
        for eid in pending:
            try: doc.Delete(eid)
            except Exception: pass

class Grupo(object):
    def __init__(self, nome): self._grupo = DB.TransactionGroup(revit.doc, nome)
    def __enter__(self):
        self._grupo.Start()
        return self
    def __exit__(self, exc_type, exc_value, tb):
        if exc_type is None: self._grupo.Assimilate()
        else: self._grupo.RollBack()
        return False