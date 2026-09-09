"""
Dashboard de prazos. Substitui a aba DASH PRAZOS do Google Sheets.

O corpo da tela e um fragmento: mexer em qualquer filtro reexecuta
apenas esta funcao, sem reprocessar a barra lateral nem consultar o
Google Sheets de novo.
"""

from __future__ import annotations

import pandas as pd
import plotly.express as px
import streamlit as st

from db import auth
from paginas import componentes as ui

ATALHOS = {
    "Todos": [],
    "Vencidos": ["VENCIDO"],
    "Vencem hoje": ["VENCE HOJE"],
    "Próximos 7 dias": ["VENCE HOJE", "VENCE EM 7 DIAS"],
    "Críticos": ["VENCIDO", "VENCE HOJE", "VENCE EM 7 DIAS"],
}

COLUNAS_BUSCA = ["autor", "conteudo", "observacao", "responsavel", "delegado", "status"]

COLUNAS_TABELA = {
    "prazo_fatal": "Fatal",
    "data_evento": "Data do evento",
    "data_final": "Data final",
    "autor": "Autor",
    "conteudo": "Conteúdo",
    "tipo_prazo": "Tipo",
    "responsavel": "Responsável",
    "delegado": "Delegado",
    "status": "Status",
    "situacao": "Situação",
    "verificacao_controladoria": "Controladoria",
    "link_bitrix": "Bitrix",
    "linha_origem": "Linha",
}


def render(prazos: pd.DataFrame) -> None:
    st.subheader("Prazos")

    prazos = auth.aplicar_recorte(prazos)
    if prazos.empty:
        st.info("Nenhum prazo disponível.")
        return

    painel(prazos)


@st.fragment
def painel(prazos: pd.DataFrame) -> None:
    atalho = st.segmented_control(
        "Atalhos",
        list(ATALHOS),
        default="Todos",
        key="pz_atalho",
        label_visibility="collapsed",
    )

    busca = st.text_input(
        "Buscar",
        placeholder="Autor, conteúdo, responsável...",
        key="pz_busca",
        label_visibility="collapsed",
    )

    with st.expander("Filtros", expanded=False):
        linha1 = st.columns(4)
        with linha1[0]:
            responsaveis = ui.multiselecao(
                "Responsável", ui.opcoes(prazos, "responsavel"), "pz_resp"
            )
        with linha1[1]:
            delegados = ui.multiselecao(
                "Delegado", ui.opcoes(prazos, "delegado"), "pz_deleg"
            )
        with linha1[2]:
            status = ui.multiselecao("Status", ui.opcoes(prazos, "status"), "pz_status")
        with linha1[3]:
            tipos = ui.multiselecao(
                "Tipo de prazo", ui.opcoes(prazos, "tipo_prazo"), "pz_tipo"
            )

        linha2 = st.columns([1, 2])
        with linha2[0]:
            somente_abertos = st.checkbox("Somente em aberto", value=True, key="pz_ab")
        with linha2[1]:
            filtrados = ui.filtro_periodo(
                prazos, "prazo_fatal", "Prazo fatal entre", "pz_periodo"
            )

    situacoes = ATALHOS.get(atalho or "Todos", [])
    filtrados = ui.aplicar_multiselecao(filtrados, "situacao", situacoes)
    filtrados = ui.aplicar_multiselecao(filtrados, "responsavel", responsaveis)
    filtrados = ui.aplicar_multiselecao(filtrados, "delegado", delegados)
    filtrados = ui.aplicar_multiselecao(filtrados, "status", status)
    filtrados = ui.aplicar_multiselecao(filtrados, "tipo_prazo", tipos)
    filtrados = ui.busca_texto(filtrados, COLUNAS_BUSCA, busca)
    if somente_abertos:
        filtrados = filtrados[~filtrados["encerrado"]]

    ui.resumo_filtro(len(prazos), len(filtrados))

    colunas = st.columns(5)
    ui.cartao(colunas[0], "No filtro", len(filtrados))
    for indice, situacao in enumerate(
        ["VENCIDO", "VENCE HOJE", "VENCE EM 7 DIAS", "NO PRAZO"], start=1
    ):
        ui.cartao(
            colunas[indice],
            situacao.capitalize(),
            int((filtrados["situacao"] == situacao).sum()),
        )

    esquerda, direita = st.columns(2)
    with esquerda:
        st.markdown("#### Por responsável")
        _barra(filtrados, "responsavel", "Responsável")
    with direita:
        st.markdown("#### Por tipo de prazo")
        _barra(filtrados, "tipo_prazo", "Tipo")

    st.markdown("#### Detalhamento")
    ordenado = filtrados.sort_values("prazo_fatal", na_position="last")
    ui.tabela(
        ui.formatar_datas(ordenado, ["data_evento", "data_final", "prazo_fatal"]),
        COLUNAS_TABELA,
        "Nenhum prazo corresponde aos filtros aplicados.",
    )

    st.download_button(
        "Exportar CSV do recorte",
        ordenado.to_csv(index=False, sep=";").encode("utf-8-sig"),
        file_name="prazos.csv",
        mime="text/csv",
    )


def _barra(dados: pd.DataFrame, coluna: str, rotulo: str) -> None:
    resumo = dados[coluna].value_counts().reset_index()
    resumo.columns = [rotulo, "Quantidade"]
    if resumo.empty:
        st.info("Sem registros no filtro.")
        return
    figura = px.bar(resumo, x="Quantidade", y=rotulo, orientation="h")
    figura.update_layout(height=340, yaxis={"categoryorder": "total ascending"})
    st.plotly_chart(figura, width="stretch")
