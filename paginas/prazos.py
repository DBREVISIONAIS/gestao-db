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
    "Próximos 7 dias": ["VENCE HOJE", "PRÓXIMOS 7 DIAS"],
    "8 a 15 dias": ["DE 8 A 15 DIAS"],
    "Críticos": ["VENCIDO", "VENCE HOJE", "PRÓXIMOS 7 DIAS"],
}

# Como o registro é controlado. COM FATAL é data válida no campo Fatal.
# AGUARDA é situação escrita no próprio campo, e não ausência de dado.
ATALHOS_FATAL = {
    "Todos": [],
    "Com prazo fatal": ["COM FATAL"],
    "Em aguarda": ["AGUARDA"],
    "Fatal vazio": ["FATAL VAZIO", "FATAL INVÁLIDO"],
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
    "status_padronizado": "Status padronizado",
    "situacao": "Situação",
    "resumo_resultados": "Resultado processual",
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
    linha_atalhos = st.columns(2)
    with linha_atalhos[0]:
        atalho = st.segmented_control(
            "Atalhos por vencimento",
            list(ATALHOS),
            default="Todos",
            key="pz_atalho",
            label_visibility="collapsed",
        )
    with linha_atalhos[1]:
        atalho_fatal = st.segmented_control(
            "Atalhos por controle",
            list(ATALHOS_FATAL),
            default="Todos",
            key="pz_atalho_fatal",
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

        linha2 = st.columns(3)
        with linha2[0]:
            situacoes_fatal = ui.multiselecao(
                "Situação do fatal", ui.opcoes(prazos, "situacao_fatal"), "pz_sitfatal"
            )
        with linha2[1]:
            fontes = ui.multiselecao(
                "Fonte da data de controle", ui.opcoes(prazos, "fonte_data"), "pz_fonte"
            )
        with linha2[2]:
            somente_abertos = st.checkbox("Somente em aberto", value=True, key="pz_ab")

        linha3 = st.columns(1)
        with linha3[0]:
            filtrados = ui.filtro_periodo(
                prazos, "data_controle", "Data de controle entre", "pz_periodo"
            )

    situacoes = ATALHOS.get(atalho or "Todos", [])
    filtrados = ui.aplicar_multiselecao(filtrados, "situacao", situacoes)
    filtrados = ui.aplicar_multiselecao(
        filtrados, "situacao_fatal", ATALHOS_FATAL.get(atalho_fatal or "Todos", [])
    )
    filtrados = ui.aplicar_multiselecao(filtrados, "situacao_fatal", situacoes_fatal)
    filtrados = ui.aplicar_multiselecao(filtrados, "fonte_data", fontes)
    filtrados = ui.aplicar_multiselecao(filtrados, "responsavel", responsaveis)
    filtrados = ui.aplicar_multiselecao(filtrados, "delegado", delegados)
    filtrados = ui.aplicar_multiselecao(filtrados, "status", status)
    filtrados = ui.aplicar_multiselecao(filtrados, "tipo_prazo", tipos)
    filtrados = ui.busca_texto(filtrados, COLUNAS_BUSCA, busca)
    if somente_abertos:
        filtrados = filtrados[~filtrados["encerrado"]]

    ui.resumo_filtro(len(prazos), len(filtrados))

    abertos = filtrados[~filtrados["encerrado"]]

    # Primeira faixa: distribuicao pela data de controle, que e o Prazo
    # Fatal e, quando ele nao existe, a Data Final.
    colunas = st.columns(6)
    ui.cartao(colunas[0], "No filtro", len(filtrados))
    for indice, (rotulo, situacao) in enumerate(
        [
            ("Vencidos", "VENCIDO"),
            ("Vencem hoje", "VENCE HOJE"),
            ("Próximos 7 dias", "PRÓXIMOS 7 DIAS"),
            ("De 8 a 15 dias", "DE 8 A 15 DIAS"),
            ("Após 15 dias", "APÓS 15 DIAS"),
        ],
        start=1,
    ):
        ui.cartao(colunas[indice], rotulo, int((abertos["situacao"] == situacao).sum()))

    # Segunda faixa: qualidade cadastral e etapa do fluxo.
    colunas = st.columns(6)
    ui.cartao(colunas[0], "Abertos", len(abertos))
    ui.cartao(
        colunas[1], "Protocolados",
        int((filtrados["status_padronizado"] == "PROTOCOLADO").sum()),
    )
    ui.cartao(
        colunas[2], "Pendentes",
        int((abertos["status_padronizado"] == "PENDENTE").sum()),
    )
    ui.cartao(
        colunas[3], "Para revisar",
        int((abertos["status_padronizado"] == "PARA REVISAR").sum()),
    )
    ui.cartao(
        colunas[4], "Sem status",
        int((abertos["status_padronizado"] == "SEM STATUS").sum()),
        "Pendência de preenchimento no controle.",
    )
    ui.cartao(
        colunas[5], "Sem responsável",
        int((abertos["responsavel"] == "SEM RESPONSÁVEL").sum()),
        "Pendência de atribuição.",
    )

    # Terceira faixa: como o registro é controlado.
    colunas = st.columns(4)
    ui.cartao(
        colunas[0], "Com prazo fatal",
        int((abertos["situacao_fatal"] == "COM FATAL").sum()),
        "Data válida no campo Fatal.",
    )
    ui.cartao(
        colunas[1], "Em aguarda",
        int((abertos["situacao_fatal"] == "AGUARDA").sum()),
        "AGUARDA escrito no campo Fatal. É situação declarada, não falta de dado.",
    )
    ui.cartao(
        colunas[2], "Fatal vazio ou inválido",
        int(abertos["situacao_fatal"].isin(["FATAL VAZIO", "FATAL INVÁLIDO"]).sum()),
        "Pendência de preenchimento.",
    )
    ui.cartao(
        colunas[3], "Controlados pela data final",
        int((abertos["fonte_data"] == "DATA FINAL").sum()),
        "Sem fatal válido, mas com Data Final para acompanhamento.",
    )

    # Quarta faixa: indicadores de tempo, iguais aos do DASH PRAZOS.
    dias = filtrados["dias_evento_fatal"].dropna()
    colunas = st.columns(4)
    ui.cartao(
        colunas[0], "Média evento até fatal",
        f"{dias.mean():.1f}".replace(".", ",") if len(dias) else "—",
        f"Dias corridos em {len(dias)} registro(s) com ambas as datas.",
    )
    ui.cartao(colunas[1], "Prazo mais curto", int(dias.min()) if len(dias) else "—")
    ui.cartao(colunas[2], "Prazo mais longo", int(dias.max()) if len(dias) else "—")
    ui.cartao(
        colunas[3], "Sem data de controle",
        int((filtrados["fonte_data"] == "SEM DATA").sum()),
        "Sem Prazo Fatal válido e sem Data Final.",
    )

    esquerda, direita = st.columns(2)
    with esquerda:
        st.markdown("#### Composição do controle")
        composicao = (
            filtrados.groupby(["situacao_fatal", "fonte_data"])
            .size()
            .reset_index(name="Quantidade")
            .sort_values("Quantidade", ascending=False)
        )
        ui.tabela(
            composicao,
            {
                "situacao_fatal": "Situação do fatal",
                "fonte_data": "Fonte da data",
                "Quantidade": "Quantidade",
            },
            "Sem registros no filtro.",
        )
    with direita:
        st.markdown("#### Aguarda por responsável")
        aguarda = filtrados[filtrados["situacao_fatal"] == "AGUARDA"]
        if aguarda.empty:
            st.info("Nenhum registro em aguarda no filtro.")
        else:
            serie = (
                aguarda.groupby("responsavel").size().reset_index(name="Quantidade")
                .sort_values("Quantidade", ascending=False)
            )
            figura = px.bar(serie, x="Quantidade", y="responsavel", orientation="h")
            figura.update_layout(
                height=320, yaxis={"categoryorder": "total ascending", "title": ""}
            )
            st.plotly_chart(figura, width="stretch")

    esquerda, direita = st.columns(2)
    with esquerda:
        st.markdown("#### Por responsável")
        _barra(filtrados, "responsavel", "Responsável")
    with direita:
        st.markdown("#### Por tipo de prazo")
        _barra(filtrados, "tipo_prazo", "Tipo")

    st.markdown("#### Detalhamento")
    ordenado = filtrados.sort_values("data_controle", na_position="last")
    ui.tabela(
        ui.formatar_datas(
            ordenado, ["data_evento", "data_final", "prazo_fatal", "data_controle"]
        ),
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
