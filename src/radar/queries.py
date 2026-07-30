"""Camada de análise: métricas calculadas em SQL (não em Pandas).

As consultas usam window functions (médias móveis, variação percentual via
LAG) e CTEs (alinhamento mensal para correlação). O Pandas entra apenas
como ponte entre o SQLite e o dashboard.
"""

from __future__ import annotations

import math
import sqlite3

import pandas as pd


def listar_series(conexao: sqlite3.Connection) -> pd.DataFrame:
    """Metadados de todas as séries cadastradas."""
    return pd.read_sql_query(
        """
        SELECT s.slug,
               s.nome,
               s.unidade,
               s.frequencia,
               COUNT(o.id)  AS total_observacoes,
               MIN(o.data)  AS primeira_data,
               MAX(o.data)  AS ultima_data
        FROM series s
        LEFT JOIN observacoes o ON o.serie_id = s.id
        GROUP BY s.id
        ORDER BY s.nome
        """,
        conexao,
    )


def observacoes(
    conexao: sqlite3.Connection, slug: str, inicio: str, fim: str
) -> pd.DataFrame:
    """Observações de uma série no período, ordenadas por data."""
    return pd.read_sql_query(
        """
        SELECT o.data, o.valor
        FROM observacoes o
        JOIN series s ON s.id = o.serie_id
        WHERE s.slug = :slug
          AND o.data BETWEEN :inicio AND :fim
        ORDER BY o.data
        """,
        conexao,
        params={"slug": slug, "inicio": inicio, "fim": fim},
        parse_dates=["data"],
    )


def media_movel(
    conexao: sqlite3.Connection, slug: str, inicio: str, fim: str, janela: int
) -> pd.DataFrame:
    """Série com média móvel calculada por window function no SQL."""
    janela = int(janela)
    if janela < 1:
        raise ValueError("A janela da média móvel deve ser >= 1")
    # O tamanho do frame da window function não aceita parâmetro bind,
    # por isso o inteiro (já validado) é interpolado diretamente.
    sql = f"""
        SELECT o.data,
               o.valor,
               AVG(o.valor) OVER (
                   ORDER BY o.data
                   ROWS BETWEEN {janela - 1} PRECEDING AND CURRENT ROW
               ) AS media_movel
        FROM observacoes o
        JOIN series s ON s.id = o.serie_id
        WHERE s.slug = :slug
          AND o.data BETWEEN :inicio AND :fim
        ORDER BY o.data
    """
    return pd.read_sql_query(
        sql,
        conexao,
        params={"slug": slug, "inicio": inicio, "fim": fim},
        parse_dates=["data"],
    )


def resumo_indicador(
    conexao: sqlite3.Connection, slug: str, inicio: str, fim: str
) -> dict:
    """Resumo do período: último valor, mínimo, máximo e variação percentual
    entre a primeira e a última observação — tudo agregado em SQL."""
    linha = conexao.execute(
        """
        WITH periodo AS (
            SELECT o.data, o.valor
            FROM observacoes o
            JOIN series s ON s.id = o.serie_id
            WHERE s.slug = :slug
              AND o.data BETWEEN :inicio AND :fim
        )
        SELECT (SELECT valor FROM periodo ORDER BY data DESC LIMIT 1) AS ultimo_valor,
               (SELECT data  FROM periodo ORDER BY data DESC LIMIT 1) AS ultima_data,
               MIN(valor) AS minimo,
               MAX(valor) AS maximo,
               100.0 * ((SELECT valor FROM periodo ORDER BY data DESC LIMIT 1)
                        - (SELECT valor FROM periodo ORDER BY data ASC LIMIT 1))
                     / NULLIF((SELECT valor FROM periodo ORDER BY data ASC LIMIT 1), 0)
                   AS variacao_pct
        FROM periodo
        """,
        {"slug": slug, "inicio": inicio, "fim": fim},
    ).fetchone()

    chaves = ("ultimo_valor", "ultima_data", "minimo", "maximo", "variacao_pct")
    return dict(zip(chaves, linha)) if linha else dict.fromkeys(chaves)


def variacao_percentual(
    conexao: sqlite3.Connection, slug: str, inicio: str, fim: str
) -> pd.DataFrame:
    """Variação percentual entre observações consecutivas, via LAG."""
    return pd.read_sql_query(
        """
        SELECT o.data,
               o.valor,
               100.0 * (o.valor - LAG(o.valor) OVER (ORDER BY o.data))
                     / NULLIF(LAG(o.valor) OVER (ORDER BY o.data), 0) AS variacao_pct
        FROM observacoes o
        JOIN series s ON s.id = o.serie_id
        WHERE s.slug = :slug
          AND o.data BETWEEN :inicio AND :fim
        ORDER BY o.data
        """,
        conexao,
        params={"slug": slug, "inicio": inicio, "fim": fim},
        parse_dates=["data"],
    )


_SQL_PARES_MENSAIS = """
    WITH mensal_a AS (
        SELECT strftime('%Y-%m', o.data) AS competencia, AVG(o.valor) AS valor
        FROM observacoes o
        JOIN series s ON s.id = o.serie_id
        WHERE s.slug = :slug_a AND o.data BETWEEN :inicio AND :fim
        GROUP BY competencia
    ),
    mensal_b AS (
        SELECT strftime('%Y-%m', o.data) AS competencia, AVG(o.valor) AS valor
        FROM observacoes o
        JOIN series s ON s.id = o.serie_id
        WHERE s.slug = :slug_b AND o.data BETWEEN :inicio AND :fim
        GROUP BY competencia
    )
    SELECT a.competencia,
           a.valor AS valor_a,
           b.valor AS valor_b
    FROM mensal_a a
    JOIN mensal_b b USING (competencia)
    ORDER BY a.competencia
"""


def pares_mensais(
    conexao: sqlite3.Connection, slug_a: str, slug_b: str, inicio: str, fim: str
) -> pd.DataFrame:
    """Duas séries alinhadas por competência mensal (média do mês, em SQL).

    Séries diárias e mensais ficam comparáveis na mesma granularidade.
    """
    return pd.read_sql_query(
        _SQL_PARES_MENSAIS,
        conexao,
        params={"slug_a": slug_a, "slug_b": slug_b, "inicio": inicio, "fim": fim},
    )


def correlacao(
    conexao: sqlite3.Connection, slug_a: str, slug_b: str, inicio: str, fim: str
) -> float | None:
    """Coeficiente de correlação de Pearson entre duas séries.

    Os somatórios são agregados em SQL sobre os pares mensais alinhados;
    apenas a raiz quadrada final é feita em Python (a função sqrt não está
    disponível em todas as builds do SQLite).
    """
    linha = conexao.execute(
        f"""
        WITH pareado AS ({_SQL_PARES_MENSAIS})
        SELECT COUNT(*)                 AS n,
               SUM(valor_a)             AS soma_x,
               SUM(valor_b)             AS soma_y,
               SUM(valor_a * valor_b)   AS soma_xy,
               SUM(valor_a * valor_a)   AS soma_x2,
               SUM(valor_b * valor_b)   AS soma_y2
        FROM pareado
        """,
        {"slug_a": slug_a, "slug_b": slug_b, "inicio": inicio, "fim": fim},
    ).fetchone()

    n, soma_x, soma_y, soma_xy, soma_x2, soma_y2 = linha
    if not n or n < 2:
        return None

    numerador = n * soma_xy - soma_x * soma_y
    denominador = math.sqrt(
        (n * soma_x2 - soma_x**2) * (n * soma_y2 - soma_y**2)
    )
    if denominador == 0:
        return None
    return numerador / denominador
