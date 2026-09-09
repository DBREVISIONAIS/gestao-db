"""
Financeiro.

Todos os valores exibidos aqui sao previsoes lancadas no CONTROLE DE
CLIENTES, e nao valores efetivamente recebidos. O rotulo deixa isso
explicito para nao gerar leitura equivocada de faturamento.
"""

from __future__ import annotations

import pandas as pd
import plotly.express as px
import streamlit as st

from db import auth
from db.normalizacao import formatar_moeda
from paginas import componentes as ui


def render(clientes: pd.DataFrame) -> None:
    st.subheader("Financeiro")
    st.caption(
        "Valores previstos, conforme lançamento no controle de clientes. "
        "Não representam valores recebidos."
    )

    if not auth.regras_atuais()["ver_financeiro"]:
        st.warning("Seu perfil não tem acesso aos dados financeiros.")
        return

    clientes = auth.aplicar_recorte(clientes)
    if clientes.empty:
        st.info("Nenhum cliente disponível.")
        return

    painel(clientes)


@st.fragment
def painel(clientes: pd.DataFrame) -> None:
    filtrados = ui.filtro_periodo(
        clientes, "data_ajuizamento", "Ajuizamento entre", "fin_periodo"
    )
    responsaveis = ui.multiselecao(
        "Responsável", ui.opcoes(filtrados, "responsavel"), "fin_resp"
    )
    filtrados = ui.aplicar_multiselecao(filtrados, "responsavel", responsaveis)

    colunas = st.columns(4)
    ui.cartao(
        colunas[0], "Honorários contratuais", formatar_moeda(filtrados["honorario_previsto"].sum())
    )
    ui.cartao(
        colunas[1],
        "Honorários sucumbenciais",
        formatar_moeda(filtrados["honorario_sucumbencial"].sum()),
    )
    ui.cartao(colunas[2], "Total previsto", formatar_moeda(filtrados["honorario_total"].sum()))
    ui.cartao(
        colunas[3], "Valor ajuizado consolidado", formatar_moeda(filtrados["valor_ajuizado"].sum())
    )

    base = filtrados.dropna(subset=["data_ajuizamento"]).copy()
    if not base.empty:
        base["competencia"] = base["data_ajuizamento"].dt.to_period("M").astype(str)

        st.markdown("#### Honorários previstos por competência")
        serie = base.groupby("competencia")[
            ["honorario_previsto", "honorario_sucumbencial"]
        ].sum().reset_index()
        figura = px.bar(
            serie,
            x="competencia",
            y=["honorario_previsto", "honorario_sucumbencial"],
            barmode="stack",
        )
        figura.update_layout(height=360, xaxis_title="Competência", yaxis_title="R$")
        st.plotly_chart(figura, width="stretch")

    st.markdown("#### Por responsável")
    por_responsavel = (
        filtrados.groupby("responsavel")
        .agg(
            clientes=("cliente", "count"),
            honorario_total=("honorario_total", "sum"),
            valor_ajuizado=("valor_ajuizado", "sum"),
        )
        .reset_index()
        .sort_values("honorario_total", ascending=False)
    )
    ui.tabela(
        ui.formatar_moedas(por_responsavel, ["honorario_total", "valor_ajuizado"]),
        {
            "responsavel": "Responsável",
            "clientes": "Clientes",
            "honorario_total": "Honorários previstos",
            "valor_ajuizado": "Valor ajuizado",
        },
        "Sem dados no filtro.",
    )

    st.markdown("#### Por serviço")
    por_servico = (
        filtrados.groupby("servico")
        .agg(
            clientes=("cliente", "count"),
            honorario_total=("honorario_total", "sum"),
        )
        .reset_index()
        .sort_values("honorario_total", ascending=False)
    )
    ui.tabela(
        ui.formatar_moedas(por_servico, ["honorario_total"]),
        {
            "servico": "Serviço",
            "clientes": "Clientes",
            "honorario_total": "Honorários previstos",
        },
        "Sem dados no filtro.",
    )
