# -*- coding: utf-8 -*-
__title__ = "Anotar\nTubos"
__doc__ = """Anota automaticamente todos os segmentos de tubo das plantas:
- Ventilação: seta (DN + Seta 1-1) de um lado e inclinação do outro
- Demais tubos: seta, inclinação (acima da seta) e UHC (do outro lado)
- Tubos verticais acima de 2 m: símbolo de coluna com linha de chamada
Se houver tubos selecionados na vista ativa, apenas eles são anotados."""
__author__ = "Guilherme Geraldo da Silva"

# IMPORTS========================================================================
import math

from Autodesk.Revit.DB import (
    Transaction,
    TransactionGroup,
    XYZ,
    Line,
    Reference,
    IndependentTag,
    TagOrientation,
    LeaderEndCondition,
    BuiltInCategory,
    BuiltInParameter,
    Element,
    FilteredElementCollector,
    LocationCurve,
    ViewType,
    UnitUtils,
    UnitTypeId,   # Revit 2021+
)
from Autodesk.Revit.DB.Plumbing import Pipe
from pyrevit import forms, script

try:
    import unicodedata
except ImportError:
    unicodedata = None

# VARIÁVEIS===========================================================================
uidoc = __revit__.ActiveUIDocument
doc = uidoc.Document

# ---------- CONFIGURAÇÕES ----------
# Tipos de etiqueta de tubo: (nome da família, nome do tipo)
TAG_SETA = (u"Anotação Tubos 4", u"DN + Seta 1-1")
TAG_INCLINACAO = (u"Anotação Tubos 3", u"Inclinação")
TAG_UHC = (u"Anotação Tubos 3", u"UHC")
TAG_COLUNA = (u"Anotacao_Coluna", u"Coluna")

# Um tubo é de ventilação se a classificação ou o tipo de sistema
# contiver alguma destas palavras (sem diferenciar maiúsculas)
PALAVRAS_VENTILACAO = (u"vent",)

# Coluna: tubos verticais mais longos que isso recebem o símbolo de coluna
COLUNA_ALTURA_MIN_M = 2.0

# Uma prumada real costuma vir dividida em vários elementos Pipe (cada luva,
# válvula ou acessório no meio quebra o tubo em segmentos menores). Para não
# perder a coluna por causa disso, segmentos verticais cujo eixo (X,Y) esteja
# a menos desta distância (cm) um do outro são tratados como a MESMA prumada,
# e a soma das alturas é o que vale para o limite de COLUNA_ALTURA_MIN_M.
PRUMADA_XY_TOL_CM = 5

# Posição do símbolo de coluna em relação ao tubo, em mm de PAPEL:
# (direita, cima). Valores negativos = esquerda / baixo.
COLUNA_OFFSET_MM = (12, -10)

# Distâncias FIXAS, em mm de PAPEL, do EIXO do tubo até o ponto de inserção
# de cada etiqueta (não dependem do tamanho real da família — são só uma
# distância; ajuste estes 4 números até o resultado ficar bom na sua escala):
SETA_OFFSET_MM = 1.5              # seta: lado de cima (ou esquerda)
INCLINACAO_OFFSET_MM = 8.0        # inclinação (tubos normais): mesmo lado, mais afastada que a seta
VENT_INCLINACAO_OFFSET_MM = 2.5   # inclinação (ventilação): lado oposto à seta
UHC_OFFSET_MM = 2.5               # UHC: lado oposto ao da seta

# Tubos cuja projeção em planta seja quase vertical (< ~6°) são tratados como
# "verticais na tela": etiquetas à ESQUERDA. Nos demais, "acima".
TOLERANCIA_VERTICAL_PLANTA = 0.1

# Ignora segmentos menores que isso (cm)
COMPRIMENTO_MIN_CM = 1

# Não repete etiquetas já existentes (pode rodar o comando mais de uma vez)
PULAR_JA_ANOTADOS = True

TIPOS_DE_VISTA = (
    ViewType.FloorPlan,
    ViewType.CeilingPlan,
    ViewType.EngineeringPlan,
    ViewType.AreaPlan,
)

# FUNÇÕES================================================================================
def eid(element_id):
    """Valor numérico do ElementId (compatível com várias versões)."""
    try:
        return element_id.Value
    except AttributeError:
        return element_id.IntegerValue


def norm(s):
    """Normaliza texto: minúsculo, sem acentos, sem espaços duplicados."""
    s = u"{}".format(s or u"")
    if unicodedata is not None:
        s = unicodedata.normalize("NFKD", s)
        s = u"".join(c for c in s if not unicodedata.combining(c))
    return u" ".join(s.lower().split())


def type_name(sym):
    try:
        return Element.Name.GetValue(sym)
    except Exception:
        return sym.Name


def paper_to_model(mm, scale):
    """mm de papel -> pés no modelo, aplicando a escala da vista."""
    return UnitUtils.ConvertToInternalUnits(mm * scale, UnitTypeId.Millimeters)


# ---------- Tipos de etiqueta ----------
def load_tag_types():
    """Localiza os 4 tipos de etiqueta no projeto.
    Retorna dict {'seta','incl','uhc','coluna': FamilySymbol}."""
    wanted = [
        ("seta", TAG_SETA),
        ("incl", TAG_INCLINACAO),
        ("uhc", TAG_UHC),
        ("coluna", TAG_COLUNA),
    ]
    symbols = (FilteredElementCollector(doc)
               .OfCategory(BuiltInCategory.OST_PipeTags)
               .WhereElementIsElementType()
               .ToElements())
    available = [(sym.FamilyName, type_name(sym), sym) for sym in symbols]

    found, missing = {}, []
    for key, (fam, typ) in wanted:
        for f, t, sym in available:
            if norm(f) == norm(fam) and norm(t) == norm(typ):
                found[key] = sym
                break
        else:
            missing.append(u"{} : {}".format(fam, typ))

    if missing:
        print("Tipos de etiqueta de tubo disponíveis no projeto:")
        for f, t, _ in sorted(available, key=lambda x: (x[0], x[1])):
            print(u"  {} : {}".format(f, t))
        forms.alert(
            u"Tipos de etiqueta não encontrados:\n\n" + u"\n".join(missing) +
            u"\n\nConfira os nomes no início do script "
            u"(a lista dos tipos disponíveis está na janela de saída).",
            title="Anotar Tubos",
            exitscript=True,
        )
    return found


# ---------- Tubos ----------
def is_vent(pipe):
    """Identifica tubos de ventilação pela classificação/tipo de sistema."""
    texts = []
    p = pipe.get_Parameter(BuiltInParameter.RBS_SYSTEM_CLASSIFICATION_PARAM)
    if p is not None:
        texts.append(p.AsString())
    p = pipe.get_Parameter(BuiltInParameter.RBS_PIPING_SYSTEM_TYPE_PARAM)
    if p is not None:
        texts.append(p.AsValueString())
    joined = norm(u" ".join(u"{}".format(t or u"") for t in texts))
    return any(norm(w) in joined for w in PALAVRAS_VENTILACAO)


def pipe_radius(pipe):
    """Raio externo do tubo (pés)."""
    for bip in (BuiltInParameter.RBS_PIPE_OUTER_DIAMETER,
                BuiltInParameter.RBS_PIPE_DIAMETER_PARAM):
        p = pipe.get_Parameter(bip)
        if p is not None and p.HasValue and p.AsDouble() > 0:
            return p.AsDouble() / 2.0
    return 0.0


def pipe_geometry(pipe, right, up, min_length):
    """Dados geométricos do tubo em relação à vista.
    Retorna None se o tubo não for reto ou for muito curto."""
    loc = pipe.Location
    if not isinstance(loc, LocationCurve):
        return None
    curve = loc.Curve
    if not isinstance(curve, Line):
        return None

    length = curve.Length
    if length < min_length:
        return None

    p0, p1 = curve.GetEndPoint(0), curve.GetEndPoint(1)
    d = (p1 - p0).Normalize()

    geo = {
        "pipe": pipe,
        "mid": (p0 + p1) * 0.5,
        "length": length,
        "vertical": abs(d.Z) > 0.99,
    }
    if geo["vertical"]:
        geo["zmin"] = min(p0.Z, p1.Z)
        geo["zmax"] = max(p0.Z, p1.Z)
        return geo

    # Direção do tubo (t) e normal (n) no sistema de eixos da vista
    du, dv = d.DotProduct(right), d.DotProduct(up)
    size = math.sqrt(du * du + dv * dv)
    tu, tv = du / size, dv / size
    nu, nv = -tv, tu

    if abs(nv) < TOLERANCIA_VERTICAL_PLANTA:
        nu, nv = -1.0, 0.0          # vertical na tela -> lado ESQUERDO
    elif nv < 0:
        nu, nv = -nu, -nv           # sempre o lado de CIMA

    geo["t3"] = right * tu + up * tv
    geo["n3"] = right * nu + up * nv
    return geo


def get_pipes_in_view(vw):
    return list(FilteredElementCollector(doc, vw.Id).OfClass(Pipe).ToElements())


def group_verticals(items, tol):
    """Agrupa segmentos verticais cujo eixo (X,Y) coincide (dentro de 'tol'),
    isto é, pedaços do mesmo prumo separados por luvas/acessórios.
    'items' é uma lista de (pipe, geo). Retorna lista de grupos, cada um uma
    lista de (pipe, geo)."""
    n = len(items)
    parent = list(range(n))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i in range(n):
        xi, yi = items[i][1]["mid"].X, items[i][1]["mid"].Y
        for j in range(i + 1, n):
            xj, yj = items[j][1]["mid"].X, items[j][1]["mid"].Y
            if math.hypot(xi - xj, yi - yj) <= tol:
                ri, rj = find(i), find(j)
                if ri != rj:
                    parent[rj] = ri

    groups = {}
    for i in range(n):
        groups.setdefault(find(i), []).append(items[i])
    return list(groups.values())


def existing_tags(vw):
    """Conjunto de (id do tubo, id do tipo da etiqueta) já presentes na vista."""
    found = set()
    for tag in FilteredElementCollector(doc, vw.Id).OfClass(IndependentTag).ToElements():
        try:
            type_id = eid(tag.GetTypeId())
            for tagged in tag.GetTaggedLocalElementIds():
                found.add((eid(tagged), type_id))
        except Exception:
            continue
    return found


# ---------- Posicionamento ----------
def tag_target_point(mid, n3, r, offset_model, side):
    """Ponto (no eixo do tubo, deslocado por 'n3') para o anchor da etiqueta.
    'side' é +1 (lado principal: seta/inclinação normal) ou -1 (lado oposto:
    UHC, ou inclinação de ventilação)."""
    return mid + n3 * (side * (r + offset_model))


# ---------- Criação de etiquetas ----------
def create_tag(vw, sym, pipe, point, leader=False):
    return IndependentTag.Create(
        doc, sym.Id, vw.Id, Reference(pipe),
        leader, TagOrientation.Horizontal, point
    )


def process_view(vw, pipes, tag_types):
    """Anota os tubos de uma vista. Retorna dict com as estatísticas."""
    stats = {"seta": 0, "incl": 0, "uhc": 0, "coluna": 0, "falhas": 0, "erro": None}

    right, up = vw.RightDirection, vw.UpDirection
    scale = vw.Scale
    level = getattr(vw, "GenLevel", None)
    z = level.Elevation if level is not None else 0.0

    min_length = UnitUtils.ConvertToInternalUnits(COMPRIMENTO_MIN_CM, UnitTypeId.Centimeters)
    column_min = UnitUtils.ConvertToInternalUnits(COLUNA_ALTURA_MIN_M, UnitTypeId.Meters)
    col_dx = paper_to_model(COLUNA_OFFSET_MM[0], scale)
    col_dy = paper_to_model(COLUNA_OFFSET_MM[1], scale)

    def register_error(e):
        stats["falhas"] += 1
        if stats["erro"] is None:
            stats["erro"] = u"{}".format(e)

    t = Transaction(doc, "Anotar tubos - {}".format(vw.Name))
    t.Start()
    try:
        for sym in tag_types.values():
            try:
                if not sym.IsActive:
                    sym.Activate()
            except Exception:
                pass

        existing = existing_tags(vw) if PULAR_JA_ANOTADOS else set()
        verticals = []

        for pipe in pipes:
            geo = pipe_geometry(pipe, right, up, min_length)
            if geo is None:
                continue

            # --- Tubo vertical: junta para processar depois, agrupado ---
            if geo["vertical"]:
                verticals.append((pipe, geo))
                continue

            # --- Tubo inclinado/horizontal: seta, inclinação, UHC ---
            pipe_id = eid(pipe.Id)
            vent = is_vent(pipe)
            mid = XYZ(geo["mid"].X, geo["mid"].Y, z)
            n3 = geo["n3"]
            r = pipe_radius(pipe)

            # (papel, lado): +1 = mesmo lado da seta; -1 = lado oposto
            role_offsets = {
                "seta": (SETA_OFFSET_MM, +1),
                "incl": (VENT_INCLINACAO_OFFSET_MM, -1) if vent else (INCLINACAO_OFFSET_MM, +1),
            }
            if not vent:
                role_offsets["uhc"] = (UHC_OFFSET_MM, -1)

            for role, (offset_mm, side) in role_offsets.items():
                sym = tag_types[role]
                if (pipe_id, eid(sym.Id)) in existing:
                    continue
                try:
                    point = tag_target_point(mid, n3, r, paper_to_model(offset_mm, scale), side)
                    create_tag(vw, sym, pipe, point)
                    stats[role] += 1
                except Exception as e:
                    register_error(e)

        # --- Prumadas: agrupa segmentos verticais fragmentados (por luvas,
        # válvulas, reduções, etc.) que compartilham o mesmo eixo (X,Y), soma
        # a altura total do grupo e cota uma única vez por prumada ---
        sym = tag_types["coluna"]
        sym_id = eid(sym.Id)
        prumada_tol = UnitUtils.ConvertToInternalUnits(PRUMADA_XY_TOL_CM, UnitTypeId.Centimeters)

        for group in group_verticals(verticals, prumada_tol):
            total_length = sum(g["length"] for _, g in group)
            if total_length <= column_min:
                continue
            if any((eid(p.Id), sym_id) in existing for p, _ in group):
                continue

            # Ponto de referência: tubo mais longo do grupo; posição em X,Y
            # é a média (o eixo pode variar poucos mm entre trechos por
            # imprecisão de modelagem), e a Z é o meio do trecho mais alto.
            ref_pipe, ref_geo = max(group, key=lambda pg: pg[1]["length"])
            avg_x = sum(g["mid"].X for _, g in group) / len(group)
            avg_y = sum(g["mid"].Y for _, g in group) / len(group)

            try:
                center = XYZ(avg_x, avg_y, z)
                head = center + right * col_dx + up * col_dy
                tag = create_tag(vw, sym, ref_pipe, head, leader=True)
                try:
                    tag.LeaderEndCondition = LeaderEndCondition.Attached
                except Exception:
                    pass
                tag.TagHeadPosition = head
                stats["coluna"] += 1
            except Exception as e:
                register_error(e)

        t.Commit()
    except Exception as e:
        t.RollBack()
        return {"seta": 0, "incl": 0, "uhc": 0, "coluna": 0, "falhas": 1, "erro": u"{}".format(e)}

    return stats

# MAIN=================================================================================
def main():
    choice = forms.alert(
        "Onde deseja anotar os tubos?",
        title="Anotar Tubos",
        options=["Vista ativa", "Escolher vistas..."],
    )
    if not choice:
        script.exit()

    tag_types = load_tag_types()

    # Lista de (vista, tubos)
    targets = []

    if choice == "Vista ativa":
        vw = doc.ActiveView
        if vw.ViewType not in TIPOS_DE_VISTA:
            forms.alert("Abra uma vista de planta para anotar os tubos.", exitscript=True)

        # Se já houver tubos selecionados, usa apenas eles
        selected = [doc.GetElement(i) for i in uidoc.Selection.GetElementIds()]
        selected = [e for e in selected if isinstance(e, Pipe)]
        targets.append((vw, selected if selected else get_pipes_in_view(vw)))
    else:
        views = forms.select_views(
            title="Selecione as plantas a anotar",
            button_name="Anotar",
            multiple=True,
            filterfunc=lambda x: x.ViewType in TIPOS_DE_VISTA and not x.IsTemplate,
        )
        if not views:
            script.exit()
        for vw in views:
            targets.append((vw, get_pipes_in_view(vw)))

    totals = {"seta": 0, "incl": 0, "uhc": 0, "coluna": 0, "falhas": 0}
    first_error = None
    report = []

    # Um único Ctrl+Z desfaz o lote inteiro
    tg = TransactionGroup(doc, "Anotar tubos")
    tg.Start()
    try:
        for vw, pipes in targets:
            s = process_view(vw, pipes, tag_types)
            for k in totals:
                totals[k] += s[k]
            if s["erro"] and first_error is None:
                first_error = s["erro"]
            report.append(u"{}: {} etiqueta(s)".format(
                vw.Name, s["seta"] + s["incl"] + s["uhc"] + s["coluna"]))

        if totals["seta"] + totals["incl"] + totals["uhc"] + totals["coluna"] > 0:
            tg.Assimilate()
        else:
            tg.RollBack()
    except Exception as e:
        tg.RollBack()
        forms.alert(u"Erro ao anotar os tubos:\n{}".format(e), exitscript=True)

    msg = (u"Setas: {}\nInclinações: {}\nUHC: {}\nColunas: {}"
           .format(totals["seta"], totals["incl"], totals["uhc"], totals["coluna"]))
    if len(report) > 1:
        msg += u"\n\n" + u"\n".join(report)
    if totals["falhas"]:
        msg += u"\n\nFalhas: {}\nPrimeiro erro: {}".format(totals["falhas"], first_error)
    forms.alert(msg, title="Anotar Tubos")


if __name__ == "__main__":
    main()