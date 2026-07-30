"""Camada de coleta: orquestra a busca na API SGS e a gravação no banco.

Pontos de engenharia de dados aplicados aqui:
- Carga incremental: consulta a última data gravada por série e pede à API
  apenas o período faltante.
- Isolamento de falhas: erro em uma série não interrompe a coleta das demais.
"""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

import yaml

from . import database
from .sgs_client import SGSClient, SGSError

logger = logging.getLogger(__name__)

CAMINHO_CONFIG_PADRAO = database.RAIZ_PROJETO / "config" / "series.yaml"

CAMPOS_OBRIGATORIOS = (
    "codigo_sgs",
    "slug",
    "nome",
    "unidade",
    "frequencia",
    "data_inicial",
)


@dataclass(frozen=True)
class SerieConfig:
    """Configuração de uma série do catálogo (config/series.yaml)."""

    codigo_sgs: int
    slug: str
    nome: str
    unidade: str
    frequencia: str
    data_inicial: date


@dataclass
class ResultadoColeta:
    """Resultado da coleta de uma série, para o resumo final."""

    slug: str
    gravadas: int = 0
    erro: str | None = None


def carregar_catalogo(caminho: str | Path = CAMINHO_CONFIG_PADRAO) -> list[SerieConfig]:
    """Lê e valida o catálogo de séries do arquivo YAML."""
    conteudo = yaml.safe_load(Path(caminho).read_text(encoding="utf-8"))
    if not conteudo or "series" not in conteudo:
        raise ValueError(f"Catálogo inválido: chave 'series' ausente em {caminho}")

    catalogo: list[SerieConfig] = []
    for entrada in conteudo["series"]:
        faltando = [campo for campo in CAMPOS_OBRIGATORIOS if campo not in entrada]
        if faltando:
            raise ValueError(
                f"Série {entrada.get('slug', '?')} sem campos obrigatórios: {faltando}"
            )
        catalogo.append(
            SerieConfig(
                codigo_sgs=int(entrada["codigo_sgs"]),
                slug=str(entrada["slug"]),
                nome=str(entrada["nome"]),
                unidade=str(entrada["unidade"]),
                frequencia=str(entrada["frequencia"]),
                data_inicial=datetime.strptime(
                    str(entrada["data_inicial"]), "%Y-%m-%d"
                ).date(),
            )
        )
    return catalogo


def coletar_serie(
    conexao: sqlite3.Connection, cliente: SGSClient, config: SerieConfig
) -> int:
    """Coleta uma série de forma incremental; retorna o nº de linhas gravadas."""
    serie_id = database.upsert_serie(
        conexao,
        codigo_sgs=config.codigo_sgs,
        slug=config.slug,
        nome=config.nome,
        unidade=config.unidade,
        frequencia=config.frequencia,
        data_inicial=config.data_inicial.isoformat(),
    )

    ultima = database.ultima_data(conexao, serie_id)
    inicio = ultima + timedelta(days=1) if ultima else config.data_inicial
    fim = date.today()

    if inicio > fim:
        logger.info("[%s] já está atualizada (última data: %s)", config.slug, ultima)
        return 0

    logger.info("[%s] coletando de %s a %s", config.slug, inicio, fim)
    observacoes = cliente.buscar(config.codigo_sgs, inicio, fim)
    gravadas = database.upsert_observacoes(conexao, serie_id, observacoes)
    logger.info("[%s] %d observações gravadas", config.slug, gravadas)
    return gravadas


def coletar_todas(
    conexao: sqlite3.Connection,
    cliente: SGSClient | None = None,
    catalogo: list[SerieConfig] | None = None,
) -> list[ResultadoColeta]:
    """Executa a coleta de todo o catálogo, isolando falhas por série."""
    cliente = cliente or SGSClient()
    catalogo = catalogo if catalogo is not None else carregar_catalogo()
    database.inicializar(conexao)

    resultados: list[ResultadoColeta] = []
    for config in catalogo:
        resultado = ResultadoColeta(slug=config.slug)
        try:
            resultado.gravadas = coletar_serie(conexao, cliente, config)
        except SGSError as exc:
            resultado.erro = str(exc)
            logger.error("[%s] falha na coleta: %s", config.slug, exc)
        resultados.append(resultado)
    return resultados
