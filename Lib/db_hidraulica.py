# -*- coding: utf-8 -*-
import os
import sys
import sqlite3

PASTA_LIB = os.path.dirname(os.path.abspath(__file__))
if PASTA_LIB not in sys.path:
    sys.path.insert(0, PASTA_LIB)

try:
    from data.criar_banco import criar_e_popular
except ImportError:
    from criar_banco import criar_e_popular

CAMINHO_DB = os.path.join(PASTA_LIB, "data", "hidraulica.db")


def garantir_banco():
    """Cria e popula o arquivo .db caso ainda não exista."""
    if not os.path.exists(CAMINHO_DB) or os.path.getsize(CAMINHO_DB) == 0:
        criar_e_popular(CAMINHO_DB)


def carregar_tabela():
    """Retorna {dn: (di_mm, [j90, j45, c90, c45, te_dir, te_la, te_bi, gaveta, esfera])}."""
    garantir_banco()

    conn = sqlite3.connect(CAMINHO_DB)
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT dn, di_mm, j90, j45, c90, c45, te_dir, te_la, te_bi, gaveta, esfera
            FROM conexoes_pvc
            ORDER BY dn ASC
        """)

        tabela = {}
        for row in cur.fetchall():
            dn = int(row[0])
            di_mm = float(row[1])
            comprimentos = [float(val) for val in row[2:]]
            tabela[dn] = (di_mm, comprimentos)

        return tabela
    finally:
        conn.close()