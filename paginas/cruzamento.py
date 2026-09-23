"""
Clientes e processos.

Cruza o CONTROLE DE CLIENTES com o CONTROLE DE PRAZOS para responder,
por cliente: em que etapa está, se já tem processo andando, qual o
próximo prazo, quando houve a última movimentação e o que já saiu de
resultado.

Como um prazo é ligado a um cliente, nesta ordem:
    1. LINK BITRIX   mesmo negócio nas duas abas.
    2. DE-PARA       aba DE_PARA da auxiliar, preenchida à mão.
    3. NOME          nome-base igual nas duas abas.
    4. PREFIXO       um nome-base é o começo do outro, com candidato único.
    5. APROXIMADO    semelhança de grafia alta, com candidato único.

Nome-base é o nome sem o que costuma vir de complemento no controle:
texto entre parênteses, o que vem depois de " - ", número final e
termos de serviço ou banco no fim (FEDERAL, AGIBANK, RMC...). Assim
"MARIA SILVA (FEDERAL)", "MARIA SILVA - FEDERAL" e "MARIA SILVA
AGIBANK 1" viram todos "MARIA SILVA".

Limite conhecido: o controle de prazos não traz número de processo,
então a visão é por cliente, não por ação. As ligações PREFIXO e
APROXIMADO são inferência e aparecem marcadas para conferência.
"""

from __future__ import annotations

import re
from datetime import datetime
from difflib import SequenceMatcher, get_close_matches

import pandas as pd
import plotly.express as px
import streamlit as st

from db import auth, modelo
from db.normalizacao import normalizar_texto
from paginas import componentes as ui

DIAS_PARADO_PADRAO = 60
SITUACOES_ABERTAS_CRITICAS = {"VENCIDO", "VENCE HOJE", "PRÓXIMOS 7 DIAS"}
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


# ------------------------------------------------------------- nomes


def nome_base(valor) -> str:
    texto = str(valor or "")
    texto = re.sub(r"\([^)]*\)", " ", texto)
    texto = re.sub(r"\[[^\]]*\]", " ", texto)
    # Corta no primeiro separador com espaço de algum lado. Hífen colado
    # ("MARIA-JOSÉ") faz parte do nome e fica.
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


def _chave_link(valor) -> str:
    """Número do negócio no link do Bitrix, imune a barra final e parâmetros."""
    texto = str(valor or "").strip().lower()
    if not texto:
        return ""
    texto = texto.split("?")[0].split("#")[0].rstrip("/")
    numero = re.search(r"(\d+)$", texto)
    return numero.group(1) if numero else texto


@st.cache_data(ttl=600, show_spinner="Cruzando nomes dos prazos com o cadastro...")
def casar_nomes(
    autores: tuple, bases: tuple, de_para: tuple
) -> dict:
    """
    Autor (nome como está no prazo) -> (nome-base do cliente, método, semelhança).

    Recebe só tuplas de texto, para o cache funcionar. A parte cara é a
    comparação aproximada, e ela só roda para quem não casou antes.
    """
    conjunto = set(bases)
    manual = {}
    for nome_prazo, nome_cliente in de_para:
        alvo = nome_base(nome_cliente)
        if alvo in conjunto:
            manual[normalizar_texto(nome_prazo)] = alvo

    por_primeiro = {}
    for base in bases:
        termos = base.split()
        if termos:
            por_primeiro.setdefault(termos[0], []).append(termos)

    resultado = {}
    for autor in autores:
        chave = normalizar_texto(autor)
        if chave in manual:
            resultado[autor] = (manual[chave], "DE-PARA", 1.0)
            continue

        base = nome_base(autor)
        if not base:
            resultado[autor] = ("", "SEM CORRESPONDÊNCIA", 0.0)
            continue
        if base in conjunto:
            resultado[autor] = (base, "NOME", 1.0)
            continue

        termos = base.split()
        candidatos = {
            " ".join(c)
            for c in por_primeiro.get(termos[0], [])
            if min(len(c), len(termos)) >= 2
            and (c[: len(termos)] == termos or termos[: len(c)] == c)
        }
        if len(candidatos) == 1:
            alvo = candidatos.pop()
            resultado[autor] = (
                alvo, "PREFIXO", round(SequenceMatcher(None, base, alvo).ratio(), 2)
            )
            continue

        # Comparação aproximada restrita a nomes com a mesma inicial,
        # para não comparar contra o cadastro inteiro.
        universo = [b for b in bases if b[:1] == base[:1]]
        proximos = get_close_matches(base, universo, n=2, cutoff=0.88)
        if proximos:
            notas = [SequenceMatcher(None, base, p).ratio() for p in proximos]
            if len(proximos) == 1 or notas[0] - notas[1] >= 0.04:
                resultado[autor] = (proximos[0], "APROXIMADO", round(notas[0], 2))
                continue

        resultado[autor] = ("", "SEM CORRESPONDÊNCIA", 0.0)
    return resultado


# ---------------------------------------------------------- cruzamento


def _juntar(serie) -> str:
    valores = sorted({str(v).strip() for v in serie if str(v).strip()})
    return " | ".join(valores)


def _juntar_responsavel(serie) -> str:
    valores = {str(v).strip() for v in serie if str(v).strip()}
    if len(valores) > 1:
        valores.discard(SEM_RESP)
    return " | ".join(sorted(valores)) or SEM_RESP


def _primeiro_link(serie):
    for valor in serie:
        if str(valor or "").strip():
            return valor
    return ""


def cruzar(clientes: pd.DataFrame, prazos: pd.DataFrame, de_para: pd.DataFrame):
    hoje = pd.Timestamp(datetime.now().date())

    cl = clientes.copy()
    cl["chave"] = cl["cliente"].map(nome_base)
    cl["link_id"] = cl["link_bitrix"].map(_chave_link)
    bases = tuple(sorted(set(cl["chave"]) - {""}))

    if prazos.empty:
        prazos = pd.DataFrame(
            columns=["autor", "link_bitrix", "encerrado", "situacao", "data_controle",
                     "data_evento", "conteudo", "resumo_resultados", "responsavel",
                     "status", "prazo_fatal", "data_final", "linha_origem"]
        )
    pz = prazos.copy()
    pz["link_id"] = pz["link_bitrix"].map(_chave_link)

    por_link = (
        cl[cl["link_id"] != ""].drop_duplicates("link_id").set_index("link_id")["chave"]
    )
    mapa_nomes = casar_nomes(
        tuple(sorted(pz["autor"].astype(str).unique())),
        bases,
        tuple(de_para[["nome_prazo", "nome_cliente"]].itertuples(index=False, name=None)),
    )

    chave, metodo, nota = [], [], []
    for link_id, autor in zip(pz["link_id"], pz["autor"].astype(str)):
        if link_id and link_id in por_link.index:
            chave.append(por_link[link_id]); metodo.append("LINK BITRIX"); nota.append(1.0)
        else:
            alvo, forma, semelhanca = mapa_nomes.get(autor, ("", "SEM CORRESPONDÊNCIA", 0))
            # Sem correspondência mantém o próprio nome-base como chave,
            # para o prazo continuar agrupado na lista de divergências.
            chave.append(alvo or f"#{nome_base(autor)}")
            metodo.append(forma); nota.append(semelhanca)
    pz["chave"] = chave
    pz["casou_por"] = metodo
    pz["semelhanca"] = nota

    # ------------------------------------------- agregado de prazos
    pz["aberto"] = ~pz["encerrado"].astype(bool)
    pz["critico"] = pz["aberto"] & pz["situacao"].isin(SITUACOES_ABERTAS_CRITICAS)
    pz["vencido"] = pz["aberto"] & pz["situacao"].eq("VENCIDO")
    controle = pd.to_datetime(pz["data_controle"], errors="coerce")
    pz["proximo_fatal"] = controle.where(pz["aberto"] & (controle >= hoje))

    ultimo = (
        pz.sort_values("data_evento", na_position="first")
        .groupby("chave").tail(1).set_index("chave")
    )
    agregado = pz.groupby("chave").agg(
        prazos=("autor", "count"),
        abertos=("aberto", "sum"),
        criticos=("critico", "sum"),
        vencidos=("vencido", "sum"),
        proximo_fatal=("proximo_fatal", "min"),
        ultima_movimentacao=("data_evento", "max"),
        nomes_no_prazo=("autor", _juntar),
    )
    agregado["ultimo_conteudo"] = ultimo["conteudo"].astype(str).str.slice(0, 120)
    agregado["ultimo_resultado"] = (
        pz[pz["resumo_resultados"].astype(str).str.strip() != ""]
        .sort_values("data_evento")
        .groupby("chave")["resumo_resultados"].last()
    )

    # ------------------------------------------ agregado de clientes
    base = cl.groupby("chave").agg(
        cliente=("cliente", "first"),
        nomes_no_cadastro=("cliente", _juntar),
        servico=("servico", _juntar),
        status=("status", _juntar),
        responsavel=("responsavel", _juntar_responsavel),
        acoes=("cliente", "count"),
        ajuizadas=("ajuizado", "sum"),
        encerrado=("encerrado", "all"),
        data_contrato=("data_contrato", "min"),
        data_ajuizamento=("data_ajuizamento", "max"),
        honorario_total=("honorario_total", "sum"),
        resultado_sentenca=("resultado_sentenca", _juntar),
        link_bitrix=("link_bitrix", _primeiro_link),
    )

    painel = base.join(agregado, how="left")
    for coluna in ("prazos", "abertos", "criticos", "vencidos"):
        painel[coluna] = painel[coluna].fillna(0).astype(int)
    painel["dias_sem_movimento"] = (
        hoje - pd.to_datetime(painel["ultima_movimentacao"])
    ).dt.days.astype("Int64")
    painel["situacao"] = [_situacao(linha) for linha in painel.itertuples()]
    painel = painel.reset_index()

    orfaos = (
        pz[pz["casou_por"] == "SEM CORRESPONDÊNCIA"]
        .groupby("autor")
        .agg(
            prazos=("autor", "count"),
            abertos=("aberto", "sum"),
            ultima_movimentacao=("data_evento", "max"),
            responsavel=("responsavel", "first"),
        )
        .reset_index()
        .sort_values("abertos", ascending=False)
    )

    # Uma linha por nome do prazo: a quem ele foi ligado e como.
    nomes_cliente = base["cliente"]
    correspondencia = (
        pz.groupby(["autor", "chave", "casou_por"])
        .agg(prazos=("autor", "count"), semelhanca=("semelhanca", "max"))
        .reset_index()
    )
    correspondencia["cliente"] = correspondencia["chave"].map(nomes_cliente).fillna("—")
    correspondencia["nomes_no_cadastro"] = (
        correspondencia["chave"].map(base["nomes_no_cadastro"]).fillna("—")
    )
    correspondencia["semelhanca"] = (correspondencia["semelhanca"] * 100).round(0)

    return painel, orfaos, correspondencia, pz


def _situacao(linha) -> str:
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


# ----------------------------------------------------------------- tela


def render(clientes: pd.DataFrame, prazos: pd.DataFrame) -> None:
    st.subheader("Clientes e processos")
    st.caption(
        "Cruzamento do controle de clientes com o controle de prazos, por "
        "cliente. Ligação pelo link do Bitrix, pela aba DE_PARA e pelo nome, "
        "ignorando complementos como (FEDERAL), - FEDERAL ou AGIBANK 1."
    )

    clientes = auth.aplicar_recorte(clientes)
    prazos = auth.aplicar_recorte(prazos)
    if clientes.empty:
        st.info("Nenhum cliente disponível.")
        return

    painel(clientes, prazos, modelo.carregar_de_para())


@st.fragment
def painel(clientes: pd.DataFrame, prazos: pd.DataFrame, de_para: pd.DataFrame) -> None:
    regras = auth.regras_atuais()
    base, orfaos, correspondencia, prazos_chave = cruzar(clientes, prazos, de_para)

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

    base = base.copy()
    parado = base["situacao"].eq("EM ANDAMENTO") & (
        base["dias_sem_movimento"].fillna(0) > limite
    )
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
        filtrados,
        ["cliente", "nomes_no_cadastro", "nomes_no_prazo", "servico", "responsavel",
         "status"],
        busca,
    )

    situacoes = [s for s in CORES if s in set(filtrados["situacao"])]
    escolha = st.segmented_control(
        "Situação", ["Todas"] + situacoes, default="Todas", key="cx_situacao",
        label_visibility="collapsed",
    )
    # Tudo daqui para baixo usa o recorte da situação escolhida:
    # cartões, pizza, tabela por responsável e lista de clientes.
    visao = filtrados if escolha in (None, "Todas") else filtrados[
        filtrados["situacao"] == escolha
    ]
    ui.resumo_filtro(len(base), len(visao))

    colunas = st.columns(5)
    ui.cartao(colunas[0], "Clientes", len(visao))
    ui.cartao(colunas[1], "Com processo andando", int((visao["ajuizadas"] > 0).sum()))
    ui.cartao(colunas[2], "Prazo vencido",
              int((visao["situacao"] == "PRAZO VENCIDO").sum()))
    ui.cartao(colunas[3], "Ajuizado sem prazo",
              int((visao["situacao"] == "AJUIZADO SEM PRAZO NO CONTROLE").sum()),
              "Cadastro diz que foi ajuizado, mas nenhum prazo foi ligado ao "
              "cliente. Ou falta lançar o prazo, ou o nome não casou (veja a "
              "lista de prazos sem correspondência).")
    ui.cartao(colunas[4], f"Parados +{limite} dias",
              int((visao["situacao"] == "PARADO").sum()),
              "Ajuizados sem nenhum evento no controle de prazos no período.")

    esquerda, direita = st.columns([1, 1.3])
    with esquerda:
        st.markdown("#### Situação da carteira")
        resumo = visao["situacao"].value_counts().reset_index()
        resumo.columns = ["situacao", "quantidade"]
        if resumo.empty:
            st.info("Sem clientes no recorte.")
        else:
            figura = px.pie(
                resumo, names="situacao", values="quantidade",
                color="situacao", color_discrete_map=CORES, hole=0.35,
            )
            figura.update_traces(textinfo="value", sort=False)
            figura.update_layout(
                height=340, margin=dict(t=10, b=10, l=10, r=10),
                legend=dict(font=dict(size=11)),
            )
            st.plotly_chart(figura, width="stretch", key="cx_pizza")
    with direita:
        st.markdown("#### Por responsável")
        responsavel = (
            visao.assign(
                vencido=visao["situacao"].eq("PRAZO VENCIDO"),
                sem_prazo=visao["situacao"].eq("AJUIZADO SEM PRAZO NO CONTROLE"),
                parado_=visao["situacao"].eq("PARADO"),
                ajuizado_=visao["ajuizadas"] > 0,
            )
            .groupby("responsavel")
            .agg(
                clientes=("cliente", "count"),
                ajuizados=("ajuizado_", "sum"),
                abertos=("abertos", "sum"),
                vencido=("vencido", "sum"),
                sem_prazo=("sem_prazo", "sum"),
                parado=("parado_", "sum"),
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
    ordem = list(CORES)
    tabela = visao.sort_values(
        ["situacao", "proximo_fatal"],
        key=lambda s: s.map(ordem.index) if s.name == "situacao" else s,
        na_position="last",
    )
    tabela = ui.formatar_datas(
        tabela,
        ["data_contrato", "data_ajuizamento", "proximo_fatal", "ultima_movimentacao"],
    )
    colunas_tabela = {
        "link_bitrix": "Bitrix",
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
        "nomes_no_cadastro": "Nomes no cadastro",
        "nomes_no_prazo": "Nomes nos prazos",
    }
    if regras["ver_financeiro"]:
        tabela = ui.formatar_moedas(tabela, ["honorario_total"])
        colunas_tabela["honorario_total"] = "Honorários previstos"
    ui.tabela(tabela, colunas_tabela, "Nenhum cliente na situação escolhida.")
    if not visao.empty:
        extra = (
            f' · Honorários previstos {ui.moeda_cheia(visao["honorario_total"].sum(), True)}'
            if regras["ver_financeiro"] else ""
        )
        st.markdown(
            f'<div class="linha-total">TOTAL · {len(visao)} cliente(s) · '
            f'{int(visao["prazos"].sum())} prazo(s), {int(visao["abertos"].sum())} '
            f'aberto(s){extra}</div>',
            unsafe_allow_html=True,
        )

    st.download_button(
        "Exportar CSV do cruzamento",
        visao.to_csv(index=False, sep=";").encode("utf-8-sig"),
        file_name="clientes_e_processos.csv",
        mime="text/csv",
    )

    _historico(visao, clientes, prazos_chave)
    _conferencia(correspondencia, orfaos)


def _historico(visao, clientes, prazos_chave) -> None:
    st.markdown("#### Histórico de um cliente")
    opcoes = visao.sort_values("cliente")[["chave", "cliente"]]
    rotulos = dict(zip(opcoes["chave"], opcoes["cliente"]))
    escolhido = st.selectbox(
        "Cliente", list(rotulos), format_func=lambda c: rotulos.get(c, c),
        index=None, placeholder="Escolha um cliente",
        key="cx_cliente", label_visibility="collapsed",
    )
    if not escolhido:
        return

    do_cliente = clientes[clientes["cliente"].map(nome_base) == escolhido]
    eventos = prazos_chave[prazos_chave["chave"] == escolhido].sort_values(
        "data_evento", ascending=False, na_position="last"
    )

    # Um botão por negócio do Bitrix encontrado no controle de clientes.
    links = (
        do_cliente.assign(url=do_cliente["link_bitrix"].map(ui.url_bitrix))
        .dropna(subset=["url"])
        .drop_duplicates("url")
    )
    if links.empty:
        st.caption("Sem link do Bitrix no controle de clientes.")
    else:
        botoes = st.columns(min(len(links), 4))
        for posicao, (_, linha) in enumerate(links.head(8).iterrows()):
            botoes[posicao % 4].link_button(
                f"Abrir no Bitrix · {linha['servico']}", linha["url"], width="stretch"
            )

    st.caption(
        f"{len(do_cliente)} linha(s) no controle de clientes · "
        f"{len(eventos)} registro(s) no controle de prazos."
    )
    ui.tabela(
        ui.formatar_datas(do_cliente, ["data_contrato", "data_ajuizamento",
                                       "data_sentenca"]),
        {
            "link_bitrix": "Bitrix",
            "cliente": "Nome no cadastro",
            "servico": "Serviço",
            "status": "Status",
            "responsavel": "Responsável",
            "diagnostico": "Diagnóstico",
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
            "autor": "Nome no prazo",
            "conteudo": "Conteúdo",
            "prazo_fatal": "Fatal",
            "status": "Status",
            "situacao": "Situação",
            "responsavel": "Responsável",
            "resumo_resultados": "Resultado",
            "casou_por": "Ligado por",
            "linha_origem": "Linha",
        },
        "Nenhum prazo ligado a este cliente.",
    )


def _conferencia(correspondencia, orfaos) -> None:
    st.markdown("#### Conferência do cruzamento")

    contagem = correspondencia.groupby("casou_por")["prazos"].sum()
    ordem = ["LINK BITRIX", "DE-PARA", "NOME", "PREFIXO", "APROXIMADO",
             "SEM CORRESPONDÊNCIA"]
    resumo = pd.DataFrame(
        {"forma": [f for f in ordem if f in contagem.index],
         "registros": [contagem[f] for f in ordem if f in contagem.index]}
    )
    esquerda, direita = st.columns([1, 2])
    with esquerda:
        ui.tabela_compacta(
            resumo,
            {"forma": "Ligação", "registros": "Prazos"},
            inteiros=["registros"],
        )
        st.caption(
            "PREFIXO e APROXIMADO são inferência: confira ao lado. Ligação "
            "errada ou nome que não casou se resolve na aba DE_PARA da "
            "planilha auxiliar (colunas NOME NO PRAZO e NOME NO CLIENTE)."
        )
    with direita:
        formas = st.multiselect(
            "Mostrar ligações do tipo",
            ordem,
            default=["PREFIXO", "APROXIMADO"],
            key="cx_formas",
        )
        recorte = correspondencia[correspondencia["casou_por"].isin(formas)]
        ui.tabela(
            recorte.sort_values(["casou_por", "semelhanca"]),
            {
                "autor": "Nome no prazo",
                "cliente": "Ligado ao cliente",
                "nomes_no_cadastro": "Nomes no cadastro",
                "casou_por": "Ligação",
                "semelhanca": "Semelhança %",
                "prazos": "Prazos",
            },
            "Nenhuma ligação desse tipo.",
        )
        st.download_button(
            "Exportar correspondências (CSV)",
            correspondencia.drop(columns=["chave"]).to_csv(index=False, sep=";")
            .encode("utf-8-sig"),
            file_name="correspondencia_nomes.csv",
            mime="text/csv",
        )

    with st.expander(f"Prazos sem cliente correspondente ({len(orfaos)} nomes)"):
        ui.tabela_compacta(
            ui.formatar_datas(orfaos, ["ultima_movimentacao"]),
            {
                "autor": "Nome no prazo",
                "responsavel": "Responsável",
                "prazos": "Prazos",
                "abertos": "Abertos",
                "ultima_movimentacao": "Última movimentação",
            },
            inteiros=["prazos", "abertos"],
            vazio="Todos os prazos foram ligados a um cliente.",
        )
