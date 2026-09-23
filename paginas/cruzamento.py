"""
Clientes e processos.

Cruza o CONTROLE DE CLIENTES com o CONTROLE DE PRAZOS para responder,
por cliente: em que etapa está, se já tem processo andando, qual o
próximo prazo, quando houve a última movimentação e o que já saiu de
resultado.

Chave do cruzamento
    1. Link do Bitrix, quando o mesmo negócio aparece nas duas abas.
    2. Nome normalizado (maiúscula, sem acento, sem pontuação).

Limite conhecido: o controle de prazos não traz número de processo,
então o cruzamento é por cliente, não por ação. Cliente com três ações
aparece numa linha só, com os prazos das três somados. Nome digitado de
forma diferente nas duas abas (abreviação, sobrenome faltando) não casa
e cai nas listas de divergência, que servem justamente para corrigir o
cadastro.
"""

from __future__ import annotations

import re
from datetime import datetime

import pandas as pd
import plotly.express as px
import streamlit as st

from db import auth
from db.normalizacao import normalizar_texto
from paginas import componentes as ui

# Sem movimentação no controle de prazos há mais do que isso, o caso
# ajuizado entra como parado. Ajustável na tela.
DIAS_PARADO_PADRAO = 60

SITUACOES_ABERTAS_CRITICAS = {"VENCIDO", "VENCE HOJE", "PRÓXIMOS 7 DIAS"}


def _chave_link(valor) -> str:
    """
    Identificador do negócio no Bitrix.

    Usa o número final do link (/crm/deal/details/12345/) quando existe,
    porque o mesmo negócio pode estar colado com e sem barra final, com
    http e https, ou com parâmetros de URL.
    """
    texto = str(valor or "").strip().lower()
    if not texto:
        return ""
    texto = texto.split("?")[0].rstrip("/")
    numero = re.search(r"(\d+)$", texto)
    return numero.group(1) if numero else texto


def cruzar(clientes: pd.DataFrame, prazos: pd.DataFrame) -> tuple:
    hoje = pd.Timestamp(datetime.now().date())

    cl = clientes.copy()
    cl["chave"] = cl["cliente"].map(normalizar_texto)
    cl["link_id"] = cl["link_bitrix"].map(_chave_link)

    pz = prazos.copy() if not prazos.empty else pd.DataFrame(
        columns=["autor", "link_bitrix", "encerrado", "situacao", "data_controle",
                 "data_evento", "conteudo", "resumo_resultados", "responsavel"]
    )
    pz["link_id"] = pz["link_bitrix"].map(_chave_link)

    # Link do Bitrix primeiro, nome depois.
    por_link = (
        cl[cl["link_id"] != ""].drop_duplicates("link_id").set_index("link_id")["chave"]
    )
    pz["chave"] = pz["link_id"].map(por_link)
    pz["casou_por"] = pz["chave"].notna().map({True: "LINK BITRIX", False: ""})
    sem_link = pz["chave"].isna()
    pz.loc[sem_link, "chave"] = pz.loc[sem_link, "autor"].map(normalizar_texto)
    chaves_clientes = set(cl["chave"])
    pz.loc[sem_link & pz["chave"].isin(chaves_clientes), "casou_por"] = "NOME"

    # ---------------------------------------------- agregado de prazos
    pz["aberto"] = ~pz["encerrado"].astype(bool)
    pz["critico"] = pz["aberto"] & pz["situacao"].isin(SITUACOES_ABERTAS_CRITICAS)
    pz["vencido"] = pz["aberto"] & pz["situacao"].eq("VENCIDO")
    futuro = pz["aberto"] & (pd.to_datetime(pz["data_controle"]) >= hoje)
    pz["proximo_fatal"] = pd.to_datetime(pz["data_controle"]).where(futuro)

    ordenado = pz.sort_values("data_evento", na_position="first")
    ultimo = ordenado.groupby("chave").tail(1).set_index("chave")

    agregado = pz.groupby("chave").agg(
        prazos=("autor", "count"),
        abertos=("aberto", "sum"),
        criticos=("critico", "sum"),
        vencidos=("vencido", "sum"),
        proximo_fatal=("proximo_fatal", "min"),
        ultima_movimentacao=("data_evento", "max"),
    )
    agregado["ultimo_conteudo"] = ultimo["conteudo"].astype(str).str.slice(0, 120)
    resultados = (
        pz[pz["resumo_resultados"].astype(str).str.strip() != ""]
        .sort_values("data_evento")
        .groupby("chave")["resumo_resultados"]
        .last()
    )
    agregado["ultimo_resultado"] = resultados

    # ------------------------------------------- agregado de clientes
    # Um cliente pode ter várias linhas (uma por ação ou contrato). A
    # visão aqui é por cliente, então as linhas são consolidadas.
    def juntar(serie):
        valores = [str(v) for v in serie if str(v).strip()]
        return " | ".join(sorted(set(valores)))

    base = cl.groupby("chave").agg(
        cliente=("cliente", "first"),
        servico=("servico", juntar),
        status=("status", juntar),
        responsavel=("responsavel", juntar),
        acoes=("cliente", "count"),
        ajuizadas=("ajuizado", "sum"),
        encerrado=("encerrado", "all"),
        data_contrato=("data_contrato", "min"),
        data_ajuizamento=("data_ajuizamento", "max"),
        honorario_total=("honorario_total", "sum"),
        resultado_sentenca=("resultado_sentenca", juntar),
    )

    painel = base.join(agregado, how="left")
    for coluna in ("prazos", "abertos", "criticos", "vencidos"):
        painel[coluna] = painel[coluna].fillna(0).astype(int)
    painel["dias_sem_movimento"] = (
        hoje - pd.to_datetime(painel["ultima_movimentacao"])
    ).dt.days.astype("Int64")

    painel["situacao"] = [
        _situacao(linha) for linha in painel.itertuples()
    ]
    painel = painel.reset_index()

    # Prazos cujo autor não existe no controle de clientes.
    orfaos = pz[~pz["chave"].isin(chaves_clientes)].copy()
    orfaos = (
        orfaos.groupby("autor")
        .agg(
            prazos=("autor", "count"),
            abertos=("aberto", "sum"),
            ultima_movimentacao=("data_evento", "max"),
            responsavel=("responsavel", "first"),
        )
        .reset_index()
        .sort_values("abertos", ascending=False)
    )

    casamento = pz["casou_por"].replace("", "SEM CORRESPONDÊNCIA").value_counts()
    return painel, orfaos, casamento, pz


def _situacao(linha) -> str:
    """
    Leitura única do caso, na ordem de gravidade.

    As regras só usam dados que existem nas duas planilhas. O limite de
    dias parado é aplicado depois, na tela, porque é ajustável.
    """
    if linha.vencidos > 0:
        return "PRAZO VENCIDO"
    if linha.encerrado and linha.abertos > 0:
        return "ENCERRADO COM PRAZO ABERTO"
    if linha.ajuizadas > 0 and linha.prazos == 0:
        return "AJUIZADO SEM PRAZO NO CONTROLE"
    if linha.ajuizadas == 0 and linha.prazos > 0 and not linha.encerrado:
        return "PRAZO SEM AJUIZAMENTO NO CADASTRO"
    if linha.criticos > 0:
        return "PRAZO NOS PRÓXIMOS 7 DIAS"
    if linha.encerrado:
        return "ENCERRADO"
    if linha.ajuizadas > 0:
        return "EM ANDAMENTO"
    return "PRÉ-AJUIZAMENTO"


CORES = {
    "PRAZO VENCIDO": "#C62828",
    "ENCERRADO COM PRAZO ABERTO": "#8E24AA",
    "AJUIZADO SEM PRAZO NO CONTROLE": "#EF6C00",
    "PRAZO SEM AJUIZAMENTO NO CADASTRO": "#F7BD2E",
    "PARADO": "#6D4C41",
    "PRAZO NOS PRÓXIMOS 7 DIAS": "#4DA2DA",
    "EM ANDAMENTO": "#2E7D32",
    "PRÉ-AJUIZAMENTO": "#1A3762",
    "ENCERRADO": "#C1B7AD",
}


def render(clientes: pd.DataFrame, prazos: pd.DataFrame) -> None:
    st.subheader("Clientes e processos")
    st.caption(
        "Cruzamento do controle de clientes com o controle de prazos, por "
        "cliente. Casamento pelo link do Bitrix e, na falta dele, pelo nome."
    )

    clientes = auth.aplicar_recorte(clientes)
    prazos = auth.aplicar_recorte(prazos)
    if clientes.empty:
        st.info("Nenhum cliente disponível.")
        return

    painel(clientes, prazos)


@st.fragment
def painel(clientes: pd.DataFrame, prazos: pd.DataFrame) -> None:
    regras = auth.regras_atuais()
    base, orfaos, casamento, prazos_chave = cruzar(clientes, prazos)

    busca = st.text_input(
        "Buscar", placeholder="Cliente, serviço, responsável...",
        key="cx_busca", label_visibility="collapsed",
    )
    with st.expander("Filtros", expanded=False):
        linha = st.columns(4)
        with linha[0]:
            responsaveis = ui.multiselecao(
                "Responsável", ui.opcoes(clientes, "responsavel"), "cx_resp"
            )
        with linha[1]:
            servicos = ui.multiselecao(
                "Serviço", ui.opcoes(clientes, "servico"), "cx_serv"
            )
        with linha[2]:
            limite = st.number_input(
                "Parado há mais de (dias)", min_value=15, max_value=365,
                value=DIAS_PARADO_PADRAO, step=15, key="cx_parado",
            )
        with linha[3]:
            ocultar_encerrados = st.toggle(
                "Ocultar encerrados", value=True, key="cx_ocultar"
            )

    # Parado: ajuizado, sem pendência mais grave e sem evento no
    # controle de prazos há mais do que o limite escolhido.
    parado = (
        base["situacao"].isin(["EM ANDAMENTO"])
        & (base["dias_sem_movimento"] > limite)
    )
    base = base.copy()
    base.loc[parado, "situacao"] = "PARADO"

    filtrados = base
    if responsaveis:
        filtrados = filtrados[
            filtrados["responsavel"].apply(lambda v: any(r in v for r in responsaveis))
        ]
    if servicos:
        filtrados = filtrados[
            filtrados["servico"].apply(lambda v: any(s in v for s in servicos))
        ]
    if ocultar_encerrados:
        filtrados = filtrados[filtrados["situacao"] != "ENCERRADO"]
    filtrados = ui.busca_texto(
        filtrados, ["cliente", "servico", "responsavel", "status"], busca
    )

    situacoes = [s for s in CORES if s in set(filtrados["situacao"])]
    escolha = st.segmented_control(
        "Situação", ["Todas"] + situacoes, default="Todas", key="cx_situacao",
        label_visibility="collapsed",
    )
    visao = filtrados if escolha in (None, "Todas") else filtrados[
        filtrados["situacao"] == escolha
    ]
    ui.resumo_filtro(len(base), len(visao))

    colunas = st.columns(5)
    ui.cartao(colunas[0], "Clientes", len(filtrados))
    ui.cartao(colunas[1], "Com processo andando",
              int((filtrados["ajuizadas"] > 0).sum()))
    ui.cartao(colunas[2], "Prazo vencido",
              int((filtrados["situacao"] == "PRAZO VENCIDO").sum()))
    ui.cartao(colunas[3], "Ajuizado sem prazo",
              int((filtrados["situacao"] == "AJUIZADO SEM PRAZO NO CONTROLE").sum()),
              "Cadastro diz que foi ajuizado, mas não há nenhum prazo lançado "
              "para o cliente. Ou falta lançar o prazo, ou o nome não casou.")
    ui.cartao(colunas[4], f"Parados +{limite} dias",
              int((filtrados["situacao"] == "PARADO").sum()),
              "Ajuizados sem nenhum evento no controle de prazos no período.")

    esquerda, direita = st.columns([1, 1.3])
    with esquerda:
        st.markdown("#### Situação da carteira")
        resumo = filtrados["situacao"].value_counts().reset_index()
        resumo.columns = ["situacao", "quantidade"]
        if not resumo.empty:
            figura = px.pie(
                resumo, names="situacao", values="quantidade",
                color="situacao", color_discrete_map=CORES, hole=0.35,
            )
            figura.update_traces(textinfo="value", sort=False)
            figura.update_layout(
                height=340, margin=dict(t=10, b=10, l=10, r=10),
                legend=dict(font=dict(size=11)),
            )
            st.plotly_chart(figura, width="stretch")
    with direita:
        st.markdown("#### Por responsável")
        responsavel = (
            filtrados.assign(
                vencido=filtrados["situacao"].eq("PRAZO VENCIDO"),
                sem_prazo=filtrados["situacao"].eq("AJUIZADO SEM PRAZO NO CONTROLE"),
                parado=filtrados["situacao"].eq("PARADO"),
            )
            .groupby("responsavel")
            .agg(
                clientes=("cliente", "count"),
                ajuizados=("ajuizadas", lambda s: int((s > 0).sum())),
                abertos=("abertos", "sum"),
                vencido=("vencido", "sum"),
                sem_prazo=("sem_prazo", "sum"),
                parado=("parado", "sum"),
            )
            .reset_index()
            .sort_values("clientes", ascending=False)
        )
        ui.tabela_compacta(
            responsavel,
            {
                "responsavel": "Responsável",
                "clientes": "Clientes",
                "ajuizados": "Ajuizados",
                "abertos": "Prazos abertos",
                "vencido": "Vencidos",
                "sem_prazo": "Sem prazo",
                "parado": "Parados",
            },
            inteiros=["clientes", "ajuizados", "abertos", "vencido",
                      "sem_prazo", "parado"],
        )

    st.markdown("#### Cliente a cliente")
    tabela = visao.sort_values(
        ["situacao", "proximo_fatal"],
        key=lambda s: s.map(list(CORES).index) if s.name == "situacao" else s,
        na_position="last",
    )
    tabela = ui.formatar_datas(
        tabela,
        ["data_contrato", "data_ajuizamento", "proximo_fatal", "ultima_movimentacao"],
    )
    colunas_tabela = {
        "cliente": "Cliente",
        "situacao": "Situação",
        "servico": "Serviço",
        "status": "Status (clientes)",
        "responsavel": "Responsável",
        "acoes": "Linhas no cadastro",
        "data_ajuizamento": "Ajuizamento",
        "prazos": "Prazos",
        "abertos": "Abertos",
        "proximo_fatal": "Próximo fatal",
        "ultima_movimentacao": "Última movimentação",
        "dias_sem_movimento": "Dias sem movimento",
        "ultimo_conteudo": "Último evento",
        "ultimo_resultado": "Último resultado",
        "resultado_sentenca": "Sentença (cadastro)",
    }
    if regras["ver_financeiro"]:
        tabela = ui.formatar_moedas(tabela, ["honorario_total"])
        colunas_tabela["honorario_total"] = "Honorários previstos"
    ui.tabela(tabela, colunas_tabela, "Nenhum cliente na situação escolhida.")
    if regras["ver_financeiro"] and not visao.empty:
        st.markdown(
            f'<div class="linha-total">TOTAL · {len(visao)} cliente(s) · '
            f'{int(visao["prazos"].sum())} prazo(s), {int(visao["abertos"].sum())} '
            f'aberto(s) · Honorários previstos '
            f'{ui.moeda_cheia(visao["honorario_total"].sum(), True)}</div>',
            unsafe_allow_html=True,
        )

    st.download_button(
        "Exportar CSV do cruzamento",
        visao.to_csv(index=False, sep=";").encode("utf-8-sig"),
        file_name="clientes_e_processos.csv",
        mime="text/csv",
    )

    st.markdown("#### Histórico de um cliente")
    nomes = sorted(visao["cliente"].astype(str).unique())
    escolhido = st.selectbox(
        "Cliente", nomes, index=None, placeholder="Escolha um cliente",
        key="cx_cliente", label_visibility="collapsed",
    )
    if escolhido:
        chave = normalizar_texto(escolhido)
        do_cliente = clientes[clientes["cliente"].map(normalizar_texto) == chave]
        eventos = prazos_chave[prazos_chave["chave"] == chave].sort_values(
            "data_evento", ascending=False, na_position="last"
        )
        st.caption(
            f"{len(do_cliente)} linha(s) no controle de clientes · "
            f"{len(eventos)} registro(s) no controle de prazos."
        )
        ui.tabela(
            ui.formatar_datas(do_cliente, ["data_contrato", "data_ajuizamento",
                                           "data_sentenca"]),
            {
                "servico": "Serviço",
                "status": "Status",
                "responsavel": "Responsável",
                "data_contrato": "Contrato",
                "data_ajuizamento": "Ajuizamento",
                "tribunal": "Tribunal",
                "resultado_sentenca": "Sentença",
                "linha_origem": "Linha",
            },
            "Sem linhas no cadastro.",
        )
        ui.tabela(
            ui.formatar_datas(eventos, ["data_evento", "prazo_fatal", "data_final"]),
            {
                "data_evento": "Evento",
                "conteudo": "Conteúdo",
                "prazo_fatal": "Fatal",
                "status": "Status",
                "situacao": "Situação",
                "responsavel": "Responsável",
                "resumo_resultados": "Resultado",
                "casou_por": "Casou por",
                "linha_origem": "Linha",
            },
            "Nenhum prazo lançado para este cliente.",
        )

    with st.expander(
        f"Prazos sem cliente correspondente no cadastro ({len(orfaos)})"
    ):
        st.caption(
            "Autores do controle de prazos que não casaram com nenhum cliente, "
            "nem por link do Bitrix nem por nome. Em geral é nome grafado "
            "diferente nas duas abas ou cliente antigo fora do cadastro."
        )
        ui.tabela_compacta(
            ui.formatar_datas(orfaos, ["ultima_movimentacao"]),
            {
                "autor": "Autor (prazos)",
                "responsavel": "Responsável",
                "prazos": "Prazos",
                "abertos": "Abertos",
                "ultima_movimentacao": "Última movimentação",
            },
            inteiros=["prazos", "abertos"],
            vazio="Todos os prazos casaram com um cliente.",
        )

    with st.expander("Qualidade do cruzamento"):
        qualidade = casamento.reset_index()
        qualidade.columns = ["forma", "registros"]
        ui.tabela_compacta(
            qualidade,
            {"forma": "Como o prazo casou", "registros": "Registros"},
            inteiros=["registros"],
        )
