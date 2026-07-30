"""Ponto de entrada da coleta do Radar Bancário.

Uso:
    python main.py                 # coleta incremental de todas as séries
    python main.py --db data/x.db  # caminho alternativo do banco
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from radar import collector, database  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Coleta indicadores do BCB (API SGS)")
    parser.add_argument(
        "--db",
        default=str(database.CAMINHO_DB_PADRAO),
        help="Caminho do banco SQLite (padrão: data/radar.db)",
    )
    parser.add_argument(
        "--config",
        default=str(collector.CAMINHO_CONFIG_PADRAO),
        help="Caminho do catálogo de séries (padrão: config/series.yaml)",
    )
    argumentos = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
    )
    logger = logging.getLogger("radar.main")

    conexao = database.conectar(argumentos.db)
    try:
        catalogo = collector.carregar_catalogo(argumentos.config)
        resultados = collector.coletar_todas(conexao, catalogo=catalogo)
    finally:
        conexao.close()

    total_gravadas = sum(r.gravadas for r in resultados)
    falhas = [r for r in resultados if r.erro]

    logger.info("Coleta concluída: %d observações gravadas", total_gravadas)
    for resultado in falhas:
        logger.warning("Série com falha: %s (%s)", resultado.slug, resultado.erro)

    # Falha total (nenhuma série coletada com sucesso) retorna código de erro;
    # falhas parciais não derrubam o pipeline.
    if resultados and len(falhas) == len(resultados):
        logger.error("Todas as séries falharam — verifique a conectividade com a API")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
