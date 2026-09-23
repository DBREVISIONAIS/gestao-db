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
    agregados = {"quantidade": ("cliente", "count"), "ajuizados": ("ajuizado", "sum")}
    colunas_resp = {
        "responsavel": "Responsável",
        "quantidade": "Clientes",
        "ajuizados": "Ajuizados",
    }
    moedas_resp = []
    if regras["ver_financeiro"]:
        agregados["honorario_total"] = ("honorario_total", "sum")
        colunas_resp["honorario_total"] = "Honorários previstos (R$)"
        moedas_resp = ["honorario_total"]
    por_responsavel = (
        filtrados.groupby("responsavel").agg(**agregados).reset_index()
        .sort_values("quantidade", ascending=False)
    )
    ui.tabela_compacta(
        por_responsavel, colunas_resp,
        moedas=moedas_resp, inteiros=["quantidade", "ajuizados"],
    )

    diagnosticos(filtrados, regras["ver_financeiro"])

    st.markdown("#### Detalhamento")
    visao = ui.formatar_datas(
        filtrados, ["data_contrato", "data_ajuizamento", "data_sentenca"]
    )
    colunas_tabela = {
        "link_bitrix": "Bitrix",
        "cliente": "Cliente",
        "servico": "Serviço",
        "status": "Status",
        "responsavel": "Responsável",
        "data_contrato": "Contrato",
        "data_ajuizamento": "Ajuizamento",
        "diagnostico": "Diagnóstico",
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
    if regras["ver_financeiro"] and not filtrados.empty:
        # Total fora da grade: linha de total dentro do st.dataframe
        # entraria na ordenação quando alguém clica no cabeçalho.
        st.markdown(
            f'<div class="linha-total">TOTAL DO RECORTE · {len(filtrados)} cliente(s)'
            f' · Honorários previstos {ui.moeda_cheia(filtrados["honorario_total"].sum(), True)}'
            f' · Valor ajuizado {ui.moeda_cheia(filtrados["valor_ajuizado"].sum(), True)}</div>',
            unsafe_allow_html=True,
        )

    st.download_button(
        "Exportar CSV do recorte",
        filtrados.to_csv(index=False, sep=";").encode("utf-8-sig"),
        file_name="clientes.csv",
        mime="text/csv",
    )


# ----------------------------------------------------------- diagnósticos

SEPARADORES_DIAGNOSTICO = r"\s*(?:/|;|,|\+|\sE\s)\s*"


def _padronizar_diagnostico(valor) -> str:
    texto = " ".join(str(valor or "").upper().split())
    return texto.strip(" .;,-")


def diagnosticos(filtrados: pd.DataFrame, ver_financeiro: bool) -> None:
    """
    Diagnósticos mais recorrentes e de maior valor.

    Respeita os filtros da tela (serviço, responsável, período). Valor
    da causa é o VALOR AJUIZADO CONSOLIDADO; honorários são o previsto
    total (contratual + sucumbencial). Grafias diferentes do mesmo
    diagnóstico contam separado: a padronização aqui só tira espaço
    duplicado, pontuação de borda e diferença de maiúscula.
    """
    st.markdown("#### Diagnósticos")

    if "diagnostico" not in filtrados.columns:
        st.info("A coluna DIAGNÓSTICO não foi encontrada no controle de clientes.")
        return

    base = filtrados.copy()
    base["diag"] = base["diagnostico"].map(_padronizar_diagnostico)
    sem = int((base["diag"] == "").sum())
    base = base[base["diag"] != ""]
    if base.empty:
        st.info("Nenhum cliente com diagnóstico preenchido no filtro.")
        return

    controles = st.columns([1.3, 1, 1])
    with controles[0]:
        separar = st.toggle(
            "Separar diagnósticos múltiplos da mesma célula",
            value=False,
            key="cl_diag_separar",
            help="Com a opção ligada, um cliente com duas doenças conta nas duas. "
            "Os totais passam a somar o mesmo cliente mais de uma vez.",
        )
    with controles[1]:
        criterios = {"Quantidade": "clientes"}
        if ver_financeiro:
            criterios.update({"Honorários": "honorarios", "Valor da causa": "causa"})
        criterio = st.selectbox(
            "Ordenar por", list(criterios), key="cl_diag_ordem"
        )
    with controles[2]:
        limite = st.number_input(
            "Mostrar os primeiros", min_value=5, max_value=100, value=20, step=5,
            key="cl_diag_limite",
        )

    if separar:
        base["diag"] = base["diag"].str.split(SEPARADORES_DIAGNOSTICO, regex=True)
        base = base.explode("diag")
        base["diag"] = base["diag"].map(_padronizar_diagnostico)
        base = base[base["diag"] != ""]

    base["procedente"] = base["resultado_sentenca"].isin(
        ["PROCEDENTE", "PARCIALMENTE PROCEDENTE"]
    )
    base["com_sentenca"] = base["resultado_sentenca"] != "SEM SENTENÇA"

    resumo = (
        base.groupby("diag")
        .agg(
            clientes=("cliente", "count"),
            ajuizados=("ajuizado", "sum"),
            causa=("valor_ajuizado", "sum"),
            honorarios=("honorario_total", "sum"),
            com_sentenca=("com_sentenca", "sum"),
            procedentes=("procedente", "sum"),
        )
        .reset_index()
    )
    resumo["media_honorarios"] = resumo["honorarios"] / resumo["clientes"]
    resumo["taxa"] = (
        resumo["procedentes"] / resumo["com_sentenca"].replace(0, pd.NA) * 100
    )
    resumo = resumo.sort_values(criterios[criterio], ascending=False)

    # O que passa do limite vira uma linha DEMAIS, para o total continuar
    # sendo o do recorte inteiro, e não só o das linhas visíveis.
    topo = resumo.head(int(limite))
    resto = resumo.iloc[int(limite):]
    if not resto.empty:
        demais = {
            "diag": f"DEMAIS ({len(resto)} diagnósticos)",
            **{c: resto[c].sum() for c in
               ["clientes", "ajuizados", "causa", "honorarios", "com_sentenca",
                "procedentes"]},
        }
        demais["media_honorarios"] = demais["honorarios"] / demais["clientes"]
        demais["taxa"] = (
            demais["procedentes"] / demais["com_sentenca"] * 100
            if demais["com_sentenca"] else pd.NA
        )
        topo = pd.concat([topo, pd.DataFrame([demais])], ignore_index=True)

    st.caption(
        f"{resumo['diag'].nunique()} diagnóstico(s) distinto(s) · "
        f"{sem} cliente(s) do filtro sem diagnóstico preenchido."
        + (" Com a separação ligada, os totais contam o mesmo cliente mais de uma vez."
           if separar else "")
    )

    graficos = [("clientes", "Mais recorrentes (clientes)")]
    if ver_financeiro:
        graficos.append(("honorarios", "Maiores honorários previstos (R$)"))
    colunas = st.columns(len(graficos))
    for coluna, (campo, titulo) in zip(colunas, graficos):
        with coluna:
            dados = resumo.sort_values(campo, ascending=False).head(12)
            figura = px.bar(
                dados, x=campo, y="diag", orientation="h", title=titulo,
                color_discrete_sequence=["#1A3762" if campo == "clientes" else "#4DA2DA"],
            )
            figura.update_layout(
                height=420, yaxis={"categoryorder": "total ascending", "title": ""},
                xaxis_title="", margin=dict(t=40, l=10, r=10, b=10),
                separators=",.", title_font_size=14,
            )
            st.plotly_chart(figura, width="stretch", key=f"diag_{campo}")

    colunas_tabela = {
        "diag": "Diagnóstico",
        "clientes": "Clientes",
        "ajuizados": "Ajuizados",
        "com_sentenca": "Com sentença",
        "procedentes": "Procedentes",
        "taxa": "% êxito",
    }
    moedas = []
    if ver_financeiro:
        colunas_tabela.update({
            "causa": "Valor da causa (R$)",
            "honorarios": "Honorários previstos (R$)",
            "media_honorarios": "Honorário médio (R$)",
        })
        moedas = ["causa", "honorarios", "media_honorarios"]

    ui.tabela_compacta(
        topo,
        colunas_tabela,
        moedas=moedas,
        inteiros=["clientes", "ajuizados", "com_sentenca", "procedentes"],
        percentuais=["taxa"],
        somar=["clientes", "ajuizados", "com_sentenca", "procedentes", "causa",
               "honorarios"],
        medias={
            "taxa": ("procedentes", "com_sentenca", 100),
            "media_honorarios": ("honorarios", "clientes"),
        },
    )
    st.caption(
        "% êxito: procedentes e parcialmente procedentes sobre o total com "
        "sentença registrada no cadastro."
    )
