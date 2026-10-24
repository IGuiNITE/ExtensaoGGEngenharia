# -*- coding: utf-8 -*-
__title__ = "Cotar Lote\nAuto"
__doc__ = """Cota automaticamente múltiplas paredes selecionadas de uma só vez.
Paredes horizontais são cotadas para cima e verticais para a esquerda."""
__author__ = "Guilherme Geraldo da Silva"

import clr
import traceback

from Autodesk.Revit.DB import (
    Transaction,
    Line,
    XYZ,
    ReferenceArray,
    Options,
    Solid,
    PlanarFace,
    Wall,
    LocationCurve,
    FilteredElementCollector,
    ViewType,
    UnitUtils,
    UnitTypeId,
)
from Autodesk.Revit.UI.Selection import ObjectType, ISelectionFilter
from Autodesk.Revit.Exceptions import OperationCanceledException
from pyrevit import forms

# ---------- RESTAURADO: O seu método à prova de falhas ----------
uidoc = __revit__.ActiveUIDocument
doc = uidoc.Document
view = doc.ActiveView

# ---------- CONFIGURAÇÕES ----------
INCLUIR_PAREDES_TRANSVERSAIS = True
OFFSET_CM = 80 
TOLERANCIA_CM = 2
TOLERANCIA_POSICAO = 0.005

# CLASSES ==================================================
class WallSelectionFilter(ISelectionFilter):
    def AllowElement(self, elem):
        return isinstance(elem, Wall)
    def AllowReference(self, reference, position):
        return True

# FUNÇÕES ==================================================
def flat(p):
    return XYZ(p.X, p.Y, 0)

def wall_segment(wall):
    loc = wall.Location
    if not isinstance(loc, LocationCurve): return None
    curve = loc.Curve
    if not isinstance(curve, Line): return None
    return flat(curve.GetEndPoint(0)), flat(curve.GetEndPoint(1))

def direction(p0, p1):
    v = p1 - p0
    if v.GetLength() < 1e-9: return None
    return v.Normalize()

def point_segment_dist(p, a, b):
    ab = b - a
    l2 = ab.DotProduct(ab)
    if l2 == 0: return p.DistanceTo(a)
    t = max(0.0, min(1.0, (p - a).DotProduct(ab) / l2))
    return p.DistanceTo(a + ab * t)

def segments_intersect(a0, a1, b0, b1):
    def cross(o, p, q):
        return (p.X - o.X) * (q.Y - o.Y) - (p.Y - o.Y) * (q.X - o.X)
    d1 = cross(b0, b1, a0)
    d2 = cross(b0, b1, a1)
    d3 = cross(a0, a1, b0)
    d4 = cross(a0, a1, b1)
    return (d1 * d2 < 0) and (d3 * d4 < 0)

def segment_distance(a0, a1, b0, b1):
    if segments_intersect(a0, a1, b0, b1): return 0.0
    return min(
        point_segment_dist(a0, b0, b1),
        point_segment_dist(a1, b0, b1),
        point_segment_dist(b0, a0, a1),
        point_segment_dist(b1, a0, a1),
    )

def find_crossing_walls(main_walls, line_dir, normal_dir):
    tol = UnitUtils.ConvertToInternalUnits(TOLERANCIA_CM, UnitTypeId.Centimeters)
    main_ids = [w.Id for w in main_walls]

    mains = []
    for w in main_walls:
        seg = wall_segment(w)
        if not seg: continue
        d = direction(seg[0], seg[1])
        if d is not None and abs(d.DotProduct(line_dir)) > 0.99:
            mains.append((seg, w.Width / 2.0 + tol))

    found = []
    if not mains: return found

    candidates = FilteredElementCollector(doc, view.Id).OfClass(Wall).ToElements()

    for cand in candidates:
        if cand.Id in main_ids: continue
        seg = wall_segment(cand)
        if not seg: continue
        d = direction(seg[0], seg[1])
        if d is None or abs(d.DotProduct(normal_dir)) < 0.99: continue

        for (m0, m1), reach in mains:
            if segment_distance(seg[0], seg[1], m0, m1) <= reach:
                found.append(cand)
                break

    return found

# MAIN ==================================================
try:
    if view.ViewType not in (ViewType.FloorPlan, ViewType.CeilingPlan, ViewType.EngineeringPlan, ViewType.AreaPlan):
        forms.alert("Abra uma vista de planta para criar cotas.", exitscript=True)

    try:
        wall_refs = uidoc.Selection.PickObjects(
            ObjectType.Element,
            WallSelectionFilter(),
            "Selecione MÚLTIPLAS paredes e clique em Concluir"
        )
    except OperationCanceledException:
        forms.alert("Operação cancelada.", exitscript=True)

    selected_walls = [doc.GetElement(ref) for ref in wall_refs]
    if not selected_walls:
        forms.alert("Nenhuma parede selecionada.", exitscript=True)

    options = Options()
    options.ComputeReferences = True
    options.View = view

    offset_interno = UnitUtils.ConvertToInternalUnits(OFFSET_CM, UnitTypeId.Centimeters)
    sucesso_cotas = 0

    t = Transaction(doc, "Cotar Paredes em Lote")
    t.Start()

    try:
        for wall in selected_walls:
            seg = wall_segment(wall)
            if not seg: continue
            
            p0, p1 = seg
            v = direction(p0, p1)
            if v is None: continue

            # Descobre se a parede é preponderantemente Horizontal ou Vertical
            is_horizontal = abs(v.X) >= abs(v.Y)

            # Determina a normal real (perpendicular à parede)
            true_normal = XYZ(-v.Y, v.X, 0).Normalize()

            # REGRAS DE ORIENTAÇÃO
            if is_horizontal:
                # Força o vetor da cota ir para a direita, e a normal ir para CIMA (+Y)
                if v.X < 0: v = v.Negate()
                if true_normal.Y < 0: true_normal = true_normal.Negate()
            else:
                # Força o vetor da cota ir para cima, e a normal ir para a ESQUERDA (-X)
                if v.Y < 0: v = v.Negate()
                if true_normal.X > 0: true_normal = true_normal.Negate()

            line_dir = v
            normal_dir = true_normal

            # Aplica o offset calculado a partir do meio da parede
            midpoint = (p0 + p1) / 2.0
            dim_origin = midpoint + normal_dir * offset_interno
            line = Line.CreateBound(dim_origin, dim_origin + line_dir)

            # Coleta referências (Parede atual + Transversais)
            group = [wall]
            if INCLUIR_PAREDES_TRANSVERSAIS:
                group.extend(find_crossing_walls([wall], line_dir, normal_dir))

            references = ReferenceArray()
            list_pos = []

            for w in group:
                geometry = w.get_Geometry(options)
                if not geometry: continue

                for geo in geometry:
                    if isinstance(geo, Solid):
                        for f in geo.Faces:
                            if isinstance(f, PlanarFace):
                                if abs(f.FaceNormal.Z) < 1e-6:
                                    dot = abs(f.FaceNormal.DotProduct(line_dir))
                                    if dot > 0.99:
                                        pos = f.Origin.DotProduct(line_dir)
                                        if not any(abs(pos - p) < TOLERANCIA_POSICAO for p in list_pos):
                                            references.Append(f.Reference)
                                            list_pos.append(pos)

            if references.Size >= 2:
                try:
                    doc.Create.NewDimension(view, line, references)
                    sucesso_cotas += 1
                except Exception:
                    pass

        t.Commit()
    except Exception as e:
        t.RollBack()
        forms.alert("Erro ao processar o lote:\n{}".format(traceback.format_exc()))

    if sucesso_cotas == 0:
        forms.alert("Nenhuma cota pôde ser gerada. Verifique se as paredes possuem intersecções válidas.")

except Exception as global_error:
    forms.alert("Erro Fatal no Script:\n\n{}".format(traceback.format_exc()))