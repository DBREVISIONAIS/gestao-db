"""
Financeiro.

Todos os valores exibidos aqui sao previsoes lancadas no CONTROLE DE
CLIENTES, e nao valores efetivamente recebidos. O rotulo deixa isso
explicito para nao gerar leitura equivocada de faturamento.

O realizado usa a Data do Ajuizamento, que e o marco de protocolo
adotado pelo escritorio. Cliente com valor mas sem ajuizamento entra no
pipeline, nunca no realizado.
"""

from __future__ import annotations

from datetime import date

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
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

    metas(clientes)
    st.divider()
    painel(clientes)


# ------------------------------------------------------------- metas


@st.fragment
def metas(clientes: pd.DataFrame) -> None:
    st.markdown("### Meta")

    anos = sorted(
        {int(a) for a in clientes["ano_ajuizamento"].dropna().unique()}, reverse=True
    )
    if not anos:
        st.info("Nenhum ajuizamento com data preenchida.")
        return

    controles = st.columns([1, 1.4, 1.4])
    with controles[0]:
        ano = st.selectbox("Ano", anos, index=0, key="meta_ano")
    with controles[1]:
        meta_anual = st.number_input(
            "Meta anual (R$)",
            min_value=0.0,
            value=4_800_000.0,
            step=100_000.0,
            format="%.2f",
            key="meta_anual",
        )
    with controles[2]:
        meta_mensal = st.number_input(
            "Meta mensal de referência (R$)",
            min_value=0.0,
            value=float(meta_anual / 12) if meta_anual else 400_000.0,
            step=50_000.0,
            format="%.2f",
            key="meta_mensal",
            help="Serve só de linha de comparação. O que fecha o ano é a meta anual.",
        )

    ajuizados = clientes[
        clientes["ajuizado"] & (clientes["ano_ajuizamento"] == ano)
    ].copy()
    realizado = float(ajuizados["honorario_total"].sum())
    falta = max(meta_anual - realizado, 0.0)
    percentual = (realizado / meta_anual * 100) if meta_anual else 0.0

    # Pipeline: cliente com valor previsto que ainda não foi ajuizado.
    # É o que está parado fora do protocolo e pode virar realizado.
    pipeline = clientes[
        (~clientes["ajuizado"])
        & (clientes["honorario_total"] > 0)
        & (~clientes["encerrado"])
    ].copy()
    valor_pipeline = float(pipeline["honorario_total"].sum())

    hoje = date.today()
    meses_restantes = (12 - hoje.month + 1) if hoje.year == ano else 0

    colunas = st.columns(4)
    ui.cartao(colunas[0], "Realizado no ano", formatar_moeda(realizado),
              f"{len(ajuizados)} ajuizamento(s) em {ano}.")
    ui.cartao(colunas[1], "Falta para a meta", formatar_moeda(falta),
              f"{percentual:.1f}% da meta atingido.".replace(".", ","))
    ui.cartao(colunas[2], "Parado fora do protocolo", formatar_moeda(valor_pipeline),
              f"{len(pipeline)} cliente(s) com valor e sem ajuizamento.")
    ui.cartao(
        colunas[3],
        "Necessário por mês",
        formatar_moeda(falta / meses_restantes) if meses_restantes else "—",
        f"{meses_restantes} mês(es) restante(s) no ano." if meses_restantes
        else "Ano encerrado.",
    )

    if falta <= 0:
        st.success(
            f"Meta de {formatar_moeda(meta_anual)} atingida. "
            f"Excedente de {formatar_moeda(realizado - meta_anual)}."
        )
    elif valor_pipeline >= falta:
        st.info(
            f"O pipeline atual cobre a meta: há {formatar_moeda(valor_pipeline)} "
            f"parado fora do protocolo para uma diferença de {formatar_moeda(falta)}."
        )
    else:
        st.warning(
            f"O pipeline atual não cobre a meta. Faltam {formatar_moeda(falta)} e há "
            f"{formatar_moeda(valor_pipeline)} disponível para protocolo, "
            f"uma diferença de {formatar_moeda(falta - valor_pipeline)} que "
            "depende de contratos novos."
        )

    _evolucao(ajuizados, meta_anual, meta_mensal, ano)
    _simulador(pipeline, falta)


def _evolucao(ajuizados, meta_anual, meta_mensal, ano) -> None:
    st.markdown("#### Evolução acumulada no ano")

    if ajuizados.empty:
        st.info("Sem ajuizamentos no ano selecionado.")
        return

    mensal = (
        ajuizados.groupby("competencia_ajuizamento")["honorario_total"]
        .sum()
        .reset_index()
        .sort_values("competencia_ajuizamento")
    )
    mensal["acumulado"] = mensal["honorario_total"].cumsum()
    mensal["meta_acumulada"] = [
        meta_mensal * (i + 1) for i in range(len(mensal))
    ]

    figura = go.Figure()
    figura.add_bar(
        x=mensal["competencia_ajuizamento"],
        y=mensal["honorario_total"],
        name="No mês",
        marker_color="#4DA2DA",
    )
    figura.add_scatter(
        x=mensal["competencia_ajuizamento"],
        y=mensal["acumulado"],
        name="Acumulado",
        mode="lines+markers",
        line=dict(color="#1A3762", width=3),
    )
    figura.add_scatter(
        x=mensal["competencia_ajuizamento"],
        y=mensal["meta_acumulada"],
        name="Meta acumulada",
        mode="lines",
        line=dict(color="#C62828", width=2, dash="dash"),
    )
    figura.add_hline(
        y=meta_anual,
        line=dict(color="#F7BD2E", width=2, dash="dot"),
        annotation_text="Meta anual",
        annotation_position="top left",
    )
    figura.update_layout(
        height=400, xaxis_title="Competência", yaxis_title="R$",
        legend=dict(orientation="h", y=1.12),
    )
    st.plotly_chart(figura, width="stretch")

    with st.expander("Tabela da evolução"):
        ui.tabela(
            ui.formatar_moedas(
                mensal, ["honorario_total", "acumulado", "meta_acumulada"]
            ),
            {
                "competencia_ajuizamento": "Mês",
                "honorario_total": "No mês",
                "acumulado": "Acumulado",
                "meta_acumulada": "Meta acumulada",
            },
            "Sem dados.",
        )


def _simulador(pipeline: pd.DataFrame, falta: float) -> None:
    st.markdown("#### Simulador de protocolo")

    if pipeline.empty:
        st.info("Nenhum cliente com valor previsto aguardando protocolo.")
        return

    ordenado = pipeline.sort_values("honorario_total", ascending=False).copy()
    ordenado["rotulo"] = (
        ordenado["cliente"].astype(str)
        + " — "
        + ordenado["honorario_total"].apply(formatar_moeda)
        + " — "
        + ordenado["status"].astype(str)
    )

    # Sugestão gulosa: os maiores primeiro, até cobrir a diferença.
    # Não é a combinação ótima, é a que exige menos protocolos.
    sugeridos: list[str] = []
    if falta > 0:
        acumulado = 0.0
        for _, linha in ordenado.iterrows():
            if acumulado >= falta:
                break
            sugeridos.append(linha["rotulo"])
            acumulado += float(linha["honorario_total"])

    if sugeridos:
        st.caption(
            f"Sugestão automática: protocolando {len(sugeridos)} cliente(s) de maior "
            "valor, a diferença é coberta. Ajuste a seleção como quiser."
        )

    escolhidos = st.multiselect(
        "Clientes a protocolar",
        ordenado["rotulo"].tolist(),
        default=sugeridos,
        key="meta_simulacao",
    )

    selecionados = ordenado[ordenado["rotulo"].isin(escolhidos)]
    soma = float(selecionados["honorario_total"].sum())
    restante = falta - soma

    colunas = st.columns(3)
    ui.cartao(colunas[0], "Selecionados", len(selecionados))
    ui.cartao(colunas[1], "Valor da seleção", formatar_moeda(soma))
    ui.cartao(
        colunas[2],
        "Diferença após protocolo",
        formatar_moeda(max(restante, 0.0)),
        "Quanto ainda faltaria para a meta anual.",
    )

    if soma and restante <= 0:
        st.success(
            f"Protocolando esses {len(selecionados)} cliente(s), a meta é batida "
            f"com folga de {formatar_moeda(-restante)}."
        )
    elif soma:
        st.warning(
            f"Ainda faltariam {formatar_moeda(restante)} depois de protocolar "
            "os clientes selecionados."
        )

    ui.tabela(
        ui.formatar_moedas(
            selecionados if not selecionados.empty else ordenado.head(30),
            ["honorario_total"],
        ),
        {
            "cliente": "Cliente",
            "servico": "Serviço",
            "status": "Status",
            "responsavel": "Responsável",
            "honorario_total": "Honorários previstos",
            "linha_origem": "Linha",
        },
        "Sem clientes aguardando protocolo.",
    )


# --------------------------------------------------------- painel geral


@st.fragment
def painel(clientes: pd.DataFrame) -> None:
    st.markdown("### Carteira")

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
    ui.cartao(colunas[2], "Média por ajuizamento", formatar_moeda(media))
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
        f"Calculada sobre {len(dias)} caso(s) com as duas datas.",
    )

    base = filtrados.dropna(subset=["data_ajuizamento"]).copy()

    esquerda, direita = st.columns(2)
    with esquerda:
        st.markdown("#### Por responsável")
        por_responsavel = (
            filtrados.groupby("responsavel")
            .agg(
                clientes=("cliente", "count"),
                ajuizamentos=("ajuizado", "sum"),
                honorario_total=("honorario_total", "sum"),
            )
            .reset_index()
            .sort_values("honorario_total", ascending=False)
        )
        por_responsavel["media"] = por_responsavel["honorario_total"] / (
            por_responsavel["ajuizamentos"].replace(0, pd.NA)
        )
        ui.tabela(
            ui.formatar_moedas(por_responsavel, ["honorario_total", "media"]),
            {
                "responsavel": "Responsável",
                "clientes": "Clientes",
                "ajuizamentos": "Ajuizados",
                "honorario_total": "Previsto",
                "media": "Média",
            },
            "Sem dados no filtro.",
        )

    with direita:
        st.markdown("#### Por status da carteira")
        por_status = (
            filtrados.groupby("status")
            .agg(
                clientes=("cliente", "count"),
                honorario_total=("honorario_total", "sum"),
            )
            .reset_index()
            .sort_values("honorario_total", ascending=False)
        )
        ui.tabela(
            ui.formatar_moedas(por_status, ["honorario_total"]),
            {
                "status": "Status",
                "clientes": "Quantidade",
                "honorario_total": "Previsto",
            },
            "Sem dados no filtro.",
        )

    if base.empty:
        return

    st.markdown("#### Honorários por mês e por serviço")
    st.caption("Valores abreviados: mi para milhão, mil para milhar.")
    matriz = ui.matriz_compacta(
        base, "competencia_ajuizamento", "servico", "honorario_total", "Mês"
    )
    st.dataframe(matriz, width="stretch", hide_index=True)

    st.markdown("#### Ajuizamentos por mês e por responsável")
    matriz = ui.matriz_compacta(
        base,
        "competencia_ajuizamento",
        "responsavel",
        "ajuizado",
        "Mês",
        formato="inteiro",
    )
    st.dataframe(matriz, width="stretch", hide_index=True)
