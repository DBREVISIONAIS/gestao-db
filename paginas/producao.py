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

    st.caption(
        "Carga é a quantidade de prazos em aberto atribuídos a cada responsável "
        "técnico, ou seja, registros do CONTROLE DE PRAZOS cujo status não é "
        "Protocolado nem Concluído. A distribuição por cor usa a data de "
        "controle, que é o Prazo Fatal e, na falta dele, a Data Final. "
        "Já os eventos vêm do log e medem o que cada pessoa executou no período."
    )

    if not prazos.empty:
        abertos = prazos[~prazos["encerrado"]]

        colunas = st.columns(4)
        ui.cartao(colunas[0], "Prazos em aberto", len(abertos))
        ui.cartao(
            colunas[1], "Responsáveis com carga",
            int(abertos[abertos["responsavel"] != "SEM RESPONSÁVEL"]["responsavel"].nunique()),
        )
        vencidos = abertos[abertos["situacao"] == "VENCIDO"]
        ui.cartao(
            colunas[2], "Vencidos sob responsabilidade",
            int((vencidos["responsavel"] != "SEM RESPONSÁVEL").sum()),
            "Prazo de controle já ultrapassado e status ainda em aberto.",
        )
        ui.cartao(
            colunas[3], "Em aberto sem responsável",
            int((abertos["responsavel"] == "SEM RESPONSÁVEL").sum()),
            "Não entram na carga de ninguém. São pendência de atribuição.",
        )

        st.markdown("#### Carga atual por responsável")
        st.caption("Prazos em aberto, por situação da data de controle.")
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
        st.info(
            "Sem histórico de alterações. Os indicadores de eventos por "
            "editor aparecem depois que o log for migrado para a planilha "
            "auxiliar."
        )
        return

    st.markdown("#### Eventos executados no período")
    st.caption(
        "Contagem de eventos do log por quem executou. Lançamento de prazo, "
        "protocolo, envio para revisão, revisão concluída, conferência no "
        "Bitrix e cadastro de cliente. Alteração de outros campos não entra."
    )
    periodo = ui.filtro_periodo(logs, "data_hora", "Período", "prod_periodo")
    eventos = periodo[periodo["tipo_evento"].isin(EVENTOS_RELEVANTES)]

    if eventos.empty:
        st.info("Nenhum evento relevante no período selecionado.")
        return

    identificados = eventos[eventos["editor"] != "NÃO IDENTIFICADO"]
    nao_identificados = len(eventos) - len(identificados)
    if nao_identificados:
        st.caption(
            f"{nao_identificados} evento(s) sem e-mail registrado pelo Google "
            "ficaram fora do gráfico por não permitirem identificar o autor."
        )
    if identificados.empty:
        st.info("Nenhum evento com editor identificado no período.")
        return

    contagem = (
        identificados.groupby(["editor", "tipo_evento"]).size().reset_index(name="Qtd")
    )

    figura = px.bar(
        contagem,
        x="Qtd",
        y="editor",
        color="tipo_evento",
        orientation="h",
    )
    figura.update_layout(
        height=420,
        yaxis={"categoryorder": "total ascending", "title": "Editor"},
        legend_title="Evento",
    )
    st.plotly_chart(figura, width="stretch")

    st.markdown("#### Resumo por editor e tipo de evento")
    tabela_resumo = (
        identificados.pivot_table(
            index="editor",
            columns="tipo_evento",
            values="id",
            aggfunc="count",
            fill_value=0,
        )
        .reset_index()
        .rename(columns={"editor": "Editor"})
    )
    st.dataframe(tabela_resumo, width="stretch", hide_index=True)
