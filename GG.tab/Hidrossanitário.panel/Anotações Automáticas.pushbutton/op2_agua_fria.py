# -*- coding: utf-8 -*-
# Opcao 2: cotas (eixo dos tubos) + anotacao de diametro nos tubos de agua fria.
# Modificado para suportar cotas verticais e planos isométricos no 3D.
import math

from Autodesk.Revit.DB import (
    Transaction,
    TransactionGroup,
    XYZ,
    Line,
    Reference,
    ReferenceArray,
    Options,
    Solid,
    PlanarFace,
    Plane,
    SketchPlane,
    Wall,
    Dimension,
    DimensionType,
    IndependentTag,
    TagOrientation,
    BuiltInCategory,
    BuiltInParameter,
    Element,
    FilteredElementCollector,
    LocationCurve,
    ViewType,
    UnitUtils,
    UnitTypeId,
)
from Autodesk.Revit.DB.Plumbing import Pipe
from Autodesk.Revit.UI.Selection import ObjectType, ISelectionFilter
from Autodesk.Revit.Exceptions import OperationCanceledException
from pyrevit import forms, script

uidoc = None   
doc = None

# ---------- CONFIGURACOES ----------
PALAVRAS_AGUA = (u"agua fria", u"cold water")
TAG_DIAMETRO = (u"Anota\u00e7\u00e3o Tubos 3", u"DN")
TAG_DIAMETRO_ALT = (u"Anotacao_Tubo Inicial", u"Di\u00e2metro")
NOME_TIPO_COTA = None

COTA_OFFSET_MM = 15.0         
TOL_POS_CM = 1.0              
TOL_ANGULO_GRAUS = 3.0        
COTA_Z_3D = "topo"           
COTAR_PRUMADAS = True         

TAG_OFFSET_MM = 2.5           
TAG_COMPRIMENTO_MIN_CM = 30   
PULAR_JA_ANOTADOS = True      

TIPOS_DE_VISTA = (
    ViewType.FloorPlan,
    ViewType.CeilingPlan,
    ViewType.EngineeringPlan,
    ViewType.AreaPlan,
    ViewType.ThreeD,      
)

# ---------- AUXILIARES ----------
class FiltroParede(ISelectionFilter):
    def AllowElement(self, elem):
        return isinstance(elem, Wall)

    def AllowReference(self, reference, position):
        return True

def eid(element_id):
    try:
        return element_id.Value
    except AttributeError:
        return element_id.IntegerValue

_ACENTOS = dict(zip(u"\u00e1\u00e0\u00e2\u00e3\u00e4\u00e9\u00e8\u00ea\u00eb\u00ed\u00ec\u00ee\u00ef\u00f3\u00f2\u00f4\u00f5\u00f6\u00fa\u00f9\u00fb\u00fc\u00e7",
                   u"aaaaaeeeeiiiiooooouuuuc"))

def norm(s):
    s = u"{}".format(s or u"").lower()
    s = u"".join(_ACENTOS.get(c, c) for c in s)
    return u" ".join(s.split())

def type_name(elem):
    for bip in (BuiltInParameter.SYMBOL_NAME_PARAM, BuiltInParameter.ALL_MODEL_TYPE_NAME):
        try:
            p = elem.get_Parameter(bip)
            if p is not None and p.HasValue and p.AsString():
                return p.AsString()
        except Exception:
            pass
    try:
        return Element.Name.GetValue(elem)
    except Exception:
        return u""

def paper_to_model(mm, scale):
    return UnitUtils.ConvertToInternalUnits(mm * scale, UnitTypeId.Millimeters)

def cota_z(vw):
    lvl = getattr(vw, "GenLevel", None)
    return lvl.Elevation if lvl is not None else 0.0

def escolher_z_3d(zmin, zmax):
    if isinstance(COTA_Z_3D, (int, float)):
        return UnitUtils.ConvertToInternalUnits(COTA_Z_3D, UnitTypeId.Meters)
    return zmin if COTA_Z_3D == "base" else zmax

def is_agua(pipe):
    textos = []
    p = pipe.get_Parameter(BuiltInParameter.RBS_SYSTEM_CLASSIFICATION_PARAM)
    if p is not None:
        textos.append(p.AsString())
    p = pipe.get_Parameter(BuiltInParameter.RBS_PIPING_SYSTEM_TYPE_PARAM)
    if p is not None:
        textos.append(p.AsValueString())
    junto = norm(u" ".join(u"{}".format(t or u"") for t in textos))
    return any(norm(w) in junto for w in PALAVRAS_AGUA)

def pipe_radius(pipe):
    for bip in (BuiltInParameter.RBS_PIPE_OUTER_DIAMETER, BuiltInParameter.RBS_PIPE_DIAMETER_PARAM):
        p = pipe.get_Parameter(bip)
        if p is not None and p.HasValue and p.AsDouble() > 0:
            return p.AsDouble() / 2.0
    return 0.0

def info_tubo(pipe):
    loc = pipe.Location
    if not isinstance(loc, LocationCurve):
        return None
    c = loc.Curve
    if not isinstance(c, Line):
        return None
    p0, p1 = c.GetEndPoint(0), c.GetEndPoint(1)
    d = p1 - p0
    comp = d.GetLength()
    if comp < 1e-6:
        return None
    dn = d.Normalize()
    if abs(dn.Z) > 0.99:
        tipo = "V"
    else:
        ang = math.degrees(math.atan2(d.Y, d.X)) % 180.0
        if ang < TOL_ANGULO_GRAUS or ang > 180.0 - TOL_ANGULO_GRAUS:
            tipo = "X"
        elif abs(ang - 90.0) < TOL_ANGULO_GRAUS:
            tipo = "Y"
        else:
            tipo = "D"
    return {"pipe": pipe, "tipo": tipo, "p0": p0, "p1": p1, "mid": (p0 + p1) * 0.5, "dir": dn, "length": comp}

def unicos(lista, tol):
    out = []
    for pos, pipe in sorted(lista, key=lambda t: t[0]):
        if out and abs(pos - out[-1][0]) < tol:
            continue
        out.append((pos, pipe))
    return out

def melhor_face_parede(paredes, direcao, itens_pos, opts):
    melhor = None
    for w in paredes:
        loc = w.Location
        if not isinstance(loc, LocationCurve) or not isinstance(loc.Curve, Line):
            continue
        a, b = loc.Curve.GetEndPoint(0), loc.Curve.GetEndPoint(1)
        wd = XYZ(b.X - a.X, b.Y - a.Y, 0)
        if wd.GetLength() < 1e-9:
            continue
        wd = wd.Normalize()
        if abs(wd.DotProduct(direcao)) > 0.1:      
            continue
        geo = w.get_Geometry(opts)
        if not geo:
            continue
        for g in geo:
            if not isinstance(g, Solid):
                continue
            for f in g.Faces:
                if not isinstance(f, PlanarFace):
                    continue
                if abs(f.FaceNormal.Z) > 1e-6 or abs(f.FaceNormal.DotProduct(direcao)) < 0.99:
                    continue
                pos = f.Origin.DotProduct(direcao)
                dist = min(abs(pos - p) for p in itens_pos)
                if f.Reference is not None and (melhor is None or dist < melhor[0]):
                    melhor = (dist, pos, f.Reference, w)
    return melhor

def cotas_existentes(vw):
    out = set()
    for d in FilteredElementCollector(doc, vw.Id).OfClass(Dimension).ToElements():
        try:
            out.add(frozenset(eid(r.ElementId) for r in d.References))
        except Exception:
            continue
    return out

def tags_existentes(vw):
    found = set()
    for tag in FilteredElementCollector(doc, vw.Id).OfClass(IndependentTag).ToElements():
        try:
            type_id = eid(tag.GetTypeId())
            for tagged in tag.GetTaggedLocalElementIds():
                found.add((eid(tagged), type_id))
        except Exception:
            continue
    return found

def achar_tipo_cota():
    if not NOME_TIPO_COTA:
        return None
    for t in FilteredElementCollector(doc).OfClass(DimensionType).ToElements():
        if norm(type_name(t)) == norm(NOME_TIPO_COTA):
            return t
    return None

def carregar_tag():
    simbolos = (FilteredElementCollector(doc)
                .OfCategory(BuiltInCategory.OST_PipeTags)
                .WhereElementIsElementType()
                .ToElements())
    disponiveis = [(s.FamilyName, type_name(s), s) for s in simbolos]
    for fam, typ in (TAG_DIAMETRO, TAG_DIAMETRO_ALT):
        for f, t, s in disponiveis:
            if norm(f) == norm(fam) and norm(t) == norm(typ):
                return s
    print(u"Tipos de etiqueta de tubo disponiveis no projeto:")
    for f, t, _ in sorted(disponiveis, key=lambda x: (x[0], x[1])):
        print(u"  {} : {}".format(f, t))
    forms.alert(
        u"Etiqueta de di\u00e2metro n\u00e3o encontrada: {} : {}\n\nConfira TAG_DIAMETRO no in\u00edcio do op2_agua_fria.py".format(TAG_DIAMETRO[0], TAG_DIAMETRO[1]),
        title=u"Anotar Tubos",
        exitscript=True,
    )

# ---------- COTAS ----------
def criar_cota(vw, eixo, itens, paredes, opts, tol, coord_linha, coord_secundaria, dimtype, existentes, stats):
    """Cria a cota e rotaciona o plano de trabalho no 3D para deixá-la em pé."""
    if eixo == "X":
        direcao = XYZ(1, 0, 0)
    elif eixo == "Y":
        direcao = XYZ(0, 1, 0)
    else:
        direcao = XYZ(0, 0, 1)

    itens = unicos(itens, tol)
    if not itens:
        return

    pares = [(pos, Reference(pipe), eid(pipe.Id)) for pos, pipe in itens]

    if paredes and eixo != "Z":
        face = melhor_face_parede(paredes, direcao, [p for p, _ in itens], opts)
        if face is None:
            stats["avisos"].append(u"Nenhuma parede de refer\u00eancia perpendicular \u00e0 dire\u00e7\u00e3o {} (cota sem parede).".format(eixo))
        else:
            pares.append((face[1], face[2], eid(face[3].Id)))

    if len(pares) < 2:
        stats["avisos"].append(u"Cota em {}: s\u00f3 h\u00e1 1 refer\u00eancia (precisa de pelo menos 2).".format(eixo))
        return

    pares.sort(key=lambda t: t[0])
    chave = frozenset(p[2] for p in pares)
    if PULAR_JA_ANOTADOS and chave in existentes:
        stats["avisos"].append(u"Cota em {} j\u00e1 existe na vista (n\u00e3o repetida).".format(eixo))
        return

    ra = ReferenceArray()
    for _, ref, _ in pares:
        ra.Append(ref)
    
    a, b = pares[0][0], pares[-1][0]
    
    em_3d = (vw.ViewType == ViewType.ThreeD)
    plano_novo = None
    plano_antigo = vw.SketchPlane if em_3d else None
    
    # Define a linha de cota e o plano de trabalho específico para cada eixo para que fique alinhado
    if eixo == "X":
        linha = Line.CreateBound(XYZ(a, coord_linha, coord_secundaria), XYZ(b, coord_linha, coord_secundaria))
        if em_3d:
            plano_novo = SketchPlane.Create(doc, Plane.CreateByNormalAndOrigin(XYZ.BasisY, XYZ(0, coord_linha, coord_secundaria)))
    elif eixo == "Y":
        linha = Line.CreateBound(XYZ(coord_linha, a, coord_secundaria), XYZ(coord_linha, b, coord_secundaria))
        if em_3d:
            plano_novo = SketchPlane.Create(doc, Plane.CreateByNormalAndOrigin(XYZ.BasisX, XYZ(coord_linha, 0, coord_secundaria)))
    elif eixo == "Z":
        linha = Line.CreateBound(XYZ(coord_linha, coord_secundaria, a), XYZ(coord_linha, coord_secundaria, b))
        if em_3d:
            plano_novo = SketchPlane.Create(doc, Plane.CreateByNormalAndOrigin(XYZ.BasisX, XYZ(coord_linha, coord_secundaria, 0)))

    try:
        if em_3d and plano_novo:
            vw.SketchPlane = plano_novo
            
        if dimtype is not None:
            doc.Create.NewDimension(vw, linha, ra, dimtype)
        else:
            doc.Create.NewDimension(vw, linha, ra)
        stats["cotas_" + eixo.lower()] += 1
        existentes.add(chave)
    except Exception as e:
        stats["falhas"] += 1
        if stats["erro"] is None:
            stats["erro"] = u"{}".format(e)
    finally:
        # Restaura o plano antigo e apaga o temporário para não sujar o modelo
        if em_3d and plano_novo:
            if plano_antigo:
                vw.SketchPlane = plano_antigo
            try:
                doc.Delete(plano_novo.Id)
            except:
                pass


# ---------- ETIQUETAS ----------
def etiquetar_diametro(vw, infos, sym, existentes, z, stats):
    right, up = vw.RightDirection, vw.UpDirection
    em_3d = (vw.ViewType == ViewType.ThreeD)
    scale = vw.Scale
    comp_min = UnitUtils.ConvertToInternalUnits(TAG_COMPRIMENTO_MIN_CM, UnitTypeId.Centimeters)
    off = paper_to_model(TAG_OFFSET_MM, scale)
    try:
        if not sym.IsActive:
            sym.Activate()
    except Exception:
        pass
    for i in infos:
        if (i["tipo"] == "V" and not em_3d) or i["length"] < comp_min:
            continue
        pipe = i["pipe"]
        if PULAR_JA_ANOTADOS and (eid(pipe.Id), eid(sym.Id)) in existentes:
            continue
        d = i["dir"]
        du, dv = d.DotProduct(right), d.DotProduct(up)
        tam = math.sqrt(du * du + dv * dv)
        if tam < 1e-9:
            continue
        tu, tv = du / tam, dv / tam
        nu, nv = -tv, tu
        if abs(nv) < 0.1:
            nu, nv = -1.0, 0.0       
        elif nv < 0:
            nu, nv = -nu, -nv        
        n3 = right * nu + up * nv
        mid = i["mid"] if em_3d else XYZ(i["mid"].X, i["mid"].Y, z)
        ponto = mid + n3 * (pipe_radius(pipe) + off)
        try:
            IndependentTag.Create(doc, sym.Id, vw.Id, Reference(pipe), False, TagOrientation.Horizontal, ponto)
            stats["tags"] += 1
        except Exception as e:
            stats["falhas"] += 1
            if stats["erro"] is None:
                stats["erro"] = u"{}".format(e)


# ---------- PROCESSO ----------
def processar(vw, pipes, paredes, tag_sym):
    stats = {"cotas_x": 0, "cotas_y": 0, "cotas_z": 0, "tags": 0, "falhas": 0, "erro": None, "avisos": []}
    infos = [i for i in (info_tubo(p) for p in pipes) if i is not None]
    if not infos:
        stats["avisos"].append(u"Nenhum tubo de \u00e1gua fria reto encontrado.")
        return stats
    diag = len([i for i in infos if i["tipo"] == "D"])
    if diag:
        stats["avisos"].append(u"{} tubo(s) diagonal(is) em planta ficaram fora das cotas (s\u00f3 recebem etiqueta).".format(diag))

    em_3d = (vw.ViewType == ViewType.ThreeD)
    if em_3d:
        zs3 = [p.Z for i in infos for p in (i["p0"], i["p1"])]
        z = escolher_z_3d(min(zs3), max(zs3))
    else:
        z = cota_z(vw)
    scale = vw.Scale
    tol = UnitUtils.ConvertToInternalUnits(TOL_POS_CM, UnitTypeId.Centimeters)
    off = paper_to_model(COTA_OFFSET_MM, scale)

    pos_x, pos_y, pos_z = [], [], []     
    for i in infos:
        if i["tipo"] == "X":
            pos_y.append((i["mid"].Y, i["pipe"]))
            pos_z.append((i["mid"].Z, i["pipe"])) # Tubos horizontais servem de ref para cotas Z
        elif i["tipo"] == "Y":
            pos_x.append((i["mid"].X, i["pipe"]))
            pos_z.append((i["mid"].Z, i["pipe"])) # Tubos horizontais servem de ref para cotas Z
        elif i["tipo"] == "V" and COTAR_PRUMADAS:
            pos_x.append((i["mid"].X, i["pipe"]))
            pos_y.append((i["mid"].Y, i["pipe"]))

    xs = [p.X for i in infos for p in (i["p0"], i["p1"])]
    ys = [p.Y for i in infos for p in (i["p0"], i["p1"])]
    x_linha = min(xs) - off     
    y_linha = max(ys) + off     

    opts = Options()
    opts.ComputeReferences = True
    opts.View = vw
    dimtype = achar_tipo_cota()

    t = Transaction(doc, u"Cotas e anotacoes - agua fria")
    t.Start()
    plano_novo, plano_antigo = None, None
    try:
        if em_3d:
            plano_antigo = vw.SketchPlane
            # Mantém um plano horizontal base para as etiquetas (tags) funcionarem corretamente
            plano_novo = SketchPlane.Create(doc, Plane.CreateByNormalAndOrigin(XYZ.BasisZ, XYZ(0, 0, z)))
            vw.SketchPlane = plano_novo
            
        existentes_c = cotas_existentes(vw) if PULAR_JA_ANOTADOS else set()
        existentes_t = tags_existentes(vw) if PULAR_JA_ANOTADOS else set()
        
        # A própria função criar_cota agora ajusta os planos verticais temporariamente para X, Y e Z
        criar_cota(vw, "Y", pos_y, paredes, opts, tol, x_linha, z, dimtype, existentes_c, stats)
        criar_cota(vw, "X", pos_x, paredes, opts, tol, y_linha, z, dimtype, existentes_c, stats)
        if em_3d:
            criar_cota(vw, "Z", pos_z, paredes, opts, tol, x_linha, y_linha, dimtype, existentes_c, stats)
            
        etiquetar_diametro(vw, infos, tag_sym, existentes_t, z, stats)
        
        if plano_novo is not None:
            try:
                if plano_antigo is not None:
                    vw.SketchPlane = plano_antigo
                doc.Delete(plano_novo.Id)
            except Exception:
                pass
        t.Commit()
    except Exception as e:
        t.RollBack()
        return {"cotas_x": 0, "cotas_y": 0, "cotas_z": 0, "tags": 0, "falhas": 1, "erro": u"{}".format(e), "avisos": stats["avisos"]}
    return stats


def tubos_da_vista(vw):
    sel = [doc.GetElement(i) for i in uidoc.Selection.GetElementIds()]
    sel = [e for e in sel if isinstance(e, Pipe)]
    base = sel if sel else list(FilteredElementCollector(doc, vw.Id).OfClass(Pipe).ToElements())
    return [p for p in base if is_agua(p)]


def executar(doc_, uidoc_):
    global doc, uidoc
    doc = doc_
    uidoc = uidoc_

    vw = doc.ActiveView
    if vw.ViewType not in TIPOS_DE_VISTA:
        forms.alert(u"Abra uma vista de planta ou uma vista 3D travada para cotar e anotar os tubos.", exitscript=True)
    if vw.ViewType == ViewType.ThreeD and not vw.IsLocked:
        forms.alert(u"Trave a vista 3D (Salvar orienta\u00e7\u00e3o e travar vista) para poder cotar e anotar.", exitscript=True)

    tag_sym = carregar_tag()
    pipes = tubos_da_vista(vw)
    if not pipes:
        forms.alert(u"Nenhum tubo de \u00e1gua fria encontrado na vista ativa.", exitscript=True)

    escolha = forms.alert(
        u"Parede(s) de refer\u00eancia para as cotas?\n\nAs cotas v\u00e3o do eixo de cada tubo at\u00e9 a face da parede escolhida.",
        title=u"Anotar Tubos - \u00c1gua Fria",
        options=[u"Selecionar parede(s)", u"Sem parede de refer\u00eancia"],
    )
    if not escolha:
        script.exit()

    paredes = []
    if escolha == u"Selecionar parede(s)":
        try:
            refs = uidoc.Selection.PickObjects(
                ObjectType.Element, FiltroParede(),
                u"Selecione a(s) parede(s) de refer\u00eancia e clique em Concluir")
        except OperationCanceledException:
            script.exit()
        paredes = [doc.GetElement(r) for r in refs]

    tg = TransactionGroup(doc, u"Anotar tubos - \u00e1gua fria")
    tg.Start()
    try:
        s = processar(vw, pipes, paredes, tag_sym)
        if s["cotas_x"] + s["cotas_y"] + s["cotas_z"] + s["tags"] > 0:
            tg.Assimilate()
        else:
            tg.RollBack()
    except Exception as e:
        tg.RollBack()
        forms.alert(u"Erro ao anotar os tubos:\n{}".format(e), exitscript=True)

    msg = (u"Cotas em Y (linha vertical): {}\nCotas em X (linha horizontal): {}\nCotas em Z (alturas): {}\nEtiquetas de di\u00e2metro: {}"
           .format(s["cotas_y"], s["cotas_x"], s["cotas_z"], s["tags"]))
    if not paredes:
        msg += u"\n\n(Sem parede de refer\u00eancia: cotas apenas entre os tubos.)"
    if s["avisos"]:
        msg += u"\n\nAvisos:\n- " + u"\n- ".join(s["avisos"])
    if s["falhas"]:
        msg += u"\n\nFalhas: {}\nPrimeiro erro: {}".format(s["falhas"], s["erro"])
    forms.alert(msg, title=u"Anotar Tubos - \u00c1gua Fria")