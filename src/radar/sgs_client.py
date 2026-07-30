"""Cliente HTTP da API SGS (Sistema Gerenciador de Séries Temporais) do BCB.

Responsabilidade única: buscar observações de uma série em um intervalo de
datas, com retry, timeout e validação da resposta. Não conhece banco de
dados nem regras de negócio.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

logger = logging.getLogger(__name__)

API_URL = "https://api.bcb.gov.br/dados/serie/bcdata.sgs.{codigo}/dados"
TIMEOUT_SEGUNDOS = 30

# A API SGS limita consultas de séries diárias a janelas de ~10 anos;
# períodos maiores são quebrados em blocos.
JANELA_MAXIMA_DIAS = 3600


class SGSError(Exception):
    """Falha ao consultar ou interpretar a resposta da API SGS."""


def _criar_sessao() -> requests.Session:
    """Cria uma sessão HTTP com retry e backoff exponencial."""
    retry = Retry(
        total=3,
        backoff_factor=1,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=("GET",),
    )
    sessao = requests.Session()
    sessao.mount("https://", HTTPAdapter(max_retries=retry))
    return sessao


def _converter_registros(registros: list[dict]) -> list[tuple[date, float]]:
    """Converte o JSON da API ({'data': 'dd/mm/aaaa', 'valor': '1.23'}) em
    tuplas (date, float), descartando registros vazios ou malformados."""
    observacoes: list[tuple[date, float]] = []
    for registro in registros:
        bruto_data = registro.get("data", "")
        bruto_valor = registro.get("valor", "")
        if not bruto_valor:
            continue
        try:
            quando = datetime.strptime(bruto_data, "%d/%m/%Y").date()
            valor = float(bruto_valor)
        except ValueError:
            logger.warning("Registro ignorado por formato inválido: %r", registro)
            continue
        observacoes.append((quando, valor))
    return observacoes


class SGSClient:
    """Consulta séries temporais na API pública do Banco Central."""

    def __init__(self, sessao: requests.Session | None = None) -> None:
        self._sessao = sessao or _criar_sessao()

    def buscar(
        self, codigo_sgs: int, inicio: date, fim: date
    ) -> list[tuple[date, float]]:
        """Busca as observações da série no intervalo [inicio, fim].

        Períodos maiores que a janela máxima da API são consultados em
        blocos e concatenados. Levanta SGSError em falha de rede ou
        resposta inválida.
        """
        if inicio > fim:
            return []

        observacoes: list[tuple[date, float]] = []
        bloco_inicio = inicio
        while bloco_inicio <= fim:
            bloco_fim = min(bloco_inicio + timedelta(days=JANELA_MAXIMA_DIAS), fim)
            observacoes.extend(self._buscar_bloco(codigo_sgs, bloco_inicio, bloco_fim))
            bloco_inicio = bloco_fim + timedelta(days=1)
        return observacoes

    def _buscar_bloco(
        self, codigo_sgs: int, inicio: date, fim: date
    ) -> list[tuple[date, float]]:
        url = API_URL.format(codigo=codigo_sgs)
        params = {
            "formato": "json",
            "dataInicial": inicio.strftime("%d/%m/%Y"),
            "dataFinal": fim.strftime("%d/%m/%Y"),
        }
        logger.debug(
            "Consultando SGS %s de %s a %s", codigo_sgs, params["dataInicial"], params["dataFinal"]
        )
        try:
            resposta = self._sessao.get(url, params=params, timeout=TIMEOUT_SEGUNDOS)
            resposta.raise_for_status()
        except requests.RequestException as exc:
            raise SGSError(f"Falha de rede ao consultar a série {codigo_sgs}: {exc}") from exc

        try:
            registros = resposta.json()
        except ValueError as exc:
            raise SGSError(
                f"Resposta da série {codigo_sgs} não é um JSON válido"
            ) from exc

        if not isinstance(registros, list):
            raise SGSError(
                f"Resposta inesperada da série {codigo_sgs}: {type(registros).__name__}"
            )
        return _converter_registros(registros)
