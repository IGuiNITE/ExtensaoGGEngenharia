# -*- coding: utf-8 -*-
import os
import sqlite3

PASTA_ATUAL = os.path.dirname(os.path.abspath(__file__))
CAMINHO_DB_PADRAO = os.path.join(PASTA_ATUAL, "hidraulica.db")

# (dn, di_mm, j90, j45, c90, c45, te_dir, te_la, te_bi, gaveta, esfera)
DADOS_INICIAIS = [
    (20,  17.0, 1.1, 0.4, 0.4, 0.2, 0.7, 2.3, 2.3, 0.1, 0.1),
    (25,  21.6, 1.2, 0.5, 0.5, 0.3, 0.8, 2.4, 2.4, 0.2, 0.2),
    (32,  27.8, 1.5, 0.7, 0.6, 0.4, 0.9, 3.1, 3.1, 0.2, 0.3),
    (40,  35.2, 2.0, 1.0, 0.7, 0.5, 1.5, 4.6, 4.6, 0.3, 0.4),
    (50,  44.0, 3.2, 1.3, 1.2, 0.6, 2.2, 7.3, 7.3, 0.7, 0.7),
    (60,  53.4, 3.4, 1.5, 1.3, 0.7, 2.3, 7.6, 7.6, 0.8, 0.8),
    (75,  66.6, 3.7, 1.7, 1.4, 0.8, 2.4, 7.8, 7.8, 0.9, 0.9),
    (85,  75.6, 3.9, 1.8, 1.5, 0.9, 2.5, 8.0, 8.0, 0.9, 0.9),
    (110, 97.8, 4.3, 1.9, 1.6, 1.0, 2.6, 8.3, 8.3, 1.0, 1.0),
]


def criar_e_popular(caminho_db=None):
    """Cria o arquivo SQLite, a tabela conexoes_pvc e insere os dados iniciais."""
    destino = caminho_db or CAMINHO_DB_PADRAO
    pasta = os.path.dirname(destino)
    if pasta and not os.path.exists(pasta):
        os.makedirs(pasta)

    conn = sqlite3.connect(destino)
    try:
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS conexoes_pvc (
                dn     INTEGER PRIMARY KEY,
                di_mm  REAL NOT NULL,
                j90    REAL NOT NULL,
                j45    REAL NOT NULL,
                c90    REAL NOT NULL,
                c45    REAL NOT NULL,
                te_dir REAL NOT NULL,
                te_la  REAL NOT NULL,
                te_bi  REAL NOT NULL,
                gaveta REAL NOT NULL,
                esfera REAL NOT NULL
            )
        """)

        cur.executemany("""
            INSERT OR REPLACE INTO conexoes_pvc (
                dn, di_mm, j90, j45, c90, c45, te_dir, te_la, te_bi, gaveta, esfera
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, DADOS_INICIAIS)

        conn.commit()
    finally:
        conn.close()


if __name__ == "__main__":
    criar_e_popular()
    print(u"Banco criado com sucesso em: {}".format(CAMINHO_DB_PADRAO))