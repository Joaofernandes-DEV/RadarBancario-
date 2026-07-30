"""Dashboard do Radar Bancário — Streamlit + Plotly.

Camada de apresentação: lê o SQLite via as consultas SQL de radar.queries
e exibe abas por indicador, filtros de período e o painel de correlação.

Executar a partir da raiz do projeto:
    streamlit run dashboard/app.py
"""

from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

RAIZ_PROJETO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ_PROJETO / "src"))

from radar import database, queries  # noqa: E402

CAMINHO_DB = database.CAMINHO_DB_PADRAO

st.set_page_config(page_title="Radar Bancário", page_icon="📡", layout="wide")


# ---------------------------------------------------------------------------
# Acesso a dados (cacheado — o Streamlit reexecuta o script a cada interação)
# ---------------------------------------------------------------------------

@st.cache_data(ttl=3600)
def carregar_series() -> pd.DataFrame:
    with database.conectar(CAMINHO_DB) as conexao:
        return queries.listar_series(conexao)


@st.cache_data(ttl=3600)
def carregar_media_movel(slug: str, inicio: str, fim: str, janela: int) -> pd.DataFrame:
    with database.conectar(CAMINHO_DB) as conexao:
        return queries.media_movel(conexao, slug, inicio, fim, janela)


@st.cache_data(ttl=3600)
def carregar_resumo(slug: str, inicio: str, fim: str) -> dict:
    with database.conectar(CAMINHO_DB) as conexao:
        return queries.resumo_indicador(conexao, slug, inicio, fim)


@st.cache_data(ttl=3600)
def carregar_correlacao(slug_a: str, slug_b: str, inicio: str, fim: str):
    with database.conectar(CAMINHO_DB) as conexao:
        pares = queries.pares_mensais(conexao, slug_a, slug_b, inicio, fim)
        coeficiente = queries.correlacao(conexao, slug_a, slug_b, inicio, fim)
    return pares, coeficiente


# ---------------------------------------------------------------------------
# Componentes de interface
# ---------------------------------------------------------------------------

def formatar_valor(valor: float | None, unidade: str) -> str:
    if valor is None:
        return "—"
    if "R$ milhões" in unidade:
        return f"R$ {valor:,.0f} mi".replace(",", ".")
    if unidade.startswith("R$"):
        return f"R$ {valor:,.2f}".replace(".", "@").replace(",", ".").replace("@", ",")
    return f"{valor:,.2f} {unidade}".replace(".", "@").replace(",", ".").replace("@", ",")


def aba_indicador(serie: pd.Series, inicio: str, fim: str, janela: int) -> None:
    slug, nome, unidade = serie["slug"], serie["nome"], serie["unidade"]

    dados = carregar_media_movel(slug, inicio, fim, janela)
    if dados.empty:
        st.info("Sem dados para o período selecionado. Rode `python main.py` para coletar.")
        return

    resumo = carregar_resumo(slug, inicio, fim)
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Último valor", formatar_valor(resumo["ultimo_valor"], unidade))
    col2.metric("Mínimo do período", formatar_valor(resumo["minimo"], unidade))
    col3.metric("Máximo do período", formatar_valor(resumo["maximo"], unidade))
    variacao = resumo["variacao_pct"]
    col4.metric(
        "Variação no período",
        f"{variacao:+.1f}%" if variacao is not None else "—",
    )

    figura = go.Figure()
    figura.add_trace(
        go.Scatter(x=dados["data"], y=dados["valor"], name=nome, mode="lines")
    )
    figura.add_trace(
        go.Scatter(
            x=dados["data"],
            y=dados["media_movel"],
            name=f"Média móvel ({janela})",
            mode="lines",
            line=dict(dash="dash"),
        )
    )
    figura.update_layout(
        yaxis_title=unidade,
        legend=dict(orientation="h", yanchor="bottom", y=1.02),
        margin=dict(t=30),
        hovermode="x unified",
    )
    st.plotly_chart(figura, use_container_width=True)
    st.caption(f"Última observação: {resumo['ultima_data']} · Fonte: Banco Central do Brasil (SGS)")


def aba_correlacao(series: pd.DataFrame, inicio: str, fim: str) -> None:
    st.markdown(
        "Compara dois indicadores na **granularidade mensal** (média do mês, "
        "agregada em SQL) e calcula o coeficiente de correlação de Pearson. "
        "Ex.: quando a Selic sobe, o que acontece com a inadimplência?"
    )
    nomes = dict(zip(series["nome"], series["slug"]))
    col_a, col_b = st.columns(2)
    nome_a = col_a.selectbox("Indicador A", list(nomes), index=0)
    indice_b = 1 if len(nomes) > 1 else 0
    nome_b = col_b.selectbox("Indicador B", list(nomes), index=indice_b)

    if nomes[nome_a] == nomes[nome_b]:
        st.warning("Escolha dois indicadores diferentes.")
        return

    pares, coeficiente = carregar_correlacao(nomes[nome_a], nomes[nome_b], inicio, fim)
    if pares.empty or coeficiente is None:
        st.info("Não há meses em comum entre as duas séries no período selecionado.")
        return

    st.metric("Correlação de Pearson", f"{coeficiente:+.3f}")

    dispersao = px.scatter(
        pares,
        x="valor_a",
        y="valor_b",
        hover_name="competencia",
        labels={"valor_a": nome_a, "valor_b": nome_b},
    )
    dispersao.update_layout(margin=dict(t=30))
    st.plotly_chart(dispersao, use_container_width=True)

    with st.expander("Ver séries mensais alinhadas"):
        st.dataframe(
            pares.rename(
                columns={"competencia": "Mês", "valor_a": nome_a, "valor_b": nome_b}
            ),
            use_container_width=True,
            hide_index=True,
        )


# ---------------------------------------------------------------------------
# Página
# ---------------------------------------------------------------------------

def main() -> None:
    st.title("📡 Radar Bancário")
    st.markdown(
        "Pipeline e painel de indicadores econômicos do Brasil — dados oficiais "
        "do Banco Central (API SGS), armazenados em SQL e analisados com SQL."
    )

    if not CAMINHO_DB.exists():
        st.error(
            "Banco de dados não encontrado. Execute a coleta primeiro:\n\n"
            "```\npython main.py\n```"
        )
        st.stop()

    series = carregar_series()
    if series.empty:
        st.error("Nenhuma série cadastrada no banco. Execute `python main.py`.")
        st.stop()

    with st.sidebar:
        st.header("Filtros")
        hoje = date.today()
        periodo = st.date_input(
            "Período",
            value=(hoje - timedelta(days=5 * 365), hoje),
            max_value=hoje,
        )
        if len(periodo) != 2:
            st.stop()
        inicio, fim = periodo[0].isoformat(), periodo[1].isoformat()

        janela = st.slider(
            "Janela da média móvel (observações)",
            min_value=2,
            max_value=90,
            value=30,
        )
        st.divider()
        st.caption("Fonte: Banco Central do Brasil — API SGS")

    titulos = list(series["nome"]) + ["🔗 Correlações"]
    abas = st.tabs(titulos)

    for aba, (_, serie) in zip(abas, series.iterrows()):
        with aba:
            aba_indicador(serie, inicio, fim, janela)

    with abas[-1]:
        aba_correlacao(series, inicio, fim)


main()
