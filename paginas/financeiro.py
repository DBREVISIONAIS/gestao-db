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

    ajuizados = filtrados[filtrados["ajuizado"]]
    total_previsto = filtrados["honorario_total"].sum()
    media = total_previsto / len(ajuizados) if len(ajuizados) else 0

    colunas = st.columns(4)
    ui.cartao(colunas[0], "Ajuizamentos no período", len(ajuizados))
    ui.cartao(colunas[1], "Honorários no período", formatar_moeda(total_previsto))
    ui.cartao(
        colunas[2], "Média por ajuizamento", formatar_moeda(media),
        "Total previsto dividido pela quantidade de ajuizamentos do recorte.",
    )
    ui.cartao(
        colunas[3], "Valor ajuizado consolidado",
        formatar_moeda(filtrados["valor_ajuizado"].sum()),
    )

    colunas = st.columns(3)
    ui.cartao(
        colunas[0], "Honorários contratuais",
        formatar_moeda(filtrados["honorario_previsto"].sum()),
    )
    ui.cartao(
        colunas[1], "Honorários sucumbenciais",
        formatar_moeda(filtrados["honorario_sucumbencial"].sum()),
    )
    dias = filtrados["dias_contrato_ajuizamento"].dropna()
    dias = dias[dias >= 0]
    ui.cartao(
        colunas[2], "Média contrato até ajuizamento",
        f"{dias.mean():.1f} dias".replace(".", ",") if len(dias) else "—",
        f"Calculada sobre {len(dias)} caso(s) com as duas datas preenchidas.",
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

        st.markdown("#### Honorários por mês de ajuizamento")
        mensal = (
            base.groupby("competencia")
            .agg(
                ajuizamentos=("cliente", "count"),
                contratual=("honorario_previsto", "sum"),
                total=("honorario_total", "sum"),
            )
            .reset_index()
            .sort_values("competencia")
        )
        mensal["media"] = mensal["total"] / mensal["ajuizamentos"].replace(0, pd.NA)
        mensal["acumulado"] = mensal["total"].cumsum()
        ui.tabela(
            ui.formatar_moedas(mensal, ["contratual", "total", "media", "acumulado"]),
            {
                "competencia": "Mês",
                "ajuizamentos": "Ajuizamentos",
                "contratual": "Contratual",
                "total": "Total previsto",
                "media": "Média",
                "acumulado": "Acumulado",
            },
            "Sem ajuizamentos no filtro.",
        )

        st.markdown("#### Honorários por mês e por serviço")
        matriz = (
            base.pivot_table(
                index="competencia",
                columns="servico",
                values="honorario_total",
                aggfunc="sum",
                fill_value=0,
            )
            .reset_index()
            .rename(columns={"competencia": "Mês"})
        )
        st.dataframe(matriz, width="stretch", hide_index=True)

        st.markdown("#### Ajuizamentos por mês e por responsável")
        matriz = (
            base.pivot_table(
                index="competencia",
                columns="responsavel",
                values="cliente",
                aggfunc="count",
                fill_value=0,
            )
            .reset_index()
            .rename(columns={"competencia": "Mês"})
        )
        matriz["Total"] = matriz.drop(columns=["Mês"]).sum(axis=1)
        st.dataframe(matriz, width="stretch", hide_index=True)

    st.markdown("#### Por responsável")
    por_responsavel = (
        filtrados.groupby("responsavel")
        .agg(
            clientes=("cliente", "count"),
            ajuizamentos=("ajuizado", "sum"),
            contratual=("honorario_previsto", "sum"),
            honorario_total=("honorario_total", "sum"),
            valor_ajuizado=("valor_ajuizado", "sum"),
        )
        .reset_index()
        .sort_values("honorario_total", ascending=False)
    )
    por_responsavel["media"] = por_responsavel["honorario_total"] / por_responsavel[
        "ajuizamentos"
    ].replace(0, pd.NA)
    ui.tabela(
        ui.formatar_moedas(
            por_responsavel, ["contratual", "honorario_total", "media", "valor_ajuizado"]
        ),
        {
            "responsavel": "Responsável",
            "clientes": "Clientes",
            "ajuizamentos": "Ajuizamentos",
            "contratual": "Contratual",
            "honorario_total": "Total previsto",
            "media": "Média",
            "valor_ajuizado": "Valor ajuizado",
        },
        "Sem dados no filtro.",
    )

    st.markdown("#### Por status da carteira")
    por_status = (
        filtrados.groupby("status")
        .agg(
            clientes=("cliente", "count"),
            honorario_total=("honorario_total", "sum"),
        )
        .reset_index()
        .sort_values("clientes", ascending=False)
    )
    ui.tabela(
        ui.formatar_moedas(por_status, ["honorario_total"]),
        {
            "status": "Status",
            "clientes": "Quantidade",
            "honorario_total": "Honorários previstos",
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
