"""
Dash de logs.

Substitui as abas DASH LOGS e DASH LOG CLIENTES do Google Sheets.
Mede tempo entre etapas, transicoes de status, ranking de edicoes e
distribuicao das alteracoes por horario e dia da semana.

Uma ressalva que vale ler antes de usar os numeros: tudo aqui depende
do historico registrado. Cliente sem transicao vinculada nao entra nas
medias de ciclo completo, e isso e proposital. Falta de marco nao e
atraso, e tratar as duas coisas junto produziria numero errado.
"""

from __future__ import annotations

import pandas as pd
import plotly.express as px
import streamlit as st

from db import auth, modelo
from paginas import componentes as ui

ORDEM_HORAS = [f"{h:02d}h" for h in range(24)]


def render(logs: pd.DataFrame, clientes: pd.DataFrame) -> None:
    st.subheader("Logs e tempo de ciclo")

    if logs.empty:
        st.info(
            "Sem histórico disponível. Instale o PATCH_LOGS_EXTERNOS.gs na "
            "planilha principal e execute a migração."
        )
        return

    painel(logs, auth.aplicar_recorte(clientes))


@st.fragment
def painel(logs: pd.DataFrame, clientes: pd.DataFrame) -> None:
    with st.expander("Filtros", expanded=False):
        colunas = st.columns(3)
        with colunas[0]:
            abas = ui.multiselecao("Aba", ui.opcoes(logs, "aba_origem"), "log_aba")
        with colunas[1]:
            editores = ui.multiselecao("Editor", ui.opcoes(logs, "editor"), "log_editor")
        with colunas[2]:
            campos = ui.multiselecao("Campo", ui.opcoes(logs, "cabecalho"), "log_campo")
        filtrados = ui.filtro_periodo(logs, "data_hora", "Período", "log_periodo")

    filtrados = ui.aplicar_multiselecao(filtrados, "aba_origem", abas)
    filtrados = ui.aplicar_multiselecao(filtrados, "editor", editores)
    filtrados = ui.aplicar_multiselecao(filtrados, "cabecalho", campos)

    transicoes = modelo.transicoes_status(filtrados)
    permanencia = modelo.permanencia_por_etapa(transicoes)
    ciclos = modelo.ciclos_do_cliente(clientes, transicoes)

    _indicadores(filtrados, transicoes, ciclos)
    _tempo_de_ciclo(ciclos)
    _etapas(permanencia)
    _quem_edita(filtrados, transicoes)
    _distribuicao(filtrados)
    _transicoes_recentes(transicoes)


def _indicadores(logs, transicoes, ciclos) -> None:
    colunas = st.columns(4)
    ui.cartao(colunas[0], "Edições registradas", len(logs))
    ui.cartao(
        colunas[1], "Transições de status", len(transicoes),
        "Somente mudanças efetivas de status.",
    )
    ui.cartao(colunas[2], "Editores identificados", logs["editor"].nunique())

    sem_historico = 0
    if not ciclos.empty and not transicoes.empty:
        com_historico = set(transicoes["chave_registro"])
        sem_historico = sum(
            1
            for nome in ciclos["cliente"]
            if str(nome).strip().upper() not in {str(c).upper() for c in com_historico}
        )
    ui.cartao(
        colunas[3], "Sem histórico de status", sem_historico,
        "Clientes sem nenhuma transição vinculada. Não entram nas médias.",
    )


def _tempo_de_ciclo(ciclos: pd.DataFrame) -> None:
    st.markdown("#### Tempo entre contrato, minuta e protocolo")

    if ciclos.empty:
        st.info("Sem clientes no recorte.")
        return

    def media(coluna: str, uteis: str | None = None) -> str:
        serie = ciclos[coluna].dropna()
        serie = serie[serie >= 0]
        if serie.empty:
            return "—"
        texto = f"{serie.mean():.1f} corridos".replace(".", ",")
        if uteis and uteis in ciclos.columns:
            serie_uteis = ciclos[uteis].dropna()
            if not serie_uteis.empty:
                texto += f" | {serie_uteis.mean():.1f} úteis".replace(".", ",")
        return texto

    colunas = st.columns(3)
    ui.cartao(
        colunas[0], "Contrato até minuta",
        media("contrato_minuta", "contrato_minuta_uteis"),
        f"{ciclos['contrato_minuta'].notna().sum()} caso(s) com as duas datas.",
    )
    ui.cartao(
        colunas[1], "Contrato até protocolo",
        media("contrato_protocolo", "contrato_protocolo_uteis"),
        f"{ciclos['contrato_protocolo'].notna().sum()} caso(s) com as duas datas.",
    )
    ui.cartao(
        colunas[2], "Minuta pronta até protocolo",
        media("pronta_protocolo"),
        "Da revisão ou do status Protocolar até o protocolo.",
    )

    detalhe = ciclos.dropna(subset=["contrato_protocolo"]).sort_values(
        "contrato_protocolo", ascending=False
    )
    with st.expander("Casos com maior tempo entre contrato e protocolo"):
        ui.tabela(
            ui.formatar_datas(detalhe.head(50), ["contrato", "minuta", "protocolo"]),
            {
                "cliente": "Cliente",
                "servico": "Serviço",
                "responsavel": "Responsável",
                "status": "Status",
                "contrato": "Contrato",
                "minuta": "Minuta",
                "protocolo": "Protocolo",
                "contrato_minuta": "Contrato→minuta",
                "contrato_protocolo": "Contrato→protocolo",
            },
            "Sem casos com as duas datas.",
        )


def _etapas(permanencia: pd.DataFrame) -> None:
    st.markdown("#### Tempo médio em cada etapa")

    if permanencia.empty:
        st.info("Sem transições suficientes para medir permanência.")
        return

    completos = permanencia[permanencia["concluido"]]
    resumo = (
        completos.groupby("etapa")
        .agg(
            ciclos=("etapa", "count"),
            media_corridos=("dias_corridos", "mean"),
            media_uteis=("dias_uteis", "mean"),
        )
        .reset_index()
        .sort_values("ciclos", ascending=False)
    )
    resumo["media_corridos"] = resumo["media_corridos"].round(1)
    resumo["media_uteis"] = resumo["media_uteis"].round(1)

    atuais = (
        permanencia[~permanencia["concluido"]]
        .groupby("etapa")
        .size()
        .reset_index(name="atuais")
    )
    resumo = resumo.merge(atuais, on="etapa", how="outer").fillna(0)

    esquerda, direita = st.columns([3, 2])
    with esquerda:
        ui.tabela(
            resumo,
            {
                "etapa": "Etapa",
                "ciclos": "Ciclos completos",
                "media_corridos": "Média corridos",
                "media_uteis": "Média úteis",
                "atuais": "Atualmente na etapa",
            },
            "Sem etapas no recorte.",
        )
    with direita:
        grafico = resumo[resumo["media_corridos"] > 0]
        if not grafico.empty:
            figura = px.bar(
                grafico, x="media_corridos", y="etapa", orientation="h"
            )
            figura.update_layout(
                height=380,
                yaxis={"categoryorder": "total ascending", "title": "Etapa"},
                xaxis_title="Dias corridos",
            )
            st.plotly_chart(figura, width="stretch")


def _quem_edita(logs: pd.DataFrame, transicoes: pd.DataFrame) -> None:
    esquerda, direita = st.columns(2)

    with esquerda:
        st.markdown("#### Ranking de edições")
        ranking = (
            logs.groupby("editor").size().reset_index(name="Total de edições")
            .sort_values("Total de edições", ascending=False)
            .rename(columns={"editor": "Editor"})
        )
        st.dataframe(ranking, width="stretch", hide_index=True, height=320)

    with direita:
        st.markdown("#### Campos mais alterados")
        campos = (
            logs.groupby("cabecalho").size().reset_index(name="Quantidade")
            .sort_values("Quantidade", ascending=False)
            .rename(columns={"cabecalho": "Campo"})
        )
        st.dataframe(campos, width="stretch", hide_index=True, height=320)

    if transicoes.empty:
        return

    st.markdown("#### Quem mudou o quê")
    matriz = (
        transicoes.groupby(["editor", "de", "para"]).size().reset_index(name="Quantidade")
        .sort_values("Quantidade", ascending=False)
    )
    ui.tabela(
        matriz.head(40),
        {"editor": "Editor", "de": "Etapa anterior", "para": "Nova etapa",
         "Quantidade": "Quantidade"},
        "Sem transições no recorte.",
    )


def _distribuicao(logs: pd.DataFrame) -> None:
    esquerda, direita = st.columns(2)

    with esquerda:
        st.markdown("#### Alterações por horário")
        serie = logs.groupby("hora").size().reset_index(name="Quantidade")
        serie = serie[serie["hora"].isin(ORDEM_HORAS)].sort_values("hora")
        if serie.empty:
            st.info("Sem horários registrados.")
        else:
            figura = px.bar(serie, x="hora", y="Quantidade")
            figura.update_layout(height=320, xaxis_title="Horário")
            st.plotly_chart(figura, width="stretch")

    with direita:
        st.markdown("#### Alterações por dia da semana")
        base = logs.dropna(subset=["dia_semana"]).copy()
        if base.empty:
            st.info("Sem datas registradas.")
        else:
            base["dia"] = base["dia_semana"].astype(int).map(
                lambda i: modelo.DIAS_SEMANA[i]
            )
            serie = (
                base.groupby("dia").size().reset_index(name="Quantidade")
                .sort_values("Quantidade", ascending=False)
            )
            figura = px.bar(serie, x="dia", y="Quantidade")
            figura.update_layout(height=320, xaxis_title="")
            st.plotly_chart(figura, width="stretch")


def _transicoes_recentes(transicoes: pd.DataFrame) -> None:
    st.markdown("#### Transições de status recentes")

    if transicoes.empty:
        st.info("Sem transições no recorte.")
        return

    recentes = transicoes.sort_values("data_hora", ascending=False).head(200).copy()
    recentes["data_hora"] = recentes["data_hora"].dt.strftime("%d/%m/%Y %H:%M")
    ui.tabela(
        recentes,
        {
            "data_hora": "Data e hora",
            "cliente_autor": "Cliente",
            "aba_origem": "Aba",
            "de": "Etapa anterior",
            "para": "Nova etapa",
            "editor": "Editor",
            "linha": "Linha",
        },
        "Sem transições no recorte.",
    )

    st.download_button(
        "Exportar CSV das transições",
        transicoes.to_csv(index=False, sep=";").encode("utf-8-sig"),
        file_name="transicoes_status.csv",
        mime="text/csv",
    )
