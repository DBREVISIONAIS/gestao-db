"""
Resultados processuais.

Classifica sentencas, liminares, justica gratuita, agravos e embargos a
partir do conteudo e da observacao do CONTROLE DE PRAZOS, com a mesma
logica do Apps Script. A data de referencia aqui e a Data do Evento, e
nao a data de controle, porque o que se mede e quando o resultado saiu.
"""

from __future__ import annotations

import pandas as pd
import plotly.express as px
import streamlit as st

from db import auth, modelo
from paginas import componentes as ui


def render(resultados: pd.DataFrame) -> None:
    st.subheader("Resultados processuais")

    resultados = auth.aplicar_recorte(resultados)
    if resultados.empty:
        st.info(
            "Nenhum resultado identificado no conteúdo ou na observação "
            "dos prazos."
        )
        return

    painel(resultados)


@st.fragment
def painel(resultados: pd.DataFrame) -> None:
    with st.expander("Filtros", expanded=False):
        colunas = st.columns(3)
        with colunas[0]:
            categorias = ui.multiselecao(
                "Categoria", ui.opcoes(resultados, "categoria"), "res_cat"
            )
        with colunas[1]:
            responsaveis = ui.multiselecao(
                "Responsável", ui.opcoes(resultados, "responsavel"), "res_resp"
            )
        with colunas[2]:
            motivos = ui.multiselecao(
                "Motivo registrado", ui.opcoes(resultados, "motivo"), "res_motivo"
            )
        filtrados = ui.filtro_periodo(
            resultados, "data_evento", "Data do evento entre", "res_periodo"
        )

    filtrados = ui.aplicar_multiselecao(filtrados, "categoria", categorias)
    filtrados = ui.aplicar_multiselecao(filtrados, "responsavel", responsaveis)
    filtrados = ui.aplicar_multiselecao(filtrados, "motivo", motivos)

    sentencas = filtrados[filtrados["categoria"] == "SENTENÇA"]
    cartoes = st.columns(4)
    for indice, rotulo in enumerate(
        ["PROCEDENTE", "PARCIALMENTE PROCEDENTE", "IMPROCEDENTE", "EXTINÇÃO"]
    ):
        ui.cartao(
            cartoes[indice],
            f"Sentenças {rotulo.lower()}",
            int((sentencas["resultado"] == rotulo).sum()),
        )

    liminares = filtrados[filtrados["categoria"] == "LIMINAR"]
    ajg = filtrados[filtrados["categoria"] == "JUSTIÇA GRATUITA"]
    cartoes = st.columns(4)
    ui.cartao(cartoes[0], "Liminares deferidas", int((liminares["resultado"] == "DEFERIDA").sum()))
    ui.cartao(cartoes[1], "Liminares indeferidas", int((liminares["resultado"] == "INDEFERIDA").sum()))
    ui.cartao(cartoes[2], "AJG deferida", int((ajg["resultado"] == "DEFERIDA").sum()))
    ui.cartao(cartoes[3], "AJG indeferida", int((ajg["resultado"] == "INDEFERIDA").sum()))

    # Taxa de êxito: procedente e parcialmente procedente sobre o total
    # de sentenças classificadas. Extinção fica fora do denominador
    # porque não é julgamento de mérito.
    julgadas = sentencas[
        sentencas["resultado"].isin(
            ["PROCEDENTE", "PARCIALMENTE PROCEDENTE", "IMPROCEDENTE"]
        )
    ]
    favoraveis = julgadas[julgadas["resultado"] != "IMPROCEDENTE"]
    taxa = (len(favoraveis) / len(julgadas) * 100) if len(julgadas) else 0

    colunas = st.columns(4)
    ui.cartao(
        colunas[0], "Taxa de êxito",
        f"{taxa:.1f}%".replace(".", ",") if len(julgadas) else "—",
        "Procedentes e parciais sobre o total de sentenças de mérito. "
        "Extinções ficam fora do cálculo.",
    )
    ui.cartao(colunas[1], "Sentenças de mérito", len(julgadas))
    embargos = filtrados[filtrados["categoria"] == "EMBARGOS"]
    ui.cartao(
        colunas[2], "Embargos acolhidos",
        int(embargos["resultado"].str.contains("ACOLHIDOS", na=False).sum()
            - embargos["resultado"].str.contains("NÃO ACOLHIDOS", na=False).sum()),
    )
    agravos = filtrados[filtrados["categoria"] == "AGRAVO"]
    ui.cartao(colunas[3], "Agravos providos",
              int((agravos["resultado"] == "PROVIDO").sum()))

    ui.resumo_filtro(len(resultados), len(filtrados))

    with st.expander("Motivos registrados nos resultados"):
        st.caption(
            "Extraídos do texto do conteúdo e da observação: prescrição, "
            "decadência, laudo desfavorável, prova insuficiente, ilegitimidade "
            "e afins. Sem registro no controle não há motivo a apurar."
        )
        motivos_serie = (
            filtrados[filtrados["motivo"] != "NÃO REGISTRADO NO CONTROLE"]
            .groupby("motivo").size().reset_index(name="Quantidade")
            .sort_values("Quantidade", ascending=False)
            .rename(columns={"motivo": "Motivo"})
        )
        if motivos_serie.empty:
            st.info("Nenhum motivo registrado no recorte.")
        else:
            st.dataframe(motivos_serie, width="stretch", hide_index=True)

    esquerda, direita = st.columns(2)

    with esquerda:
        st.markdown("#### Sentenças por mês")
        base = sentencas[sentencas["competencia"] != ""]
        if base.empty:
            st.info("Sem sentenças com data de evento no filtro.")
        else:
            serie = (
                base.groupby(["competencia", "resultado"])
                .size()
                .reset_index(name="Quantidade")
            )
            figura = px.bar(
                serie, x="competencia", y="Quantidade", color="resultado", barmode="stack"
            )
            figura.update_layout(height=360, xaxis_title="Competência", legend_title="")
            st.plotly_chart(figura, width="stretch")

    with direita:
        st.markdown("#### Resultados por tipo")
        serie = (
            filtrados.groupby(["categoria", "resultado"])
            .size()
            .reset_index(name="Quantidade")
            .sort_values("Quantidade", ascending=False)
        )
        st.dataframe(
            serie.rename(
                columns={
                    "categoria": "Evento",
                    "resultado": "Resultado",
                }
            ),
            width="stretch",
            hide_index=True,
            height=360,
        )

    st.markdown("#### Resultados por responsável")
    matriz = (
        filtrados.pivot_table(
            index="responsavel",
            columns="categoria",
            values="linha_origem",
            aggfunc="count",
            fill_value=0,
        )
        .reset_index()
        .rename(columns={"responsavel": "Responsável"})
    )
    st.dataframe(matriz, width="stretch", hide_index=True)

    st.markdown("#### Detalhamento")
    visao = ui.formatar_datas(filtrados, ["data_evento"])
    ui.tabela(
        visao,
        {
            "data_evento": "Data do evento",
            "autor": "Autor",
            "responsavel": "Responsável",
            "categoria": "Evento",
            "resultado": "Resultado",
            "motivo": "Motivo registrado",
            "conteudo": "Conteúdo e observação",
            "status": "Status",
            "linha_origem": "Linha",
        },
        "Nenhum resultado no filtro.",
    )

    st.download_button(
        "Exportar CSV do recorte",
        filtrados.to_csv(index=False, sep=";").encode("utf-8-sig"),
        file_name="resultados_processuais.csv",
        mime="text/csv",
    )
