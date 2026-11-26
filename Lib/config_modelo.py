# -*- coding: utf-8 -*-
"""Leitor de configuracoes do modelo Revit a partir de config_modelo.csv.

Compativel com IronPython 2.7 e CPython 3, sem dependencias externas.
"""
import csv
import os
import sys

_PY3 = sys.version_info[0] >= 3
_EXT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_CSV_PATH = os.path.join(_EXT_DIR, "config_modelo.csv")
_CACHE = None


def _to_unicode(val):
    if val is None:
        return u""
    if _PY3:
        return str(val)
    if isinstance(val, unicode):  # noqa: F821
        return val
    for enc in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
        try:
            return val.decode(enc)
        except Exception:
            pass
    return val.decode("latin-1", "replace")


def _detectar_delimitador(primeira_linha):
    if u";" in primeira_linha:
        return ";"
    if u"," in primeira_linha:
        return ","
    return ";"


def _carregar_csv():
    global _CACHE
    if _CACHE is not None:
        return _CACHE

    dados = {}
    if not os.path.isfile(_CSV_PATH):
        _CACHE = dados
        return dados

    if _PY3:
        conteudo = None
        for enc in ("utf-8-sig", "cp1252", "latin-1"):
            try:
                with open(_CSV_PATH, "r", encoding=enc, newline="") as f:
                    conteudo = f.read()
                break
            except UnicodeDecodeError:
                continue
        if conteudo is None:
            _CACHE = dados
            return dados

        linhas = conteudo.splitlines()
        if not linhas:
            _CACHE = dados
            return dados
        delim = _detectar_delimitador(linhas[0])
        reader = csv.reader(linhas, delimiter=delim)
        cabecalho = True
        for row in reader:
            if not row or len(row) < 2:
                continue
            chave = row[0].strip()
            if cabecalho and chave.lower() == "chave":
                cabecalho = False
                continue
            cabecalho = False
            if chave and not chave.startswith("#"):
                dados[chave] = row[1].strip()
    else:
        with open(_CSV_PATH, "rb") as f:
            bruto = f.read()
        texto = _to_unicode(bruto)
        linhas = texto.splitlines()
        if not linhas:
            _CACHE = dados
            return dados
        delim = _detectar_delimitador(linhas[0])
        linhas_utf8 = [ln.encode("utf-8") for ln in linhas]
        reader = csv.reader(linhas_utf8, delimiter=delim)
        cabecalho = True
        for row in reader:
            if not row or len(row) < 2:
                continue
            chave = _to_unicode(row[0]).strip()
            if cabecalho and chave.lower() == u"chave":
                cabecalho = False
                continue
            cabecalho = False
            if chave and not chave.startswith(u"#"):
                dados[chave] = _to_unicode(row[1]).strip()

    _CACHE = dados
    return dados


def obter(chave, padrao=u""):
    """Retorna o valor string configurado para a chave, ou o padrao se vazio/ausente."""
    dados = _carregar_csv()
    val = dados.get(chave)
    if val is None or val == u"":
        return padrao
    return val


def obter_opcional(chave, padrao=None):
    """Retorna o valor configurado ou None quando a celula estiver vazia."""
    dados = _carregar_csv()
    if chave not in dados:
        return padrao
    val = dados[chave]
    return val if val != u"" else None


def obter_tupla(chave, padrao):
    """Converte 'Familia : Tipo' em (Familia, Tipo), usando padrao como fallback."""
    val = obter(chave, u"")
    if not val:
        return padrao
    if u":" in val:
        partes = val.split(u":", 1)
        fam = partes[0].strip()
        tipo = partes[1].strip()
        if fam and tipo:
            return (fam, tipo)
    return padrao


def obter_lista(chave, padrao):
    """Converte valores separados por virgula em tupla de strings em minusculas."""
    val = obter(chave, u"")
    if not val:
        return padrao
    itens = tuple(item.strip().lower() for item in val.split(u",") if item.strip())
    return itens if itens else padrao
