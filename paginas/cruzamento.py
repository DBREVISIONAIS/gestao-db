"""
Clientes e processos.

Visão por cliente construída só com o CONTROLE DE CLIENTES. Cada linha
do controle é uma demanda (um serviço, uma ação); o cliente é o
conjunto das suas linhas.

O cruzamento com o controle de prazos foi retirado desta tela: sem
número de processo nem ID comum entre as duas abas, a ligação por nome
gerava divergência demais para servir de controle.

Agrupamento das linhas do mesmo cliente: pelo nome-base, que é o nome
sem o complemento que a equipe costuma acrescentar no cadastro (texto
entre parênteses, o que vem depois de " - ", número final e termos de
serviço ou banco no fim). "MARIA SILVA (FEDERAL)" e "MARIA SILVA -
AGIBANK 1" ficam juntos. A coluna "Nomes no cadastro" mostra o que foi
agrupado, para conferência.
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

SEM_RESP = "SEM RESPONSÁVEL"

# Termos que aparecem no FIM do nome como complemento, e não como parte
# do nome da pessoa. Só são retirados enquanto sobrarem ao menos dois
# termos, para não reduzir um nome a uma palavra só.
SUFIXOS = {
    "FEDERAL", "ESTADUAL", "JEF", "JEFP", "JF", "JE", "TRF", "TJ", "INSS",
    "IPREV", "IPSEMG", "SPPREV", "IR", "IRPF", "ISENCAO", "PREV",
    "PREVIDENCIARIO", "PREVIDENCIARIA", "CONTRIBUICAO", "BANCARIO",
    "REVISIONAL", "RMC", "RCC", "CARTAO", "EMPRESTIMO", "CONSIGNADO",
    "BANCO", "AGIBANK", "BMG", "CREFISA", "FACTA", "REALIZE", "CREDI",
    "CREFAZ", "SIMPALA", "PAN", "BANRISUL", "CAIXA", "SANTANDER",
    "BRADESCO", "SICOOB", "BB", "ITAU", "NUBANK", "C6", "MIDWAY",
    "VOTORANTIM", "BV", "OLE", "MERCANTIL", "DAYCOVAL", "SAFRA",
    "PICPAY", "PORTOCRED", "XP", "INTER", "SUPERENDIVIDAMENTO",
    "EXECUCAO", "CUMPRIMENTO", "RECURSO", "AGRAVO",
}
# Conectores que sobram no fim depois de tirar o sufixo ("... DO BRASIL").
CONECTORES = {"DO", "DA", "DE", "DOS", "DAS", "E"}

def nome_base(valor) -> str:
    texto = str(valor or "")
    texto = re.sub(r"\([^)]*\)", " ", texto)
    texto = re.sub(r"\[[^\]]*\]", " ", texto)
    texto = re.split(r"\s+[-–—|/]\s*|\s*[-–—|/]\s+", texto)[0]
    termos = normalizar_texto(texto).split()
    while len(termos) > 2 and (
        termos[-1].isdigit()
        or termos[-1] in SUFIXOS
        or termos[-1] in CONECTORES
        or re.fullmatch(r"\d+[A-Z]?|[A-Z]\d+", termos[-1] or "")
    ):
        termos.pop()
    return " ".join(termos)


# ------------------------------------------------------------------ fases

# Fase da demanda, do marco mais avançado para o menos avançado. É
# derivada das datas que o próprio controle de clientes guarda, e não
# do texto livre do status.
FASES = [
    "EXECUÇÃO CONCLUÍDA",
    "FATURADO",
    "TRANSITADO",
    "SENTENCIADO",
    "AJUIZADO",
    "PRÉ-AJUIZAMENTO",
    "DESCARTADO",
]
CORES_FASE = {
    "EXECUÇÃO CONCLUÍDA": "#2E7D32",
    "FATURADO": "#66BB6A",
    "TRANSITADO": "#1A3762",
    "SENTENCIADO": "#4DA2DA",
    "AJUIZADO": "#F7BD2E",
    "PRÉ-AJUIZAMENTO": "#C1B7AD",
    "DESCARTADO": "#C62828",
}

# Marcos que viram eventos no histórico do cliente.
MARCOS = {
    "data_contrato": "Contrato",
    "data_ajuizamento": "Ajuizamento",
    "data_sentenca": "Sentença",
    "data_transito": "Trânsito em julgado",
    "data_primeiro_faturamento": "1º faturamento",
    "data_faturamento_restituicao": "Faturamento da restituição",
    "data_conclusao_execucao": "Conclusão da execução",
}


def _data(linha, campo):
    valor = linha.get(campo) if hasattr(linha, "get") else getattr(linha, campo, None)
    return valor if isinstance(valor, pd.Timestamp) and pd.notna(valor) else None


def fase_da_demanda(linha) -> str:
    status = normalizar_texto(linha.get("status"))
    if "DESCART" in status:
        return "DESCARTADO"
    if _data(linha, "data_conclusao_execucao"):
        return "EXECUÇÃO CONCLUÍDA"
    if _data(linha, "data_faturamento_restituicao") or _data(
        linha, "data_primeiro_faturamento"
    ):
        return "FATURADO"
    if _data(linha, "data_transito"):
        return "TRANSITADO"
    if _data(linha, "data_sentenca") or linha.get("resultado_sentenca") not in (
        None, "", "SEM SENTENÇA"
    ):
        return "SENTENCIADO"
    if linha.get("ajuizado"):
        return "AJUIZADO"
    return "PRÉ-AJUIZAMENTO"


def ultimo_marco(linha) -> tuple:
    """
    Último marco com data preenchida: (nome, data).

    Data futura é erro de digitação no controle e fica de fora, senão
    viraria o "último marco" e o contador de dias sairia negativo.
    """
    hoje = pd.Timestamp(datetime.now().date())
    melhor = (None, pd.NaT)
    for campo, rotulo in MARCOS.items():
        data = _data(linha, campo)
        if data is not None and data <= hoje and (pd.isna(melhor[1]) or data >= melhor[1]):
            melhor = (rotulo, data)
    return melhor


# ------------------------------------------------------------ preparação


def preparar(clientes: pd.DataFrame) -> pd.DataFrame:
    """Uma linha por demanda, com fase, último marco e chave do cliente."""
    dados = clientes.copy()
    for campo in MARCOS:
        if campo not in dados.columns:
            dados[campo] = pd.NaT
    dados["chave"] = dados["cliente"].map(nome_base)
    registros = dados.to_dict("records")
    dados["fase"] = [fase_da_demanda(r) for r in registros]
    marcos = [ultimo_marco(r) for r in registros]
    dados["ultimo_marco"] = [m[0] or "" for m in marcos]
    dados["data_ultimo_marco"] = pd.to_datetime([m[1] for m in marcos])
    hoje = pd.Timestamp(datetime.now().date())
    dados["dias_desde_marco"] = (hoje - dados["data_ultimo_marco"]).dt.days.astype("Int64")
    dados["ordem_fase"] = dados["fase"].map(FASES.index)
    return dados


def _juntar(serie) -> str:
    return " | ".join(sorted({str(v).strip() for v in serie if str(v).strip()}))


def _juntar_responsavel(serie) -> str:
    valores = {str(v).strip() for v in serie if str(v).strip()}
    if len(valores) > 1:
        valores.discard(SEM_RESP)
    return " | ".join(sorted(valores)) or SEM_RESP


def _servicos_com_contagem(serie) -> str:
    contagem = serie.value_counts()
    return ", ".join(
        f"{servico} ({qtd})" if qtd > 1 else servico for servico, qtd in contagem.items()
    )


def _primeiro_link(serie):
    for valor in serie:
        if ui.url_bitrix(valor):
            return valor
    return ""


def por_cliente(demandas: pd.DataFrame) -> pd.DataFrame:
    ativas = demandas[demandas["fase"] != "DESCARTADO"]

    base = demandas.groupby("chave").agg(
        cliente=("cliente", "first"),
        nomes_no_cadastro=("cliente", _juntar),
        demandas=("cliente", "count"),
        servicos=("servico", _servicos_com_contagem),
        responsavel=("responsavel", _juntar_responsavel),
        status=("status", _juntar),
        primeiro_contrato=("data_contrato", "min"),
        ultimo_ajuizamento=("data_ajuizamento", "max"),
        ajuizadas=("ajuizado", "sum"),
        descartadas=("fase", lambda s: int((s == "DESCARTADO").sum())),
        honorario_total=("honorario_total", "sum"),
        valor_ajuizado=("valor_ajuizado", "sum"),
        link_bitrix=("link_bitrix", _primeiro_link),
        data_ultimo_marco=("data_ultimo_marco", "max"),
    )

    # Fase mais avançada entre as demandas não descartadas. Cliente só
    # com demandas descartadas fica como DESCARTADO.
    mais_avancada = ativas.groupby("chave")["ordem_fase"].min().map(lambda i: FASES[i])
    base["fase_mais_avancada"] = mais_avancada.reindex(base.index).fillna("DESCARTADO")

    abertas = ativas[~ativas["fase"].isin(["EXECUÇÃO CONCLUÍDA"])]
    base["demandas_em_aberto"] = (
        abertas.groupby("chave").size().reindex(base.index).fillna(0).astype(int)
    )

    ultimo = (
        demandas.dropna(subset=["data_ultimo_marco"])
        .sort_values("data_ultimo_marco")
        .groupby("chave").tail(1).set_index("chave")
    )
    base["ultimo_marco"] = (
        ultimo["ultimo_marco"] + " · " + ultimo["servico"].astype(str)
    ).reindex(base.index).fillna("")
    hoje = pd.Timestamp(datetime.now().date())
    base["dias_desde_marco"] = (
        (hoje - pd.to_datetime(base["data_ultimo_marco"])).dt.days.astype("Int64")
    )
    return base.reset_index()


# ----------------------------------------------------------------- tela


def render(clientes: pd.DataFrame) -> None:
    st.subheader("Clientes e processos")
    st.caption(
        "Visão por cliente a partir do controle de clientes. Cada linha do "
        "controle é uma demanda; a fase vem das datas preenchidas (ajuizamento, "
        "sentença, trânsito, faturamento, conclusão da execução)."
    )

    clientes = auth.aplicar_recorte(clientes)
    if clientes.empty:
        st.info("Nenhum cliente disponível.")
        return

    painel(preparar(clientes))


@st.fragment
def painel(demandas: pd.DataFrame) -> None:
    regras = auth.regras_atuais()
    financeiro = regras["ver_financeiro"]

    busca = st.text_input(
        "Buscar cliente", placeholder="Nome do cliente, serviço, responsável...",
        key="cx_busca", label_visibility="collapsed",
    )
    with st.expander("Filtros", expanded=False):
        linha = st.columns(4)
        with linha[0]:
            servicos = ui.multiselecao(
                "Serviço", ui.opcoes(demandas, "servico"), "cx_serv"
            )
        with linha[1]:
            responsaveis = ui.multiselecao(
                "Responsável", ui.opcoes(demandas, "responsavel"), "cx_resp"
            )
        with linha[2]:
            status = ui.multiselecao(
                "Status (controle)", ui.opcoes(demandas, "status"), "cx_status"
            )
        with linha[3]:
            incluir_descartadas = st.toggle(
                "Incluir descartadas", value=False, key="cx_descartadas"
            )
        filtradas = ui.filtro_periodo(
            demandas, "data_contrato", "Contrato entre", "cx_periodo"
        )

    filtradas = ui.aplicar_multiselecao(filtradas, "servico", servicos)
    filtradas = ui.aplicar_multiselecao(filtradas, "responsavel", responsaveis)
    filtradas = ui.aplicar_multiselecao(filtradas, "status", status)
    if not incluir_descartadas:
        filtradas = filtradas[filtradas["fase"] != "DESCARTADO"]
    filtradas = ui.busca_texto(
        filtradas, ["cliente", "servico", "responsavel", "status", "diagnostico"], busca
    )

    fases = [f for f in FASES if f in set(filtradas["fase"])]
    escolha = st.segmented_control(
        "Fase", ["Todas"] + fases, default="Todas", key="cx_fase",
        label_visibility="collapsed",
    )
    if escolha not in (None, "Todas"):
        filtradas = filtradas[filtradas["fase"] == escolha]

    if filtradas.empty:
        st.info("Nenhuma demanda nos filtros atuais.")
        return

    clientes = por_cliente(filtradas)
    st.caption(
        f"{len(clientes)} cliente(s) · {len(filtradas)} demanda(s) no recorte, "
        f"de {demandas['chave'].nunique()} cliente(s) e {len(demandas)} demanda(s) "
        "no controle."
    )

    colunas = st.columns(5)
    ui.cartao(colunas[0], "Clientes", len(clientes))
    ui.cartao(colunas[1], "Demandas", len(filtradas))
    ui.cartao(
        colunas[2], "Clientes com 2+ demandas", int((clientes["demandas"] > 1).sum()),
        "Clientes com mais de uma linha no controle, no recorte atual.",
    )
    ui.cartao(colunas[3], "Demandas ajuizadas", int(filtradas["ajuizado"].sum()))
    if financeiro:
        ui.cartao(
            colunas[4], "Honorários previstos",
            ui.moeda_cheia(filtradas["honorario_total"].sum(), True),
        )
    else:
        ui.cartao(
            colunas[4], "Com sentença",
            int(filtradas["fase"].isin(FASES[:4]).sum()),
        )

    _resumos(filtradas, financeiro)
    _lista_de_clientes(clientes, financeiro)
    _historico(demandas, clientes, financeiro)


def _resumos(filtradas: pd.DataFrame, financeiro: bool) -> None:
    esquerda, direita = st.columns([1, 1.4])
    with esquerda:
        st.markdown("#### Demandas por fase")
        contagem = filtradas["fase"].value_counts().reindex(FASES).dropna().reset_index()
        contagem.columns = ["fase", "demandas"]
        figura = px.pie(
            contagem, names="fase", values="demandas", color="fase",
            color_discrete_map=CORES_FASE, hole=0.35,
        )
        figura.update_traces(textinfo="value", sort=False)
        figura.update_layout(height=320, margin=dict(t=10, b=10, l=10, r=10))
        st.plotly_chart(figura, width="stretch", key="cx_pizza_fase")

    with direita:
        st.markdown("#### Por serviço")
        tabela = filtradas.assign(
            sentenciada=filtradas["fase"].isin(FASES[:4]),
            procedente=filtradas["resultado_sentenca"].isin(
                ["PROCEDENTE", "PARCIALMENTE PROCEDENTE"]
            ),
        )
        por_servico = (
            tabela.groupby("servico")
            .agg(
                clientes=("chave", "nunique"),
                demandas=("cliente", "count"),
                ajuizadas=("ajuizado", "sum"),
                sentenciadas=("sentenciada", "sum"),
                procedentes=("procedente", "sum"),
                honorario_total=("honorario_total", "sum"),
                valor_ajuizado=("valor_ajuizado", "sum"),
            )
            .reset_index()
            .sort_values("demandas", ascending=False)
        )
        colunas = {
            "servico": "Serviço",
            "clientes": "Clientes",
            "demandas": "Demandas",
            "ajuizadas": "Ajuizadas",
            "sentenciadas": "Com sentença",
            "procedentes": "Procedentes",
        }
        moedas = []
        if financeiro:
            colunas.update({
                "valor_ajuizado": "Valor ajuizado (R$)",
                "honorario_total": "Honorários (R$)",
            })
            moedas = ["valor_ajuizado", "honorario_total"]
        ui.tabela_compacta(
            por_servico, colunas, moedas=moedas,
            inteiros=["clientes", "demandas", "ajuizadas", "sentenciadas",
                      "procedentes"],
        )
        st.caption(
            "Na linha de total, Clientes soma os clientes de cada serviço: quem "
            "tem dois serviços conta duas vezes. O número de clientes únicos "
            "está no cartão acima."
        )

    st.markdown("#### Serviço × fase")
    ui.matriz_com_total(
        filtradas.assign(um=1), "servico", "fase", "um", "Serviço",
        formato="inteiro",
    )


def _lista_de_clientes(clientes: pd.DataFrame, financeiro: bool) -> None:
    st.markdown("#### Cliente a cliente")
    ordenacao = st.selectbox(
        "Ordenar por",
        ["Mais demandas", "Nome", "Mais tempo sem marco novo", "Ajuizamento mais recente"]
        + (["Maior honorário"] if financeiro else []),
        key="cx_ordem",
    )
    criterio = {
        "Mais demandas": (["demandas", "cliente"], [False, True]),
        "Nome": (["cliente"], [True]),
        "Mais tempo sem marco novo": (["dias_desde_marco"], [False]),
        "Ajuizamento mais recente": (["ultimo_ajuizamento"], [False]),
        "Maior honorário": (["honorario_total"], [False]),
    }[ordenacao]
    lista = clientes.sort_values(criterio[0], ascending=criterio[1], na_position="last")
    lista = ui.formatar_datas(
        lista, ["primeiro_contrato", "ultimo_ajuizamento", "data_ultimo_marco"]
    )
    colunas = {
        "link_bitrix": "Bitrix",
        "cliente": "Cliente",
        "demandas": "Demandas",
        "servicos": "Serviços",
        "fase_mais_avancada": "Fase mais avançada",
        "demandas_em_aberto": "Em aberto",
        "responsavel": "Responsável",
        "primeiro_contrato": "1º contrato",
        "ultimo_ajuizamento": "Último ajuizamento",
        "ultimo_marco": "Último marco",
        "data_ultimo_marco": "Data do marco",
        "dias_desde_marco": "Dias desde o marco",
        "status": "Status no controle",
        "nomes_no_cadastro": "Nomes no cadastro",
    }
    if financeiro:
        lista = ui.formatar_moedas(lista, ["honorario_total", "valor_ajuizado"])
        colunas["honorario_total"] = "Honorários previstos"
        colunas["valor_ajuizado"] = "Valor ajuizado"
    ui.tabela(lista, colunas, "Nenhum cliente no recorte.")

    extra = (
        f" · Honorários previstos {ui.moeda_cheia(clientes['honorario_total'].sum(), True)}"
        f" · Valor ajuizado {ui.moeda_cheia(clientes['valor_ajuizado'].sum(), True)}"
        if financeiro else ""
    )
    st.markdown(
        f'<div class="linha-total">TOTAL · {len(clientes)} cliente(s) · '
        f'{int(clientes["demandas"].sum())} demanda(s){extra}</div>',
        unsafe_allow_html=True,
    )
    st.download_button(
        "Exportar CSV por cliente",
        clientes.drop(columns=["chave"]).to_csv(index=False, sep=";").encode("utf-8-sig"),
        file_name="clientes_por_cliente.csv",
        mime="text/csv",
    )


def _historico(demandas: pd.DataFrame, clientes: pd.DataFrame, financeiro: bool) -> None:
    st.markdown("#### Histórico do cliente")
    rotulos = dict(
        zip(clientes.sort_values("cliente")["chave"],
            clientes.sort_values("cliente")["cliente"])
    )
    escolhido = st.selectbox(
        "Cliente", list(rotulos), format_func=lambda c: rotulos.get(c, c),
        index=None, placeholder="Escolha um cliente para ver o histórico completo",
        key="cx_cliente", label_visibility="collapsed",
    )
    if not escolhido:
        return

    # O histórico usa todas as demandas do cliente, inclusive as que os
    # filtros acima esconderiam: é a ficha completa.
    do_cliente = demandas[demandas["chave"] == escolhido].sort_values(
        "data_contrato", na_position="last"
    )
    nomes = sorted(do_cliente["cliente"].unique())

    colunas = st.columns(4)
    ui.cartao(colunas[0], "Demandas", len(do_cliente))
    ui.cartao(colunas[1], "Ajuizadas", int(do_cliente["ajuizado"].sum()))
    ui.cartao(
        colunas[2], "Com sentença", int(do_cliente["fase"].isin(FASES[:4]).sum())
    )
    if financeiro:
        ui.cartao(
            colunas[3], "Honorários previstos",
            ui.moeda_cheia(do_cliente["honorario_total"].sum(), True),
        )
    if len(nomes) > 1:
        st.caption("Nomes agrupados neste cliente: " + " · ".join(nomes))

    # Botões do Bitrix, um por demanda com link.
    com_link = do_cliente.assign(url=do_cliente["link_bitrix"].map(ui.url_bitrix))
    com_link = com_link.dropna(subset=["url"]).drop_duplicates("url")
    if not com_link.empty:
        botoes = st.columns(min(len(com_link), 4))
        for posicao, (_, linha) in enumerate(com_link.head(8).iterrows()):
            botoes[posicao % 4].link_button(
                f"Bitrix · {linha['servico']} · linha {linha['linha_origem']}",
                linha["url"], width="stretch",
            )

    st.markdown("##### Demandas")
    visao = ui.formatar_datas(do_cliente, list(MARCOS))
    colunas_demanda = {
        "link_bitrix": "Bitrix",
        "servico": "Serviço",
        "fase": "Fase",
        "status": "Status no controle",
        "responsavel": "Responsável",
        "diagnostico": "Diagnóstico",
        "tribunal": "Tribunal",
        "data_contrato": "Contrato",
        "data_ajuizamento": "Ajuizamento",
        "liminar": "Liminar",
        "resultado_sentenca": "Sentença",
        "data_sentenca": "Data da sentença",
        "data_transito": "Trânsito",
        "data_faturamento_restituicao": "Faturamento restituição",
        "data_conclusao_execucao": "Conclusão execução",
        "comentario": "Comentário",
        "linha_origem": "Linha",
    }
    if financeiro:
        visao = ui.formatar_moedas(visao, ["honorario_total", "valor_ajuizado"])
        colunas_demanda["valor_ajuizado"] = "Valor ajuizado"
        colunas_demanda["honorario_total"] = "Honorários previstos"
    ui.tabela(visao, colunas_demanda, "Sem demandas.")

    # Linha do tempo: todos os marcos de todas as demandas, em ordem.
    eventos = do_cliente.melt(
        id_vars=["servico", "linha_origem"],
        value_vars=list(MARCOS),
        var_name="campo",
        value_name="data",
    ).dropna(subset=["data"])
    st.markdown("##### Linha do tempo")
    if eventos.empty:
        st.info("Nenhuma data de marco preenchida para este cliente.")
        return
    eventos["marco"] = eventos["campo"].map(MARCOS)
    eventos["demanda"] = (
        eventos["servico"].astype(str) + " · linha " + eventos["linha_origem"].astype(str)
    )
    eventos = eventos.sort_values("data")

    figura = px.scatter(
        eventos, x="data", y="demanda", color="marco", symbol="marco",
        hover_data={"data": "|%d/%m/%Y", "marco": True, "demanda": False},
    )
    figura.update_traces(marker=dict(size=12))
    figura.update_layout(
        height=max(220, 70 * eventos["demanda"].nunique() + 120),
        yaxis_title="", xaxis_title="", legend_title="",
        margin=dict(t=10, b=10, l=10, r=10),
    )
    st.plotly_chart(figura, width="stretch", key="cx_linha_tempo")

    ui.tabela_compacta(
        ui.formatar_datas(eventos, ["data"]),
        {"data": "Data", "marco": "Marco", "demanda": "Demanda"},
        total=False,
    )
