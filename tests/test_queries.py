"""Testes da camada de análise em SQL."""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from radar import database, queries  # noqa: E402


@pytest.fixture
def conexao():
    conexao = database.conectar(":memory:")
    database.inicializar(conexao)
    yield conexao
    conexao.close()


def preparar_serie(conexao, slug, valores, codigo=None):
    """Insere uma série com observações mensais consecutivas a partir de jan/2024."""
    serie_id = database.upsert_serie(
        conexao,
        codigo_sgs=codigo or abs(hash(slug)) % 100000,
        slug=slug,
        nome=slug.title(),
        unidade="%",
        frequencia="mensal",
        data_inicial="2024-01-01",
    )
    observacoes = [
        (date(2024, mes + 1, 1), valor) for mes, valor in enumerate(valores)
    ]
    database.upsert_observacoes(conexao, serie_id, observacoes)
    return serie_id


def test_media_movel_de_janela_2(conexao):
    preparar_serie(conexao, "selic", [10.0, 12.0, 14.0])

    resultado = queries.media_movel(conexao, "selic", "2024-01-01", "2024-12-31", 2)

    assert list(resultado["media_movel"]) == [10.0, 11.0, 13.0]


def test_media_movel_rejeita_janela_invalida(conexao):
    with pytest.raises(ValueError):
        queries.media_movel(conexao, "selic", "2024-01-01", "2024-12-31", 0)


def test_resumo_indicador(conexao):
    preparar_serie(conexao, "selic", [10.0, 15.0, 12.0])

    resumo = queries.resumo_indicador(conexao, "selic", "2024-01-01", "2024-12-31")

    assert resumo["ultimo_valor"] == 12.0
    assert resumo["minimo"] == 10.0
    assert resumo["maximo"] == 15.0
    assert resumo["variacao_pct"] == pytest.approx(20.0)  # de 10.0 para 12.0


def test_variacao_percentual_usa_lag(conexao):
    preparar_serie(conexao, "ipca", [100.0, 110.0, 99.0])

    resultado = queries.variacao_percentual(conexao, "ipca", "2024-01-01", "2024-12-31")

    assert resultado["variacao_pct"].iloc[0] != resultado["variacao_pct"].iloc[0]  # NaN
    assert resultado["variacao_pct"].iloc[1] == pytest.approx(10.0)
    assert resultado["variacao_pct"].iloc[2] == pytest.approx(-10.0)


def test_correlacao_perfeita_positiva(conexao):
    preparar_serie(conexao, "selic", [10.0, 11.0, 12.0, 13.0], codigo=1)
    preparar_serie(conexao, "inadimplencia", [3.0, 3.5, 4.0, 4.5], codigo=2)

    coeficiente = queries.correlacao(
        conexao, "selic", "inadimplencia", "2024-01-01", "2024-12-31"
    )

    assert coeficiente == pytest.approx(1.0)


def test_correlacao_perfeita_negativa(conexao):
    preparar_serie(conexao, "selic", [10.0, 11.0, 12.0], codigo=1)
    preparar_serie(conexao, "credito", [500.0, 400.0, 300.0], codigo=2)

    coeficiente = queries.correlacao(
        conexao, "selic", "credito", "2024-01-01", "2024-12-31"
    )

    assert coeficiente == pytest.approx(-1.0)


def test_correlacao_sem_meses_em_comum_retorna_none(conexao):
    preparar_serie(conexao, "selic", [10.0], codigo=1)

    coeficiente = queries.correlacao(
        conexao, "selic", "inexistente", "2024-01-01", "2024-12-31"
    )

    assert coeficiente is None
