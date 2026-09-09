"""
Producao.

Cruza o estado atual (planilha principal) com o historico (planilha de
logs). E aqui que a arquitetura de duas fontes se paga: quantidade de
prazos por responsavel vem da fonte operacional, e volume de eventos
por editor vem do log.
"""

from __future__ import annotations

import pandas as pd
import plotly.express as px
import streamlit as st

from db import auth
from paginas import componentes as ui

EVENTOS_RELEVANTES = (
    "LANCAMENTO_PRAZO",
    "PROTOCOLO",
    "PROTOCOLO_CLIENTE",
    "ENVIADO_REVISAO",
    "REVISAO_CONCLUIDA",
    "CONFERENCIA_BITRIX",
    "CADASTRO_CLIENTE",
)


def render(prazos: pd.DataFrame, logs: pd.DataFrame) -> None:
    st.subheader("Produção")

    prazos = auth.aplicar_recorte(prazos)
    painel(prazos, logs)


@st.fragment
def painel(prazos: pd.DataFrame, logs: pd.DataFrame) -> None:
    regras = auth.regras_atuais()

    if not prazos.empty:
        st.markdown("#### Carga atual por responsável")
        abertos = prazos[~prazos["encerrado"]]
        resumo = (
            abertos.groupby(["responsavel", "situacao"])
            .size()
            .reset_index(name="Quantidade")
        )
        if resumo.empty:
            st.info("Nenhum prazo em aberto no recorte.")
        else:
            figura = px.bar(
                resumo,
                x="Quantidade",
                y="responsavel",
                color="situacao",
                orientation="h",
                color_discrete_map=ui.CORES_SITUACAO,
            )
            figura.update_layout(
                height=420,
                yaxis={"categoryorder": "total ascending", "title": "Responsável"},
                legend_title="Situação",
            )
            st.plotly_chart(figura, width="stretch")

    if logs.empty:
        st.info("Nenhum registro no histórico de alterações.")
        return

    st.markdown("#### Eventos registrados no período")
    periodo = ui.filtro_periodo(logs, "data_hora", "Período", "prod_periodo")
    eventos = periodo[periodo["tipo_evento"].isin(EVENTOS_RELEVANTES)]

    if eventos.empty:
        st.info("Nenhum evento relevante no período selecionado.")
        return

    contagem = (
        eventos.groupby(["chave_editor", "tipo_evento"]).size().reset_index(name="Qtd")
    )
    if not regras["ver_editor"]:
        contagem["chave_editor"] = contagem["chave_editor"].astype(str).str.split("@").str[0]

    figura = px.bar(
        contagem,
        x="Qtd",
        y="chave_editor",
        color="tipo_evento",
        orientation="h",
    )
    figura.update_layout(
        height=420,
        yaxis={"categoryorder": "total ascending", "title": "Editor"},
        legend_title="Evento",
    )
    st.plotly_chart(figura, width="stretch")

    tabela_resumo = (
        eventos.pivot_table(
            index="chave_editor",
            columns="tipo_evento",
            values="id",
            aggfunc="count",
            fill_value=0,
        )
        .reset_index()
        .rename(columns={"chave_editor": "Editor"})
    )
    st.dataframe(tabela_resumo, width="stretch", hide_index=True)
