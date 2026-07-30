"""Camada de armazenamento: conexão SQLite, criação de schema e upserts.

Todas as escritas são idempotentes — reexecutar a coleta nunca duplica
observações, graças à constraint UNIQUE (serie_id, data) e ao upsert.
"""

from __future__ import annotations

import sqlite3
from datetime import date, datetime
from pathlib import Path

RAIZ_PROJETO = Path(__file__).resolve().parents[2]
CAMINHO_DB_PADRAO = RAIZ_PROJETO / "data" / "radar.db"
CAMINHO_SCHEMA = RAIZ_PROJETO / "sql" / "schema.sql"


def conectar(caminho_db: str | Path = CAMINHO_DB_PADRAO) -> sqlite3.Connection:
    """Abre a conexão com o banco, garantindo diretório e chaves estrangeiras."""
    caminho = Path(caminho_db)
    if str(caminho) != ":memory:":
        caminho.parent.mkdir(parents=True, exist_ok=True)
    conexao = sqlite3.connect(str(caminho))
    conexao.execute("PRAGMA foreign_keys = ON")
    return conexao


def inicializar(conexao: sqlite3.Connection) -> None:
    """Cria as tabelas do schema, caso ainda não existam."""
    conexao.executescript(CAMINHO_SCHEMA.read_text(encoding="utf-8"))
    conexao.commit()


def upsert_serie(
    conexao: sqlite3.Connection,
    codigo_sgs: int,
    slug: str,
    nome: str,
    unidade: str,
    frequencia: str,
    data_inicial: str,
) -> int:
    """Insere ou atualiza os metadados de uma série; retorna seu id."""
    conexao.execute(
        """
        INSERT INTO series (codigo_sgs, slug, nome, unidade, frequencia, data_inicial)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT (codigo_sgs) DO UPDATE SET
            slug         = excluded.slug,
            nome         = excluded.nome,
            unidade      = excluded.unidade,
            frequencia   = excluded.frequencia,
            data_inicial = excluded.data_inicial
        """,
        (codigo_sgs, slug, nome, unidade, frequencia, data_inicial),
    )
    conexao.commit()
    linha = conexao.execute(
        "SELECT id FROM series WHERE codigo_sgs = ?", (codigo_sgs,)
    ).fetchone()
    return int(linha[0])


def ultima_data(conexao: sqlite3.Connection, serie_id: int) -> date | None:
    """Data da observação mais recente da série — base da carga incremental."""
    linha = conexao.execute(
        "SELECT MAX(data) FROM observacoes WHERE serie_id = ?", (serie_id,)
    ).fetchone()
    if linha is None or linha[0] is None:
        return None
    return datetime.strptime(linha[0], "%Y-%m-%d").date()


def upsert_observacoes(
    conexao: sqlite3.Connection,
    serie_id: int,
    observacoes: list[tuple[date, float]],
) -> int:
    """Grava observações com upsert; retorna o total de linhas gravadas."""
    if not observacoes:
        return 0
    cursor = conexao.executemany(
        """
        INSERT INTO observacoes (serie_id, data, valor)
        VALUES (?, ?, ?)
        ON CONFLICT (serie_id, data) DO UPDATE SET
            valor       = excluded.valor,
            coletado_em = datetime('now')
        """,
        [(serie_id, quando.isoformat(), valor) for quando, valor in observacoes],
    )
    conexao.commit()
    return cursor.rowcount
