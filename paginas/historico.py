"""
Historico e auditoria.

Le a planilha de logs. Substitui a necessidade de alguem abrir uma aba
com dezenas de milhares de linhas de LOG_ALTERACOES dentro do Sheets.
"""

from __future__ import annotations

import pandas as pd
import streamlit as st

from db import auth
from paginas import componentes as ui

COLUNAS_HISTORICO = {
    "data_hora": "Data e hora",
    "aba_origem": "Aba",
    "linha": "Linha",
    "cliente_autor": "Cliente / Autor",
    "cabecalho": "Campo",
    "valor_anterior": "De",
    "valor_novo": "Para",
    "tipo_evento": "Evento",
    "editor": "Editor",
}


def render(logs: pd.DataFrame, base_ids: pd.DataFrame) -> None:
    st.subheader("Histórico e auditoria")

    if logs.empty:
        st.info(
            "Nenhum histórico disponível ainda. O log continua sendo gravado "
            "na planilha principal. Para que ele apareça aqui, instale o "
            "arquivo PATCH_LOGS_EXTERNOS.gs no Apps Script da planilha "
            "principal e execute a migração pelo menu Gestão DB — Migração."
        )
        return

    painel(logs, base_ids)


@st.fragment
def painel(logs: pd.DataFrame, base_ids: pd.DataFrame) -> None:
    regras = auth.regras_atuais()
    dados = logs.copy()

    if regras["somente_proprios"]:
        alvo = str(auth.usuario_logado().get("responsavel", "")).strip().upper()
        dados = dados[
            dados["responsavel_tecnico"].astype(str).str.upper() == alvo
        ] if alvo else dados.iloc[0:0]

    with st.expander("Filtros", expanded=True):
        colunas = st.columns(4)
        with colunas[0]:
            busca = st.text_input("Cliente ou autor", key="hist_busca")
        with colunas[1]:
            abas = ui.multiselecao("Aba", ui.opcoes(dados, "aba_origem"), "hist_aba")
        with colunas[2]:
            eventos = ui.multiselecao(
                "Evento", ui.opcoes(dados, "tipo_evento"), "hist_evento"
            )
        with colunas[3]:
            editores = ui.multiselecao(
                "Editor", ui.opcoes(dados, "editor"), "hist_editor"
            )
        dados = ui.filtro_periodo(dados, "data_hora", "Período", "hist_periodo")

    if busca:
        dados = dados[
            dados["cliente_autor"].astype(str).str.contains(busca, case=False, na=False)
        ]
    dados = ui.aplicar_multiselecao(dados, "aba_origem", abas)
    dados = ui.aplicar_multiselecao(dados, "tipo_evento", eventos)
    dados = ui.aplicar_multiselecao(dados, "editor", editores)

    st.caption(f"{len(dados)} registro(s) no filtro atual.")

    visao = dados.copy()
    visao["data_hora"] = pd.to_datetime(visao["data_hora"], errors="coerce").dt.strftime(
        "%d/%m/%Y %H:%M:%S"
    )
    ui.tabela(visao.head(1000), COLUNAS_HISTORICO, "Nenhum registro no filtro.")

    if len(dados) > 1000:
        st.caption("Exibindo os 1.000 registros mais recentes. Refine o filtro ou exporte o CSV.")

    st.download_button(
        "Exportar CSV do recorte",
        dados.to_csv(index=False, sep=";").encode("utf-8-sig"),
        file_name="historico.csv",
        mime="text/csv",
    )

    if not base_ids.empty:
        with st.expander("BASE IDS"):
            st.dataframe(base_ids, width="stretch", hide_index=True)
