# -*- coding: utf-8 -*-
import math
from pyrevit import DB
import config_modelo

# CONSTANTES GLOBAIS
DECLIVE = 0.02
NOME_TIPO_RAMAL = config_modelo.obter("TIPO_TUBO_ESGOTO", u"PVC Esgoto Série Normal")
NOME_SISTEMA = config_modelo.obter("SISTEMA_ESGOTO", u"Esgoto Sanitário")
TOL_45 = 3.0
COMPRIMENTO_GUIA = 1000 / 304.8
QUEDA_MINIMA = 80 / 304.8
TRECHO_MIN = 150 / 304.8
MARGEM_COLETOR = 300 / 304.8
TRECHO_FINAL_MIN = 60 / 304.8
DESLOC_MAX_CAIXA = 300 / 304.8

def mm(value): return value / 304.8
def to_mm(feet): return feet * 304.8
def xy(pt): return DB.XYZ(pt.X, pt.Y, 0)

def folded_deg(a, b):
    ang = a.AngleTo(b) * 180.0 / math.pi
    return min(ang, 180.0 - ang)

def multiplo_45(vec, tol=1.0):
    ang = math.degrees(math.atan2(vec.Y, vec.X)) % 45.0
    return min(ang, 45.0 - ang) <= tol

def pipe_axis(curve):
    p0 = curve.GetEndPoint(0)
    p1 = curve.GetEndPoint(1)
    main_xy = xy(p1) - xy(p0)
    if main_xy.GetLength() < mm(50): return None
    return p0, p1, main_xy.Normalize()

def sentido_fluxo(curve):
    p0 = curve.GetEndPoint(0)
    p1 = curve.GetEndPoint(1)
    vec = xy(p1) - xy(p0)
    if p0.Z < p1.Z - 1e-9: vec = vec.Negate()
    return vec.Normalize()

def horizontal_dir(conn):
    flat = xy(conn.CoordinateSystem.BasisZ)
    if flat.GetLength() < 0.2: return None
    return flat.Normalize()

def ray_hit(origin, direction, p0, p1, main_dir):
    target = xy(p0) - xy(origin)
    denom = direction.X * main_dir.Y - direction.Y * main_dir.X
    if abs(denom) < 1e-8: return None
    t = (target.X * main_dir.Y - target.Y * main_dir.X) / denom
    u = (target.X * direction.Y - target.Y * direction.X) / denom
    length = xy(p0).DistanceTo(xy(p1))
    if t < TRECHO_MIN or u < MARGEM_COLETOR or u > length - MARGEM_COLETOR: return None
    return xy(p0) + main_dir * u, t

def rot2d(vec, deg):
    ang = math.radians(deg)
    c = math.cos(ang)
    s = math.sin(ang)
    return DB.XYZ(vec.X * c - vec.Y * s, vec.X * s + vec.Y * c, 0)

def z_at_xy(curve, pt2):
    p0 = curve.GetEndPoint(0)
    p1 = curve.GetEndPoint(1)
    ab = xy(p1) - xy(p0)
    len2 = ab.DotProduct(ab)
    if len2 < 1e-12: return p0.Z
    t = (pt2 - xy(p0)).DotProduct(ab) / len2
    return p0.Z + (p1.Z - p0.Z) * t

def point_on_pipe(curve, pt2):
    guess = DB.XYZ(pt2.X, pt2.Y, z_at_xy(curve, pt2))
    proj = curve.Project(guess)
    if proj is None or proj.Distance > mm(30): return None
    return proj

def hit_point(curve, hit_xy, z_fallback):
    proj = point_on_pipe(curve, hit_xy)
    if proj is None: return DB.XYZ(hit_xy.X, hit_xy.Y, z_fallback)
    return proj.XYZPoint

def comprimento_planta(pontos):
    total = 0.0
    for i in range(len(pontos) - 1):
        total += xy(pontos[i]).DistanceTo(xy(pontos[i + 1]))
    return total