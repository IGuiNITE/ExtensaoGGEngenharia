# -*- coding: utf-8 -*-
"""Leitor de configuracoes do modelo Revit a partir de config_modelo.csv.

Compativel com IronPython 2.7 e CPython 3, sem dependencias externas.
"""
import io
import os
import sys

_EXT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_CSV_PATH = os.path.join(_EXT_DIR, "config_modelo.csv")
_CACHE = None


def _detectar_delimitador(primeira_linha):
    if u";" in primeira_linha:
        return u";"
    if u"," in primeira_linha:
        return u","
    return u";"


def _parse_linha_csv(linha, delim):
    """Faz o parse de uma linha CSV respeitando aspas duplas em Unicode."""
    campos = []
    atual = []
    em_aspas = False
    i = 0
    n = len(linha)
    while i < n:
        ch = linha[i]
        if ch == u'"':
            if em_aspas and i + 1 < n and linha[i + 1] == u'"':
                atual.append(u'"')
                i += 2
                continue
            em_aspas = not em_aspas
        elif ch == delim and not em_aspas:
            campos.append(u"".join(atual))
            atual = []
        else:
            atual.append(ch)
        i += 1
    campos.append(u"".join(atual))
    return campos


def _ler_texto_csv():
    for enc in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            with io.open(_CSV_PATH, "r", encoding=enc) as f:
                return f.read()
        except Exception:
            continue
    return None


def _carregar_csv():
    global _CACHE
    if _CACHE is not None:
        return _CACHE

    dados = {}
    if not os.path.isfile(_CSV_PATH):
        _CACHE = dados
        return dados

    conteudo = _ler_texto_csv()
    if not conteudo:
        _CACHE = dados
        return dados

    linhas = conteudo.splitlines()
    if not linhas:
        _CACHE = dados
        return dados

    delim = _detectar_delimitador(linhas[0])
    cabecalho = True
    for linha in linhas:
        if not linha.strip():
            continue
        row = _parse_linha_csv(linha, delim)
        if len(row) < 2:
            continue
        chave = row[0].strip()
        if cabecalho and chave.lower() == u"chave":
            cabecalho = False
            continue
        cabecalho = False
        if chave and not chave.startswith(u"#"):
            dados[chave] = row[1].strip()

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
