# -*- coding: utf-8 -*-
__title__ = "Gerar talude\nAutomático"
__doc__ = "Gera talude e registra cotas do terreno natural antes da edicao."
__author__ = "Guilherme Geraldo da Silva"

import os
import json
import math
import uuid
import datetime

import clr
clr.AddReference("RevitAPI")
clr.AddReference("RevitAPIUI")

from Autodesk.Revit.DB import (XYZ, Options, Solid, PlanarFace, View3D, ViewDetailLevel,
    ReferenceIntersector, FindReferenceTarget, Transaction, TransactionStatus,
    CurveLoop, Toposolid)
from Autodesk.Revit.UI.Selection import ObjectType, ISelectionFilter
from Autodesk.Revit.Exceptions import OperationCanceledException
from pyrevit import revit, forms, script

doc = revit.doc
uidoc = revit.uidoc
output = script.get_output()
FT3_TO_M3 = 0.028316846592
FT_TO_M = 0.3048
PASSO_GRID_M = 0.50  # Pode reduzir para 0.25 m para comparar convergencia.
MAX_CELULAS = 25000
VOLUME_MIN_M3 = 0.0001

# SELEÇÃO RESTRITA À TOPOSOLID========================================================================
class ToposolidSelectionFilter(ISelectionFilter):
    def AllowElement(self, element):
        return isinstance(element, Toposolid)

    def AllowReference(self, reference, position):
        return False


def pick_toposolid(prompt):
    try:
        ref = uidoc.Selection.PickObject(
            ObjectType.Element, ToposolidSelectionFilter(), prompt
        )
    except OperationCanceledException:
        script.exit()
    return doc.GetElement(ref)


def get_any_view3d():
    """Exige vista 3D explicita; nao escolhe automaticamente vista arbitraria.

    ReferenceIntersector respeita a visibilidade e a Section Box da vista.
    """
    v = doc.ActiveView
    if not isinstance(v, View3D) or v.IsTemplate:
        forms.alert("Abra uma vista 3D do terreno e execute novamente.\n"
                    "O calculo por raios nao usa vistas de planta/corte.",
                    exitscript=True)
    if v.IsSectionBoxActive:
        forms.alert("Desative a Section Box na vista 3D ativa e execute novamente.\n"
                    "Ela pode cortar exatamente os 4 raios que falharam.",
                    exitscript=True)
    output.print_md("Vista 3D de calculo: **{}** (Id {}).".format(v.Name, v.Id.Value))
    return v

# GEOMETRIA: PEGAR CONTORNO DO PLATO ======================================================================
def get_top_face(toposolid_elem):
    opt = Options()
    opt.ComputeReferences = True
    opt.DetailLevel = __import__("Autodesk.Revit.DB", fromlist=["ViewDetailLevel"]).ViewDetailLevel.Fine
    geo = toposolid_elem.get_Geometry(opt)

    best_face = None
    best_area = -1.0
    for gobj in geo:
        solid = gobj if isinstance(gobj, Solid) else None
        if solid is None or solid.Faces.Size == 0:
            continue
        for face in solid.Faces:
            bbox = face.GetBoundingBox()
            uv_mid = (bbox.Min + bbox.Max) / 2.0
            normal = face.ComputeNormal(uv_mid)
            if normal.Z > 0.5 and face.Area > best_area:
                best_area = face.Area
                best_face = face
    return best_face


def get_outer_loop(face):
    loops = face.GetEdgesAsCurveLoops()
    best_loop = None
    best_area = -1.0
    for loop in loops:
        pts = [c.GetEndPoint(0) for c in loop]
        area = 0.0
        n = len(pts)
        for i in range(n):
            p1 = pts[i]
            p2 = pts[(i + 1) % n]
            area += p1.X * p2.Y - p2.X * p1.Y
        area = abs(area) / 2.0
        if area > best_area:
            best_area = area
            best_loop = loop
    return best_loop


def get_platform_sketch_loop(platform):
    """Prioriza o CONTORNO completo do sketch, nao a maior face triangulada."""
    try:
        sketch = doc.GetElement(platform.SketchId)
        if sketch is None:
            return None
        best_loop, best_area = None, -1.0
        for curve_array in sketch.Profile:
            curves = [c for c in curve_array]
            pts = [c.GetEndPoint(0) for c in curves]
            if len(pts) < 3:
                continue
            twice_area = sum(pts[k].X * pts[(k + 1) % len(pts)].Y -
                             pts[(k + 1) % len(pts)].X * pts[k].Y
                             for k in range(len(pts)))
            area = abs(twice_area) / 2.0
            if area > best_area:
                loop = CurveLoop()
                for curve in curves:
                    loop.Append(curve)
                best_loop, best_area = loop, area
        return best_loop
    except Exception as ex:
        output.print_md("Nao foi possivel ler sketch do plato: {}".format(ex))
        return None


def levantar_contorno_ao_topo(samples, face):
    """Corrige a cota Z do Sketch para a face superior de plato PLANO."""
    if not isinstance(face, PlanarFace):
        raise Exception("Nao foi obtida uma face superior plana do plato.")
    bb = face.GetBoundingBox()
    uv = (bb.Min + bb.Max) / 2.0
    normal = face.ComputeNormal(uv)
    if normal.Z < 0.999:
        raise Exception("Platô nao horizontal; este metodo de sketch exige topo plano.")
    point = face.Evaluate(uv)
    fixed = []
    for p, direction in samples:
        z = point.Z - (normal.X * (p.X - point.X) +
                       normal.Y * (p.Y - point.Y)) / normal.Z
        fixed.append((XYZ(p.X, p.Y, z), direction))
    return fixed


def compute_outward(pt, tangent, centroid):
    up = XYZ.BasisZ
    outward = tangent.CrossProduct(up).Normalize()
    test_pt = pt + outward.Multiply(1.0)
    if (test_pt - centroid).GetLength() < (pt - centroid).GetLength():
        outward = outward.Negate()
    return outward


def slerp_2d(v1, v2, t):
    """Interpola a direcao (no plano XY) do vetor v1 para v2, pelo caminho
    mais curto - usado para gerar o leque/cone no canto."""
    a1 = math.atan2(v1.Y, v1.X)
    a2 = math.atan2(v2.Y, v2.X)
    diff = a2 - a1
    while diff > math.pi:
        diff -= 2.0 * math.pi
    while diff < -math.pi:
        diff += 2.0 * math.pi
    a = a1 + diff * t
    return XYZ(math.cos(a), math.sin(a), 0.0)


def sample_boundary_with_corners(loop, points_per_edge, corner_fan_points):
    """Amostra pontos ao longo de cada aresta (sem repetir o vertice) e, em
    cada vertice/quina, adiciona um leque de direcoes entre a normal da
    aresta anterior e a da proxima aresta - isso arredonda o canto (cone)
    em vez de deixar a quina em degrau."""
    curves = [c for c in loop]
    n_curves = len(curves)

    all_pts = [c.GetEndPoint(0) for c in curves]
    cx = sum(p.X for p in all_pts) / len(all_pts)
    cy = sum(p.Y for p in all_pts) / len(all_pts)
    centroid = XYZ(cx, cy, all_pts[0].Z)

    samples = []
    edge_dirs_start = []
    edge_dirs_end = []

    for curve in curves:
        for i in range(points_per_edge):
            t = float(i) / float(points_per_edge)
            pt = curve.Evaluate(t, True)
            tangent = curve.ComputeDerivatives(t, True).BasisX.Normalize()
            outward = compute_outward(pt, tangent, centroid)
            samples.append((pt, outward))

        d0 = curve.ComputeDerivatives(0.0, True).BasisX.Normalize()
        d1 = curve.ComputeDerivatives(1.0, True).BasisX.Normalize()
        edge_dirs_start.append(compute_outward(curve.Evaluate(0.0, True), d0, centroid))
        edge_dirs_end.append(compute_outward(curve.Evaluate(1.0, True), d1, centroid))

    for k in range(n_curves):
        prev_k = (k - 1) % n_curves
        vertex_pt = curves[k].GetEndPoint(0)
        prev_outward = edge_dirs_end[prev_k]
        next_outward = edge_dirs_start[k]
        for i in range(1, corner_fan_points):
            t = float(i) / float(corner_fan_points)
            direction = slerp_2d(prev_outward, next_outward, t)
            samples.append((vertex_pt, direction))

    return samples

# LOCALIZAR COTA DO TERRENO EXISTENTE EM UM PONTO XY====================================================
def make_terrain_probe(target_elem, view3d):
    return ReferenceIntersector(
        target_elem.Id, FindReferenceTarget.Face, view3d
    )

def get_terrain_z(probe, xy_point, search_from_z, search_down_length):
    origin = XYZ(xy_point.X, xy_point.Y, search_from_z)
    direction = XYZ.BasisZ.Negate()
    results = probe.Find(origin, direction)
    best = None
    for r in results:
        dist = r.Proximity
        if dist <= search_down_length and (best is None or dist < best):
            best = dist
    if best is None:
        return None
    return search_from_z - best

# Encontrar interseccao rampa x terreno (marcha + bisseccao)
def find_slope_terrain_point(edge_pt, outward, z_platform, angle_rad,
                              probe, search_from_z, search_down_length,
                              step, max_dist):
    """Busca primeiro nos 0..1 ft (antes ignorados); devolve ponto + diagnostico.

    Nunca inventa um encontro se o TN termina, esta oculto ou a rampa nao o cruza.
    """
    def terrain_z_at(d):
        xy = edge_pt + outward.Multiply(d)
        return get_terrain_z(probe, xy, search_from_z, search_down_length)

    # A rotina anterior comecava em 1 ft, perdendo encontros proximos do plato.
    distances = [0.0, 0.05, 0.1, 0.25, 0.5, 0.75]
    d = step
    while d <= max_dist + 1.0e-9:
        distances.append(d)
        d += step

    sign = None
    prev_d, prev_diff = None, None
    hits = 0
    holes = 0
    for d in distances:
        tz = terrain_z_at(d)
        if tz is None:
            holes += 1
            # Nao interpolar atraves de um trecho onde o TN nao existe.
            prev_d, prev_diff = None, None
            continue
        hits += 1
        if sign is None:
            sign = -1.0 if tz < z_platform else 1.0
        slope_z = z_platform + sign * d * math.tan(angle_rad)
        diff = slope_z - tz
        if abs(diff) < 1.0e-5:
            pt = edge_pt + outward.Multiply(d)
            return XYZ(pt.X, pt.Y, tz), "OK"

        if prev_d is not None and (diff < 0) != (prev_diff < 0):
            lo, hi, lo_diff = prev_d, d, prev_diff
            for _ in range(30):
                mid = (lo + hi) / 2.0
                mid_tz = terrain_z_at(mid)
                if mid_tz is None:
                    return None, "TN ausente dentro do intervalo de intersecao"
                mid_diff = (z_platform + sign * mid * math.tan(angle_rad)) - mid_tz
                if (mid_diff < 0) == (lo_diff < 0):
                    lo, lo_diff = mid, mid_diff
                else:
                    hi = mid
            mid = (lo + hi) / 2.0
            tz = terrain_z_at(mid)
            if tz is None:
                return None, "TN ausente no refinamento"
            xy = edge_pt + outward.Multiply(mid)
            return XYZ(xy.X, xy.Y, tz), "OK"
        prev_d, prev_diff = d, diff

    if hits == 0:
        return None, "TN nao visivel / fora do terreno ao longo de todo o raio"
    if holes:
        return None, "TN aparece, mas rampa nao o cruza ({} lacunas)".format(holes)
    return None, "TN encontrado, mas rampa nao o cruza em ate {:.2f} m".format(max_dist * 0.3048)

# EDITAR O TERRENO EXISTENTE (adiciona pontos - "cavar")==================================================
def apply_slope_to_terrain(terrain_elem, pairs):
    """Insere os pares, sem duplicar vertices XY ja existentes.

    Um XY existente com Z diferente exige edicao consciente do vertice;
    nao o substituimos silenciosamente, para proteger taludes manuais.
    """
    editor = terrain_elem.GetSlabShapeEditor()
    if not editor.IsEnabled:
        editor.Enable()
        doc.Regenerate()

    def xy_key(p):
        # Resolucao de 0.0001 ft (aprox. 0.03 mm), apenas para deduplicacao.
        return (int(round(p.X * 10000.0)), int(round(p.Y * 10000.0)))

    existentes = {}
    for vertex in editor.SlabShapeVertices:
        pos = vertex.Position
        existentes[xy_key(pos)] = pos

    added, skipped = 0, 0
    for edge_pt, meet_pt in pairs:
        for p in (edge_pt, meet_pt):
            key = xy_key(p)
            old = existentes.get(key)
            if old is not None:
                if abs(old.Z - p.Z) > 0.001:
                    raise Exception("Vertice ja existente em XY ({:.3f}, {:.3f}), "
                                    "cota atual {:.3f}, cota solicitada {:.3f} ft. "
                                    "Operacao cancelada para preservar edicoes.".format(
                                        p.X, p.Y, old.Z, p.Z))
                skipped += 1
                continue
            new_vertex = editor.AddPoint(p)
            if new_vertex is None:
                raise Exception("AddPoint nao inseriu o vertice em ({:.3f}, {:.3f}, {:.3f}).".format(
                    p.X, p.Y, p.Z))
            existentes[key] = p
            added += 1
    output.print_md("Vertices inseridos: {} | XY ja existentes: {}".format(added, skipped))
    if not added:
        raise Exception("Nenhum ponto novo foi inserido; terreno ja contem os XY solicitados.")



# BASELINE V5: APENAS O RELEVO ORIGINAL, SEM BREP/TRIANGULOS/BOOLEANAS

def arquivos_teste(terrain, platform):
    pasta = os.path.dirname(doc.PathName)
    nome = "Talude_GradeV5_{}_{}.json".format(terrain.Id.Value, platform.Id.Value)
    return os.path.join(pasta, nome)


def ray_z(probe, x, y, origem_z, alcance):
    # O primeiro encontro vertical, vindo de cima, e a superficie visivel superior.
    near = None
    for hit in probe.Find(XYZ(x, y, origem_z), XYZ.BasisZ.Negate()):
        if 0 <= hit.Proximity <= alcance:
            if near is None or hit.Proximity < near:
                near = hit.Proximity
    return None if near is None else origem_z - near


def amostrar_grade(probe, bbox, passo_m, origem_z, alcance):
    xmin, ymin, xmax, ymax = bbox
    passo = passo_m / FT_TO_M
    nx = max(1, int(math.ceil((xmax - xmin) / passo)))
    ny = max(1, int(math.ceil((ymax - ymin) / passo)))
    if nx * ny > MAX_CELULAS:
        raise Exception("Grade requer {} celulas (limite {}). Aumente PASSO_GRID_M.".format(
            nx * ny, MAX_CELULAS))
    dx = (xmax - xmin) / float(nx)
    dy = (ymax - ymin) / float(ny)
    cotas = []
    for j in range(ny):
        y = ymin + (j + .5) * dy
        for i in range(nx):
            x = xmin + (i + .5) * dx
            cotas.append(ray_z(probe, x, y, origem_z, alcance))
    validas = sum(1 for z in cotas if z is not None)
    if not validas:
        raise Exception("Nenhum ponto da grade encontrou o Toposolid de TN.")
    output.print_md("GRADE: {} x {} = {} celulas; {} validas; espacamento {:.3f} x {:.3f} m".format(
        nx, ny, len(cotas), validas, dx * FT_TO_M, dy * FT_TO_M))
    return {"bbox": list(bbox), "nx": nx, "ny": ny,
            "dx_ft": dx, "dy_ft": dy, "z_origem_ft": origem_z,
            "alcance_ft": alcance, "cotas_ft": cotas}


def reamostrar_grade(probe, grade):
    xmin, ymin, _, _ = grade['bbox']
    nx, ny = grade['nx'], grade['ny']
    dx, dy = grade['dx_ft'], grade['dy_ft']
    origem, alcance = grade['z_origem_ft'], grade['alcance_ft']
    return [ray_z(probe, xmin + (i + .5) * dx,
                  ymin + (j + .5) * dy, origem, alcance)
            for j in range(ny) for i in range(nx)]


def comparar(grade, cotas_atuais):
    originais = grade['cotas_ft']
    if len(originais) != len(cotas_atuais):
        raise Exception("Tamanho da grade atual difere do baseline.")
    area = grade['dx_ft'] * grade['dy_ft']
    corte_ft3, aterro_ft3 = 0., 0.
    alteradas, ausentes, max_delta = 0, 0, 0.
    for z0, z1 in zip(originais, cotas_atuais):
        if z0 is None:
            continue
        if z1 is None:
            ausentes += 1
            continue
        dz = z1-z0
        if abs(dz) >= 0.001:
            alteradas += 1
        max_delta = max(max_delta, abs(dz))
        if dz < 0:
            corte_ft3 -= dz * area
        else:
            aterro_ft3 += dz * area
    if ausentes:
        raise Exception("{} celulas perderam encontro com o TN. Nao registrar volume parcial.".format(ausentes))
    return (corte_ft3 * FT3_TO_M3, aterro_ft3 * FT3_TO_M3,
            alteradas, max_delta * FT_TO_M)


def salvar_json(path, terrain, platform, grade, borda, angulo):
    if os.path.exists(path):
        raise Exception("JSON V5 ja existe: {}. Para iniciar OUTRO teste, restaure o terreno e apague o JSON V5.".format(path))
    run_id = uuid.uuid4().hex[:12]

    # A API do Revit devolve ElementId.Value como System.Int64 no pyRevit.
    # O json do IronPython nao serializa tipos numericos .NET diretamente.
    # Montar payload SOMENTE com tipos nativos do Python (int/float/str/list/dict).
    grade_json = {
        "bbox": [float(v) for v in grade["bbox"]],
        "nx": int(grade["nx"]),
        "ny": int(grade["ny"]),
        "dx_ft": float(grade["dx_ft"]),
        "dy_ft": float(grade["dy_ft"]),
        "z_origem_ft": float(grade["z_origem_ft"]),
        "alcance_ft": float(grade["alcance_ft"]),
        "cotas_ft": [None if z is None else float(z)
                     for z in grade["cotas_ft"]],
    }
    data = {
        "versao": 5,
        "metodo": "grade_vertical_v5",
        "sessao": run_id,
        "arquivo_rvt": unicode(doc.PathName),
        # No IronPython, int(System.Int64) pode continuar como long do .NET.
        # Salvar IDs como texto elimina a sobrecarga ambigua e serializa com seguranca.
        # O script de ajuste valida com int(data["terreno_id"]), aceitando texto.
        "terreno_id": unicode(terrain.Id.Value.ToString()),
        "plato_id": unicode(platform.Id.Value.ToString()),
        "terreno_unique_id": unicode(terrain.UniqueId),
        "plato_unique_id": unicode(platform.UniqueId),
        "data": datetime.datetime.now().isoformat(),
        "angulo_graus": float(angulo),
        "grade": grade_json,
        "pontos_controle_ft": [None if z is None else float(z)
                              for z in borda],
    }
    # Diagnostico por campo ANTES de criar arquivo ou iniciar a transacao.
    # Caso outro objeto .NET entre futuramente no payload, identificar o campo.
    for chave, valor in data.items():
        try:
            json.dumps(valor)
        except (TypeError, ValueError) as ex:
            raise Exception("Campo do JSON '{}' nao serializavel: {}".format(chave, ex))
    payload = json.dumps(data)
    temporario = path + ".gravando"
    try:
        with open(temporario, "w") as f:
            f.write(payload)
            f.flush()
        os.rename(temporario, path)
    finally:
        if os.path.exists(temporario):
            os.remove(temporario)
    with open(path, 'r') as f:
        conferido = json.load(f)
    if conferido['sessao'] != run_id or len(conferido['grade']['cotas_ft']) != grade['nx'] * grade['ny']:
        raise Exception("JSON gravado nao passou na verificacao.")
    return conferido


def calcular_controles(probe, pares, origem, alcance):
    return [ray_z(probe, p.X, p.Y, origem, alcance) for p, _ in pares]


def resumo_controles(originais, finais):
    validos = [(a,b) for a,b in zip(originais, finais) if a is not None and b is not None]
    modificados = sum(1 for a,b in validos if abs(a-b)>0.001)
    max_dz = max([abs(a-b) for a,b in validos] or [0.])
    output.print_md("CONTROLE na borda: {} pares validos, {} mudaram; maior |dZ| {:.4f} m".format(
        len(validos), modificados, max_dz*FT_TO_M))


def csv_da_sessao(data):
    pasta = os.path.dirname(doc.PathName)
    return os.path.join(pasta, "Talude_CorteAterro_V5_{}_{}_{}.csv".format(
        data['terreno_id'], data['plato_id'], data['sessao']))


def gravar_csv(data, corte, aterro, origem):
    caminho = csv_da_sessao(data)
    corte_anterior, aterro_anterior = 0., 0.
    existe = os.path.isfile(caminho)
    if existe:
        with open(caminho, 'r') as f:
            for linha in f:
                col = linha.strip().split(';')
                if len(col) == 7 and col[0] != 'Data/Hora':
                    corte_anterior = float(col[1].replace(',', '.'))
                    aterro_anterior = float(col[2].replace(',', '.'))
    def br(v):
        return '{:.6f}'.format(v).replace('.', ',')
    with open(caminho, 'a') as f:
        if not existe or os.path.getsize(caminho)==0:
            f.write('Data/Hora;Corte total (m3);Aterro total (m3);Delta corte (m3);Delta aterro (m3);Origem;Sessao\n')
        f.write(';'.join([datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                          br(corte), br(aterro), br(corte-corte_anterior),
                          br(aterro-aterro_anterior), origem, data['sessao']])+'\n')
    return caminho, corte-corte_anterior, aterro-aterro_anterior


def main():
    if not doc.PathName:
        forms.alert("Salve o RVT para colocar o JSON na mesma pasta.", exitscript=True)
    platform = pick_toposolid("Selecione o PLATO (origem do talude)")
    terrain = pick_toposolid("Selecione o TERRENO NATURAL (sera editado)")
    if terrain.Id == platform.Id:
        forms.alert("Selecione Toposolids distintos.", exitscript=True)
    path = arquivos_teste(terrain, platform)
    if os.path.exists(path):
        forms.alert("O JSON V5 ja existe. Se voce voltou o terreno ao TN original para OUTRO "
                    "teste, exclua SOMENTE o arquivo Talude_GradeV5 correspondente. "
                    "O CSV anterior permanece separado por sessao.\n{}".format(path), exitscript=True)
    txt = forms.ask_for_string(default='45', prompt='Angulo do talude (graus):', title='Talude V5')
    if not txt: return
    try:
        angle_deg = float(txt.replace(',', '.'))
        if not 0 < angle_deg < 90: raise ValueError('fora da faixa')
    except Exception:
        forms.alert('Angulo invalido. Informe numero entre 0 e 90.', exitscript=True)
    txt = forms.ask_for_string(default='10', prompt='Pontos por aresta:', title='Talude V5')
    if not txt: return
    txt2 = forms.ask_for_string(default='20', prompt='Pontos no leque de cada canto:', title='Talude V5')
    if not txt2: return
    try:
        n_aresta = max(2, int(txt))
        n_canto = max(2, int(txt2))
    except Exception:
        forms.alert('Informe quantidades inteiras de pontos.', exitscript=True)
    view3d = get_any_view3d()
    face = get_top_face(platform)
    if face is None:
        forms.alert('Nao foi localizada a face superior do plato.', exitscript=True)
    loop = get_platform_sketch_loop(platform)
    if loop:
        samples = levantar_contorno_ao_topo(sample_boundary_with_corners(
            loop, n_aresta, n_canto), face)
        output.print_md('Contorno do plato: SKETCH completo.')
    else:
        loop = get_outer_loop(face)
        if loop is None:
            forms.alert('Contorno do plato nao encontrado.', exitscript=True)
        samples = sample_boundary_with_corners(loop, n_aresta, n_canto)
        output.print_md('Contorno do plato: maior face superior (verificar contorno).')
    try:
        if view3d.IsHidden(terrain.Id) or view3d.GetCategoryHidden(terrain.Category.Id):
            forms.alert('Exiba o TN e a categoria Toposolids na vista 3D.', exitscript=True)
    except Exception:
        pass
    probe = make_terrain_probe(terrain, view3d)
    origem = max(p.Z for p,_ in samples)+500.
    alcance = 2000.
    pares, falhas = [], []
    for idx,(p, direcao) in enumerate(samples):
        q, msg = find_slope_terrain_point(p, direcao, p.Z, math.radians(angle_deg),
                                          probe, origem, alcance, 1., 1000.)
        if q is None:
            falhas.append((idx+1, p, msg))
        else:
            pares.append((p,q))
    if falhas:
        for idx,p,msg in falhas:
            output.print_md('Raio {}: ({:.3f}, {:.3f}, {:.3f}) m - {}'.format(
                idx,p.X*FT_TO_M,p.Y*FT_TO_M,p.Z*FT_TO_M,msg))
        forms.alert('{} de {} raios falharam. Nada alterado.'.format(len(falhas),len(samples)), exitscript=True)
    all_pts = [p for p,_ in pares] + [q for _,q in pares]
    margem_ft = 10.0
    bbox = [min(p.X for p in all_pts)-margem_ft,
            min(p.Y for p in all_pts)-margem_ft,
            max(p.X for p in all_pts)+margem_ft,
            max(p.Y for p in all_pts)+margem_ft]
    try:
        grade = amostrar_grade(probe, bbox, PASSO_GRID_M, origem, alcance)
        controles = calcular_controles(probe, pares, origem, alcance)
        if not any(z is not None for z in controles):
            raise Exception('Pontos de controle nao encontraram o TN.')
        data = salvar_json(path, terrain, platform, grade, controles, angle_deg)
        output.print_md('**JSON ORIGINAL salvo e reaberto ANTES DA EDICAO**: {} | Sessao {}'.format(
            path, data['sessao']))
    except Exception as ex:
        forms.alert('Falha antes de editar o terreno: {}'.format(ex), exitscript=True)
    try:
        ed = terrain.GetSlabShapeEditor()
        n_antes = len(list(ed.SlabShapeVertices)) if ed.IsEnabled else 0
        tx = Transaction(doc, 'Talude V5 - Inserir pontos')
        if tx.Start() != TransactionStatus.Started:
            raise Exception('A transacao nao iniciou.')
        try:
            apply_slope_to_terrain(terrain, pares)
            doc.Regenerate()
            status = tx.Commit()
            if status != TransactionStatus.Committed:
                raise Exception('Commit nao confirmado: {}'.format(status))
        except Exception:
            if tx.GetStatus() == TransactionStatus.Started:
                tx.RollBack()
            raise
        output.print_md('Status transacao: Committed')
        terrain = doc.GetElement(terrain.Id)
        ed = terrain.GetSlabShapeEditor()
        n_depois = len(list(ed.SlabShapeVertices))
        output.print_md('**EDICAO CONFIRMADA** | Vertices antes: {} / depois: {}'.format(n_antes,n_depois))
    except Exception as ex:
        forms.alert('Edicao falhou e a transacao foi revertida. JSON ORIGINAL continua salvo: {}'.format(ex), exitscript=True)
    try:
        probe_atual = make_terrain_probe(terrain, view3d)
        cotas_atuais = reamostrar_grade(probe_atual, data['grade'])
        corte, aterro, alteradas, max_dz = comparar(data['grade'], cotas_atuais)
        resumo_controles(controles, calcular_controles(probe_atual, pares, origem, alcance))
        output.print_md('GRADE: {} celulas alteradas | maior |dZ| {:.4f} m'.format(alteradas,max_dz))
        output.print_md('CORTE ESTIMADO: {:.3f} m3 | ATERRO ESTIMADO: {:.3f} m3'.format(corte,aterro))
        if corte <= VOLUME_MIN_M3 and aterro <= VOLUME_MIN_M3:
            forms.alert('Terreno EDITADO e JSON salvo; grade retornou zero. Verifique as cotas de controle e a vista 3D. CSV intacto.')
            return
        csv_path,dc,da = gravar_csv(data,corte,aterro,'GeracaoV5')
        output.print_md('CSV DA SESSAO: {} | Delta corte {:.3f} m3 / aterro {:.3f} m3'.format(csv_path,dc,da))
    except Exception as ex:
        forms.alert('Talude foi aplicado e JSON preservado, mas o calculo/CSV falhou: {}'.format(ex))

if __name__ == '__main__':
    main()