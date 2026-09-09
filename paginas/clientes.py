"""Dashboard de clientes. Substitui a aba DASH CLIENTES do Google Sheets."""

from __future__ import annotations

import pandas as pd
import plotly.express as px
import streamlit as st

from db import auth
from paginas import componentes as ui


COLUNAS_BUSCA = ["cliente", "servico", "responsavel", "tribunal", "comentario"]


def render(clientes: pd.DataFrame) -> None:
    st.subheader("Clientes")

    clientes = auth.aplicar_recorte(clientes)
    if clientes.empty:
        st.info("Nenhum cliente disponível.")
        return

    painel(clientes)


@st.fragment
def painel(clientes: pd.DataFrame) -> None:
    regras = auth.regras_atuais()

    busca = st.text_input(
        "Buscar",
        placeholder="Cliente, serviço, responsável...",
        key="cl_busca",
        label_visibility="collapsed",
    )

    with st.expander("Filtros", expanded=False):
        linha = st.columns(4)
        with linha[0]:
            responsaveis = ui.multiselecao(
                "Responsável", ui.opcoes(clientes, "responsavel"), "cl_resp"
            )
        with linha[1]:
            servicos = ui.multiselecao(
                "Serviço", ui.opcoes(clientes, "servico"), "cl_serv"
            )
        with linha[2]:
            status = ui.multiselecao(
                "Status", ui.opcoes(clientes, "status"), "cl_status"
            )
        with linha[3]:
            resultados = ui.multiselecao(
                "Resultado da sentença",
                ui.opcoes(clientes, "resultado_sentenca"),
                "cl_result",
            )
        filtrados = ui.filtro_periodo(
            clientes, "data_ajuizamento", "Ajuizamento entre", "cl_periodo"
        )

    filtrados = ui.aplicar_multiselecao(filtrados, "responsavel", responsaveis)
    filtrados = ui.aplicar_multiselecao(filtrados, "servico", servicos)
    filtrados = ui.aplicar_multiselecao(filtrados, "status", status)
    filtrados = ui.aplicar_multiselecao(filtrados, "resultado_sentenca", resultados)
    filtrados = ui.busca_texto(filtrados, COLUNAS_BUSCA, busca)

    ui.resumo_filtro(len(clientes), len(filtrados))

    colunas = st.columns(5)
    ui.cartao(colunas[0], "Clientes na carteira", len(filtrados))
    ui.cartao(
        colunas[1], "Protocolados",
        int((filtrados["status"].str.contains("PROTOCOLAD", na=False)).sum()),
    )
    ui.cartao(
        colunas[2], "Checklist",
        int((filtrados["status"].str.contains("CHECKLIST", na=False)).sum()),
    )
    ui.cartao(colunas[3], "Ajuizados", int(filtrados["ajuizado"].sum()))
    ui.cartao(
        colunas[4], "Com cálculo",
        int(filtrados["possui_calculo"].sum()),
        "Possui RT confirmada pela contabilista ou valor ajuizado consolidado.",
    )

    colunas = st.columns(4)
    for indice, etapa in enumerate(["MINUTA", "ESTRATÉGIA", "REVISÃO", "CÁLCULO"]):
        ui.cartao(
            colunas[indice], etapa.capitalize(),
            int((filtrados["status"].str.upper() == etapa).sum()),
        )

    colunas = st.columns(2)
    ui.cartao(
        colunas[0], "Sem responsável",
        int((filtrados["responsavel"] == "SEM RESPONSÁVEL").sum()),
        "Pendência de atribuição.",
    )
    dias = filtrados["dias_contrato_ajuizamento"].dropna()
    dias = dias[dias >= 0]
    ui.cartao(
        colunas[1], "Média contrato até ajuizamento",
        f"{dias.mean():.1f} dias".replace(".", ",") if len(dias) else "—",
        f"Calculada sobre {len(dias)} caso(s) com as duas datas preenchidas.",
    )

    esquerda, direita = st.columns(2)
    with esquerda:
        st.markdown("#### Por status")
        resumo = filtrados["status"].value_counts().reset_index()
        resumo.columns = ["Status", "Quantidade"]
        if not resumo.empty:
            figura = px.bar(resumo, x="Quantidade", y="Status", orientation="h")
            figura.update_layout(height=340, yaxis={"categoryorder": "total ascending"})
            st.plotly_chart(figura, width="stretch")

    with direita:
        st.markdown("#### Ajuizamentos por mês")
        base = filtrados.dropna(subset=["data_ajuizamento"]).copy()
        if base.empty:
            st.info("Nenhum ajuizamento com data preenchida no filtro.")
        else:
            base["competencia"] = base["data_ajuizamento"].dt.to_period("M").astype(str)
            serie = base.groupby("competencia").size().reset_index(name="Quantidade")
            figura = px.line(serie, x="competencia", y="Quantidade", markers=True)
            figura.update_layout(height=340, xaxis_title="Competência")
            st.plotly_chart(figura, width="stretch")

    st.markdown("#### Clientes por responsável")
    por_responsavel = (
        filtrados.groupby("responsavel").size().reset_index(name="Quantidade")
        .sort_values("Quantidade", ascending=False)
        .rename(columns={"responsavel": "Responsável"})
    )
    st.dataframe(por_responsavel, width="stretch", hide_index=True)

    st.markdown("#### Detalhamento")
    visao = ui.formatar_datas(
        filtrados, ["data_contrato", "data_ajuizamento", "data_sentenca"]
    )
    colunas_tabela = {
        "cliente": "Cliente",
        "servico": "Serviço",
        "status": "Status",
        "responsavel": "Responsável",
        "data_contrato": "Contrato",
        "data_ajuizamento": "Ajuizamento",
        "tribunal": "Tribunal",
        "resultado_sentenca": "Sentença",
        "data_sentenca": "Data da sentença",
        "linha_origem": "Linha",
    }
    if regras["ver_financeiro"]:
        visao = ui.formatar_moedas(visao, ["honorario_total", "valor_ajuizado"])
        colunas_tabela["honorario_total"] = "Honorários previstos"
        colunas_tabela["valor_ajuizado"] = "Valor ajuizado"

    ui.tabela(visao, colunas_tabela, "Nenhum cliente corresponde aos filtros.")

    st.download_button(
        "Exportar CSV do recorte",
        filtrados.to_csv(index=False, sep=";").encode("utf-8-sig"),
        file_name="clientes.csv",
        mime="text/csv",
    )
