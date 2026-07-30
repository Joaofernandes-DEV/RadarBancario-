"""Testes da camada de coleta e do parsing do cliente SGS."""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from radar import database  # noqa: E402
from radar.collector import SerieConfig, coletar_serie  # noqa: E402
from radar.sgs_client import _converter_registros  # noqa: E402


class ClienteFalso:
    """Substitui o SGSClient nos testes, registrando o período pedido."""

    def __init__(self, observacoes):
        self.observacoes = observacoes
        self.chamadas = []

    def buscar(self, codigo_sgs, inicio, fim):
        self.chamadas.append((codigo_sgs, inicio, fim))
        return [obs for obs in self.observacoes if inicio <= obs[0] <= fim]


@pytest.fixture
def conexao():
    conexao = database.conectar(":memory:")
    database.inicializar(conexao)
    yield conexao
    conexao.close()


CONFIG_SELIC = SerieConfig(
    codigo_sgs=432,
    slug="selic",
    nome="Taxa Selic",
    unidade="% a.a.",
    frequencia="diaria",
    data_inicial=date(2024, 1, 1),
)


def test_converter_registros_parseia_datas_e_valores():
    registros = [
        {"data": "02/01/2024", "valor": "11.75"},
        {"data": "03/01/2024", "valor": "11.75"},
    ]
    assert _converter_registros(registros) == [
        (date(2024, 1, 2), 11.75),
        (date(2024, 1, 3), 11.75),
    ]


def test_converter_registros_descarta_malformados():
    registros = [
        {"data": "02/01/2024", "valor": ""},
        {"data": "data-invalida", "valor": "1.0"},
        {"data": "04/01/2024", "valor": "abc"},
        {"data": "05/01/2024", "valor": "10.5"},
    ]
    assert _converter_registros(registros) == [(date(2024, 1, 5), 10.5)]


def test_primeira_coleta_parte_da_data_inicial(conexao):
    cliente = ClienteFalso([(date(2024, 1, 2), 11.75)])

    gravadas = coletar_serie(conexao, cliente, CONFIG_SELIC)

    assert gravadas == 1
    (_, inicio, _) = cliente.chamadas[0]
    assert inicio == CONFIG_SELIC.data_inicial


def test_carga_incremental_pede_apenas_o_periodo_faltante(conexao):
    cliente = ClienteFalso([(date(2024, 1, 2), 11.75), (date(2024, 1, 3), 11.80)])
    coletar_serie(conexao, cliente, CONFIG_SELIC)

    # Segunda execução: deve pedir a partir do dia seguinte à última data gravada.
    coletar_serie(conexao, cliente, CONFIG_SELIC)

    (_, inicio_segunda, _) = cliente.chamadas[1]
    assert inicio_segunda == date(2024, 1, 4)


def test_recoleta_e_idempotente(conexao):
    cliente = ClienteFalso([(date(2024, 1, 2), 11.75)])
    coletar_serie(conexao, cliente, CONFIG_SELIC)
    coletar_serie(conexao, cliente, CONFIG_SELIC)

    total = conexao.execute("SELECT COUNT(*) FROM observacoes").fetchone()[0]
    assert total == 1
