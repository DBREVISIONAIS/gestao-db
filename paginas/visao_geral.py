"""Visao geral: os numeros que a equipe precisa ver ao abrir o painel."""

from __future__ import annotations

import pandas as pd
import plotly.express as px
import streamlit as st

from db import auth
from paginas import componentes as ui


def render(prazos: pd.DataFrame, clientes: pd.DataFrame) -> None:
    st.subheader("Visão geral")

    prazos = auth.aplicar_recorte(prazos)
    clientes = auth.aplicar_recorte(clientes)
    regras = auth.regras_atuais()

    abertos = prazos[~prazos["encerrado"]] if not prazos.empty else prazos

    colunas = st.columns(5)
    ui.cartao(colunas[0], "Prazos em aberto", len(abertos))
    ui.cartao(
        colunas[1],
        "Vencidos",
        int((abertos["situacao"] == "VENCIDO").sum()) if not abertos.empty else 0,
        "Prazo fatal ultrapassado e status ainda não encerrado.",
    )
    ui.cartao(
        colunas[2],
        "Vencem hoje",
        int((abertos["situacao"] == "VENCE HOJE").sum()) if not abertos.empty else 0,
    )
    ui.cartao(
        colunas[3],
        "Próximos 7 dias",
        int((abertos["situacao"] == "PRÓXIMOS 7 DIAS").sum())
        if not abertos.empty
        else 0,
    )
    ui.cartao(
        colunas[4],
        "Clientes ativos",
        int((~clientes["encerrado"]).sum()) if not clientes.empty else 0,
    )

    if not abertos.empty:
        criticos = abertos[
            abertos["situacao"].isin(["VENCIDO", "VENCE HOJE", "PRÓXIMOS 7 DIAS"])
        ].sort_values("data_controle", na_position="last")

        st.markdown("#### Prazos críticos")
        ui.tabela(
            ui.formatar_datas(
                criticos.head(50), ["data_controle", "prazo_fatal", "data_evento"]
            ),
            {
                "data_controle": "Data de controle",
                "fonte_data": "Fonte",
                "autor": "Autor",
                "conteudo": "Conteúdo",
                "responsavel": "Responsável",
                "delegado": "Delegado",
                "status": "Status",
                "situacao": "Situação",
                "linha_origem": "Linha",
            },
            "Não há prazos críticos no recorte atual.",
        )

    esquerda, direita = st.columns(2)

    if not abertos.empty:
        with esquerda:
            st.markdown("#### Situação dos prazos em aberto")
            resumo = abertos["situacao"].value_counts().reset_index()
            resumo.columns = ["Situação", "Quantidade"]
            figura = px.bar(
                resumo,
                x="Situação",
                y="Quantidade",
                color="Situação",
                color_discrete_map=ui.CORES_SITUACAO,
            )
            figura.update_layout(showlegend=False, height=320)
            st.plotly_chart(figura, width="stretch")

    if not clientes.empty and regras["ver_financeiro"]:
        with direita:
            st.markdown("#### Honorários previstos por mês de ajuizamento")
            base = clientes.dropna(subset=["data_ajuizamento"]).copy()
            if base.empty:
                st.info("Nenhum ajuizamento com data preenchida.")
            else:
                base["competencia"] = base["data_ajuizamento"].dt.to_period("M").astype(
                    str
                )
                serie = (
                    base.groupby("competencia")["honorario_total"].sum().reset_index()
                )
                figura = px.bar(serie, x="competencia", y="honorario_total")
                figura.update_layout(
                    height=320,
                    xaxis_title="Competência",
                    yaxis_title="Honorários previstos",
                )
                st.plotly_chart(figura, width="stretch")
