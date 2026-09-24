"""
Controladoria.

Mede o trabalho feito no CONTROLE DE PRAZOS a partir do log de
alterações: prazos novos incluídos, verificações da controladoria,
troca de AGUARDA por data fatal e protocolos marcados pelos advogados.

Cada evento é reconhecido pela mudança de valor na célula, e não pelo
rótulo TIPO_EVENTO, porque a mudança de célula está registrada em
todas as versões do Apps Script:

    PRAZO NOVO      célula AUTOR passa de vazia para preenchida, ou evento
                    LANCAMENTO_PRAZO. Conta uma vez por linha por dia.
                    Preencher DATA EVENTO ou CONTEÚDO não conta: isso
                    acontece também ao completar prazo antigo, e contar
                    cada campo multiplicava a mesma inclusão.
    VERIFICAÇÃO     célula VERIFICAÇÃO CONTROLADORIA recebe valor novo.
    AGUARDA->FATAL  célula FATAL sai de AGUARDA e passa a ter data.
    PROTOCOLO       STATUS passa a conter PROTOCOLADO.

Limites: o painel lê só as últimas MAX_LINHAS_LOG linhas do log
(padrão 30.000), então períodos longos podem estar incompletos; e
edições sem e-mail registrado pelo Google aparecem como NÃO IDENTIFICADO.
"""

from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pandas as pd
import plotly.express as px
import streamlit as st

from db.normalizacao import converter_data_segura, normalizar_texto
from paginas import componentes as ui

CAMPOS_ABERTURA = {"AUTOR", "CLIENTE"}
CAMPOS_FATAL = {"FATAL", "PRAZO FATAL", "DATA FATAL"}
CAMPO_VERIFICACAO = "VERIFICACAO CONTROLADORIA"

EVENTOS = {
    "prazo_novo": "Prazos novos",
    "verificacao": "Verificações",
    "aguarda_fatal": "AGUARDA → fatal",
    "protocolo": "Protocolos",
}
# Retrabalho: fica fora dos cartões principais e tem aba própria.
RETRABALHO = {
    "correcao_conteudo": "Correções de conteúdo",
    "alteracao_fatal": "Alterações de fatal",
}
TODOS_EVENTOS = {**EVENTOS, **RETRABALHO}
CAMPO_CONTEUDO = "CONTEUDO DO PRAZO"
# Edição do conteúdo até este tempo depois da inclusão é acabamento do
# próprio lançamento (digitou, releu, ajustou), não correção de erro.
MINUTOS_AJUSTE = 30
# Edições da mesma pessoa na mesma linha com intervalo menor que isto
# formam uma única sessão de trabalho, para medir o tempo gasto.
MINUTOS_SESSAO = 10

CORES_EVENTO = {
    "Correções de conteúdo": "#C62828",
    "Alterações de fatal": "#8E24AA",
    "Prazos novos": "#1A3762",
    "Verificações": "#4DA2DA",
    "AGUARDA → fatal": "#F7BD2E",
    "Protocolos": "#2E7D32",
}

# Tipos de compromisso acompanhados à parte. A ordem importa: o
# primeiro termo encontrado define o tipo.
TIPOS_COMPROMISSO = (
    ("AUDIÊNCIA", ("AUDIENCIA",)),
    ("SESSÃO DE JULGAMENTO", ("SESSAO", "PAUTA", "JULGAMENTO VIRTUAL",
                              "SUSTENTACAO ORAL")),
    ("PERÍCIA", ("PERICIA", "PERITO", "PERICIAL")),
)

ROTULO_COMPROMISSO = {
    "AUDIÊNCIA": "Audiências",
    "SESSÃO DE JULGAMENTO": "Sessões de julgamento",
    "PERÍCIA": "Perícias",
    "DEMAIS PRAZOS": "Demais prazos",
}

# Audiência, sessão e perícia costumam ser lançadas em duas linhas: uma
# para a intimação (o prazo que corre) e outra para a data do ato.
# O papel de cada linha é inferido pelo conteúdo: menção a intimação,
# publicação ou ciência indica a linha da intimação; o resto é a data do
# ato. É inferência por palavra e deve ser conferida na tabela.
TERMOS_INTIMACAO = ("INTIMA", "PUBLICA", "CIENCIA", "NOTIFICA")
JANELA_PAR_DIAS = 7


def papel_da_linha(texto) -> str:
    normal = normalizar_texto(texto)
    return "INTIMAÇÃO" if any(t in normal for t in TERMOS_INTIMACAO) else "DATA DO ATO"


def agrupar_compromissos(novos: pd.DataFrame) -> pd.DataFrame:
    """
    Junta as linhas do mesmo compromisso: mesmo autor, mesmo tipo,
    incluídas com até JANELA_PAR_DIAS dias de diferença. Devolve uma
    linha por compromisso com a situação do lançamento.
    """
    base = novos[novos["compromisso"] != "DEMAIS PRAZOS"].copy()
    if base.empty:
        return pd.DataFrame()
    base["autor_n"] = base["autor_ref"].map(normalizar_texto)
    base = base.sort_values(["autor_n", "compromisso", "data_hora"])
    salto = (
        base.groupby(["autor_n", "compromisso"])["data_hora"].diff()
        > pd.Timedelta(days=JANELA_PAR_DIAS)
    )
    base["grupo_c"] = salto.groupby([base["autor_n"], base["compromisso"]]).cumsum()

    def situacao(grupo):
        papeis = set(grupo["papel"])
        if len(grupo) > 2:
            return "MAIS DE 2 LINHAS"
        if len(grupo) == 2 and papeis == {"INTIMAÇÃO", "DATA DO ATO"}:
            return "COMPLETO"
        if len(grupo) == 2:
            return "2 LINHAS, MESMO PAPEL"
        return "SÓ INTIMAÇÃO" if papeis == {"INTIMAÇÃO"} else "SÓ DATA DO ATO"

    linhas = []
    for (_, tipo, _), grupo in base.groupby(["autor_n", "compromisso", "grupo_c"]):
        linhas.append({
            "autor": grupo["autor_ref"].iloc[0],
            "compromisso": tipo,
            "linhas": len(grupo),
            "primeira_inclusao": grupo["data_hora"].min(),
            "dia": grupo["dia"].min(),
            "situacao": situacao(grupo),
            "quem": " | ".join(sorted(set(grupo["editor"]))),
            "conteudos": " || ".join(grupo["conteudo_prazo"].astype(str)),
        })
    return pd.DataFrame(linhas)


DIAS = ("Segunda", "Terça", "Quarta", "Quinta", "Sexta", "Sábado", "Domingo")
PADRAO_CONTROLADORIA = ("MARIANA", "CAROL")


def tipo_compromisso(texto) -> str:
    normal = normalizar_texto(texto)
    for rotulo, termos in TIPOS_COMPROMISSO:
        if any(t in normal for t in termos):
            return rotulo
    return "DEMAIS PRAZOS"


# ------------------------------------------------------------ eventos


def _mapa_unico(serie: pd.Series, funcao) -> pd.Series:
    """
    Aplica a função uma vez por valor distinto. No log, os mesmos textos
    se repetem milhares de vezes (nomes de coluna, status, datas), e
    normalizar linha a linha era o que deixava a aba lenta.
    """
    valores = serie.astype(str)
    mapa = {v: funcao(v) for v in valores.unique()}
    return valores.map(mapa)


def extrair_eventos(logs: pd.DataFrame) -> pd.DataFrame:
    """Uma linha por evento reconhecido, com quem, quando e qual prazo."""
    if logs.empty:
        return pd.DataFrame()

    base = logs[
        _mapa_unico(logs["aba_origem"], normalizar_texto).str.contains("PRAZO", na=False)
    ].copy()
    if base.empty:
        return pd.DataFrame()

    base["data_hora"] = pd.to_datetime(base["data_hora"], errors="coerce")
    base = base.dropna(subset=["data_hora"])
    base["campo"] = _mapa_unico(base["cabecalho"], normalizar_texto)
    base["antes"] = base["valor_anterior"].astype(str).str.strip()
    base["depois"] = base["valor_novo"].astype(str).str.strip()
    base["antes_n"] = _mapa_unico(base["antes"], normalizar_texto)
    base["depois_n"] = _mapa_unico(base["depois"], normalizar_texto)

    # Identidade do prazo: ID permanente quando o Apps Script grava, e
    # na falta dele a linha somada ao nome do autor no momento da edição.
    # O nome evita somar dois prazos diferentes que ocuparam a mesma
    # linha em épocas distintas.
    id_prazo = base.get("id_prazo", pd.Series("", index=base.index)).astype(str).str.strip()
    base["prazo"] = np.where(
        id_prazo != "",
        "ID:" + id_prazo,
        base["chave_origem"].astype(str) + "|" + _mapa_unico(base["cliente_autor"], normalizar_texto),
    )

    # Sessão de trabalho: edições seguidas da mesma pessoa na mesma linha,
    # sem pausa maior que MINUTOS_SESSAO. A duração vai da primeira à
    # última edição, com piso de 1 minuto (uma edição isolada também
    # custa tempo). É o tempo mínimo com a planilha aberta naquela linha.
    base = base.sort_values(["editor", "chave_origem", "data_hora"])
    pausa = base.groupby(["editor", "chave_origem"])["data_hora"].diff()
    base["sessao"] = (pausa.isna() | (pausa > pd.Timedelta(minutes=MINUTOS_SESSAO))).cumsum()
    grupo_sessao = base.groupby("sessao")["data_hora"]
    base["minutos_sessao"] = (
        (grupo_sessao.transform("max") - grupo_sessao.transform("min"))
        .dt.total_seconds() / 60
    ).clip(lower=1.0)

    tipo = base["tipo_evento"].astype(str).str.upper()

    abertura = (
        (base["campo"].isin(CAMPOS_ABERTURA) & (base["antes"] == "") & (base["depois"] != ""))
        | tipo.eq("LANCAMENTO_PRAZO")
    )
    verificacao = (
        base["campo"].eq(CAMPO_VERIFICACAO)
        & (base["depois"] != "")
        & (base["depois"] != base["antes"])
    )
    # Conversão de data só nas células de FATAL: é a única coluna em que
    # a data importa aqui, e converter o log inteiro custava segundos.
    e_fatal = base["campo"].isin(CAMPOS_FATAL)
    data_antes = pd.to_datetime(
        _mapa_unico(base["antes"].where(e_fatal, ""), converter_data_segura),
        errors="coerce",
    )
    data_depois = pd.to_datetime(
        _mapa_unico(base["depois"].where(e_fatal, ""), converter_data_segura),
        errors="coerce",
    )
    data_nova = data_depois.notna()
    aguarda = (
        base["campo"].isin(CAMPOS_FATAL)
        & base["antes_n"].str.contains("AGUARDA", na=False)
        & data_nova
    )
    protocolo = (
        base["campo"].eq("STATUS")
        & base["depois_n"].str.contains("PROTOCOLADO", na=False)
        & ~base["antes_n"].str.contains("PROTOCOLADO", na=False)
    )

    correcao = (
        base["campo"].eq(CAMPO_CONTEUDO)
        & (base["antes"] != "")
        & (base["depois_n"] != base["antes_n"])
    )
    alteracao_fatal = (
        base["campo"].isin(CAMPOS_FATAL)
        & data_antes.notna() & data_depois.notna()
        & (data_antes != data_depois)
    )
    base["fatal_antes"] = data_antes
    base["fatal_depois"] = data_depois

    partes = []
    # Uma inclusão por linha por dia. A identidade aqui é só aba + linha,
    # e não o nome do autor: o nome ainda está vazio quando a equipe
    # começa a linha pela data, e isso fazia a mesma inclusão virar duas
    # ou três. O evento LANCAMENTO_PRAZO registrado junto também cai
    # na mesma linha e no mesmo dia, então não soma de novo.
    novos = base[abertura].assign(dia_=lambda d: d["data_hora"].dt.date)
    novos = novos.sort_values("data_hora").drop_duplicates(["chave_origem", "dia_"])
    novos["gatilho"] = np.where(
        novos["tipo_evento"].astype(str).str.upper().eq("LANCAMENTO_PRAZO"),
        "LANCAMENTO_PRAZO", "AUTOR preenchido",
    )
    partes.append(novos.drop(columns="dia_").assign(evento="prazo_novo"))
    partes.append(base[verificacao].assign(evento="verificacao"))
    partes.append(base[aguarda].assign(evento="aguarda_fatal"))
    # Mesmo prazo protocolado duas vezes no mesmo dia (status desfeito e
    # refeito) conta uma vez.
    protocolos = base[protocolo].assign(dia=lambda d: d["data_hora"].dt.date)
    partes.append(
        protocolos.drop_duplicates(["prazo", "dia"]).drop(columns="dia")
        .assign(evento="protocolo")
    )

    partes.append(base[correcao].assign(evento="correcao_conteudo"))
    partes.append(base[alteracao_fatal].assign(evento="alteracao_fatal"))

    eventos = pd.concat(partes, ignore_index=True)
    if eventos.empty:
        return eventos

    # Quem incluiu a linha e quando: a inclusão mais recente da mesma
    # linha antes do evento. Serve para separar autocorreção de correção
    # feita por outra pessoa e para medir quanto tempo depois veio a
    # correção ou a mudança de fatal.
    inclusoes = (
        novos[["chave_origem", "data_hora", "editor"]]
        .rename(columns={"data_hora": "incluido_em", "editor": "incluido_por"})
        .sort_values("incluido_em")
    )
    eventos = eventos.sort_values("data_hora")
    if not inclusoes.empty:
        eventos = pd.merge_asof(
            eventos, inclusoes, left_on="data_hora", right_on="incluido_em",
            by="chave_origem", direction="backward",
        )
    else:
        eventos["incluido_em"] = pd.NaT
        eventos["incluido_por"] = ""
    eventos["minutos_apos_inclusao"] = (
        (eventos["data_hora"] - eventos["incluido_em"]).dt.total_seconds() / 60
    )
    eventos["dias_corridos_fatal"] = (
        pd.to_datetime(eventos["fatal_depois"]) - pd.to_datetime(eventos["fatal_antes"])
    ).dt.days

    # Conteúdo do prazo: o último texto não vazio registrado no log para
    # aquele prazo, porque na hora da inclusão o conteúdo pode ainda
    # não ter sido digitado.
    conteudo = (
        base[base["conteudo"].astype(str).str.strip() != ""]
        .sort_values("data_hora")
        .groupby("prazo")["conteudo"].last()
    )
    eventos["conteudo_prazo"] = eventos["prazo"].map(conteudo).fillna(eventos["conteudo"])
    eventos["compromisso"] = _mapa_unico(eventos["conteudo_prazo"], tipo_compromisso)
    eventos["papel"] = [
        papel_da_linha(texto) if tipo != "DEMAIS PRAZOS" else ""
        for texto, tipo in zip(eventos["conteudo_prazo"], eventos["compromisso"])
    ]
    autor = eventos["cliente_autor"].astype(str).str.strip()
    eventos["autor_ref"] = autor.where(autor != "", eventos["depois"])
    eventos["rotulo"] = eventos["evento"].map(TODOS_EVENTOS)
    eventos["dia"] = eventos["data_hora"].dt.normalize()
    eventos["hora"] = eventos["data_hora"].dt.hour
    eventos["dia_semana"] = eventos["data_hora"].dt.dayofweek
    return eventos


# ---------------------------------------------------------------- tela


def render(prazos: pd.DataFrame, logs: pd.DataFrame) -> None:
    st.subheader("Controladoria")
    st.caption(
        "Produção diária no controle de prazos, medida pelo log de alterações: "
        "prazos novos, verificações, troca de AGUARDA por data fatal e "
        "protocolos marcados."
    )

    if logs.empty:
        st.info("Sem histórico de alterações disponível.")
        return

    eventos = extrair_eventos(logs)
    if eventos.empty:
        st.info("Nenhum evento do controle de prazos encontrado no log.")
        return

    painel(eventos, prazos)


@st.fragment
def painel(eventos: pd.DataFrame, prazos: pd.DataFrame) -> None:
    hoje = date.today()
    mais_antigo = eventos["dia"].min().date()

    controles = st.columns([1.4, 2.2])
    with controles[0]:
        intervalo = st.date_input(
            "Período",
            value=(max(hoje - timedelta(days=30), mais_antigo), hoje),
            min_value=mais_antigo,
            max_value=hoje,
            format="DD/MM/YYYY",
            key="ctl_periodo",
        )
    pessoas = sorted(eventos["editor"].dropna().unique())
    padrao = [
        p for p in pessoas
        if any(t in normalizar_texto(p) for t in PADRAO_CONTROLADORIA)
    ]
    with controles[1]:
        equipe = st.multiselect(
            "Equipe da controladoria",
            pessoas,
            default=padrao,
            key="ctl_equipe",
            help="Quem não estiver aqui é tratado como advogado na análise de "
            "protocolos. O nome vem do e-mail de quem editou a planilha.",
        )

    if not isinstance(intervalo, (tuple, list)) or len(intervalo) != 2:
        st.info("Escolha a data inicial e a final.")
        return
    inicio, fim = pd.Timestamp(intervalo[0]), pd.Timestamp(intervalo[1])
    recorte = eventos[(eventos["dia"] >= inicio) & (eventos["dia"] <= fim)].copy()
    recorte["grupo"] = np.where(
        recorte["editor"].isin(equipe), "CONTROLADORIA", "ADVOGADOS E DEMAIS"
    )

    dias_uteis = max(int(np.busday_count(inicio.date(), (fim + pd.Timedelta(days=1)).date())), 1)
    _cartoes(recorte, dias_uteis)

    # Seletor em vez de st.tabs: com abas, o Streamlit calcula e desenha
    # o conteúdo de todas a cada clique; com o seletor, só a seção aberta.
    secoes = {
        "Por dia": lambda: _por_dia(recorte),
        "Controladoria": lambda: _controladoria(recorte, equipe, eventos),
        "Retrabalho": lambda: _retrabalho(recorte),
        "Protocolos dos advogados": lambda: _protocolos(recorte),
        "Horários": lambda: _horarios(recorte),
        "Audiências, sessões e perícias": lambda: _compromissos(recorte, prazos),
        "Médias": lambda: _medias(recorte, equipe, inicio, fim),
        "Resumo mensal": lambda: _mensal(eventos, equipe),
    }
    secao = st.segmented_control(
        "Seção", list(secoes), default="Por dia", key="ctl_secao",
        label_visibility="collapsed",
    ) or "Por dia"
    secoes[secao]()


def _cartoes(recorte: pd.DataFrame, dias_uteis: int) -> None:
    colunas = st.columns(4)
    for coluna, (codigo, rotulo) in zip(colunas, EVENTOS.items()):
        total = int((recorte["evento"] == codigo).sum())
        ui.cartao(
            coluna, rotulo, f"{total}",
            f"Média de {total / dias_uteis:.1f} por dia útil no período "
            f"({dias_uteis} dias úteis).".replace(".", ",", 1),
        )
    novos = recorte[recorte["evento"] == "prazo_novo"]
    linhas_compromisso = int((novos["compromisso"] != "DEMAIS PRAZOS").sum())
    if linhas_compromisso:
        distintos = len(agrupar_compromissos(novos))
        st.caption(
            f"Dos {len(novos)} prazos novos, {len(novos) - linhas_compromisso} são "
            f"prazos comuns e {linhas_compromisso} são linhas de audiência, sessão ou "
            f"perícia, que correspondem a {distintos} compromisso(s) distinto(s) "
            "(em regra, duas linhas por compromisso: intimação e data do ato)."
        )
    nao_identificados = int((recorte["editor"] == "NÃO IDENTIFICADO").sum())
    if nao_identificados:
        st.caption(
            f"{nao_identificados} evento(s) sem e-mail registrado pelo Google: "
            "entram nos totais, mas não são atribuídos a ninguém."
        )


def _por_dia(recorte: pd.DataFrame) -> None:
    if recorte.empty:
        st.info("Nenhum evento no período.")
        return

    diario = (
        recorte[recorte["evento"].isin(list(EVENTOS))]
        .groupby(["dia", "rotulo"]).size().reset_index(name="quantidade")
    )
    figura = px.bar(
        diario, x="dia", y="quantidade", color="rotulo", barmode="group",
        color_discrete_map=CORES_EVENTO,
    )
    figura.update_layout(
        height=380, xaxis_title="", yaxis_title="Eventos", legend_title="",
        legend=dict(orientation="h", y=1.1),
    )
    figura.update_xaxes(tickformat="%d/%m")
    st.plotly_chart(figura, width="stretch", key="ctl_diario")

    tabela = (
        recorte.pivot_table(index="dia", columns="evento", values="editor",
                            aggfunc="count", fill_value=0)
        .reindex(columns=list(EVENTOS), fill_value=0)
        .sort_index(ascending=False)
        .reset_index()
    )
    novos = recorte[recorte["evento"] == "prazo_novo"]
    comuns = novos[novos["compromisso"] == "DEMAIS PRAZOS"].groupby("dia").size()
    linhas_c = novos[novos["compromisso"] != "DEMAIS PRAZOS"].groupby("dia").size()
    grupos = agrupar_compromissos(novos)
    distintos = grupos.groupby("dia").size() if not grupos.empty else pd.Series(dtype=int)
    tabela["comuns"] = tabela["dia"].map(comuns).fillna(0).astype(int)
    tabela["linhas_compromisso"] = tabela["dia"].map(linhas_c).fillna(0).astype(int)
    tabela["compromissos"] = tabela["dia"].map(distintos).fillna(0).astype(int)
    tabela["dia_semana"] = tabela["dia"].dt.dayofweek.map(lambda d: DIAS[d])
    tabela["dia"] = tabela["dia"].dt.strftime("%d/%m/%Y")
    colunas = {
        "dia": "Dia", "dia_semana": "",
        "prazo_novo": "Linhas novas",
        "comuns": "Prazos comuns",
        "linhas_compromisso": "Linhas aud./sessão/perícia",
        "compromissos": "Compromissos distintos",
        "verificacao": EVENTOS["verificacao"],
        "aguarda_fatal": EVENTOS["aguarda_fatal"],
        "protocolo": EVENTOS["protocolo"],
    }
    ui.tabela_compacta(tabela, colunas, inteiros=[c for c in colunas if c not in ("dia", "dia_semana")])
    st.caption(
        "Linhas novas = prazos comuns + linhas de audiência, sessão e perícia. "
        "Compromissos distintos junta as duas linhas do mesmo ato (intimação e data), "
        "pelo autor e tipo, incluídas com até 7 dias de diferença; o compromisso "
        "entra no dia da primeira linha."
    )

    # Conferência linha a linha: para bater o número com a planilha.
    st.markdown("#### Conferir prazos novos de um dia")
    dias = sorted(recorte.loc[recorte["evento"] == "prazo_novo", "dia"].unique(),
                  reverse=True)
    if not dias:
        return
    escolhido = st.selectbox(
        "Dia", dias, format_func=lambda d: pd.Timestamp(d).strftime("%d/%m/%Y"),
        key="ctl_conferir_dia",
    )
    lista = recorte[(recorte["evento"] == "prazo_novo") & (recorte["dia"] == escolhido)]
    st.caption(
        f"{len(lista)} inclusão(ões) em {pd.Timestamp(escolhido).strftime('%d/%m/%Y')}. "
        "A linha é a da planilha no momento da edição; se linhas foram inseridas "
        "acima depois, o número atual pode ter mudado."
    )
    st.dataframe(
        lista.sort_values("data_hora")
        .assign(
            hora_=lambda d: d["data_hora"].dt.strftime("%H:%M"),
            autor_=lambda d: d["cliente_autor"].astype(str).str.strip()
            .where(d["cliente_autor"].astype(str).str.strip() != "", d["depois"]),
        )
        [["hora_", "editor", "linha", "autor_", "conteudo_prazo", "compromisso",
          "papel", "gatilho"]]
        .rename(columns={"hora_": "Hora", "editor": "Quem", "linha": "Linha",
                         "autor_": "Autor incluído", "conteudo_prazo": "Conteúdo",
                         "compromisso": "Tipo", "papel": "Linha de",
                         "gatilho": "Reconhecido por"}),
        width="stretch", hide_index=True,
    )


def _controladoria(recorte: pd.DataFrame, equipe: list, todos: pd.DataFrame) -> None:
    if not equipe:
        st.info("Selecione acima quem faz parte da controladoria.")
        return
    time_ = recorte[recorte["grupo"] == "CONTROLADORIA"]
    if time_.empty:
        st.info("Nenhum evento da controladoria no período.")
        return

    por_pessoa = (
        time_.pivot_table(index="editor", columns="evento", values="dia",
                          aggfunc="count", fill_value=0)
        .reindex(columns=list(EVENTOS), fill_value=0)
        .reset_index()
    )
    por_pessoa["dias_ativos"] = por_pessoa["editor"].map(
        time_.groupby("editor")["dia"].nunique()
    )
    por_pessoa["total"] = por_pessoa[list(EVENTOS)].sum(axis=1)
    por_pessoa["media_dia"] = (
        por_pessoa["total"] / por_pessoa["dias_ativos"]
    ).map(lambda v: f"{v:.1f}".replace(".", ","))

    st.markdown("#### Por pessoa")
    ui.tabela_compacta(
        por_pessoa,
        {"editor": "Pessoa", **EVENTOS, "total": "Total",
         "dias_ativos": "Dias com registro", "media_dia": "Média por dia ativo"},
        inteiros=list(EVENTOS) + ["total", "dias_ativos"],
        percentuais=[],
        somar=list(EVENTOS) + ["total"],
    )
    st.caption("Média por dia ativo: total de eventos dividido pelos dias em que a "
               "pessoa registrou algo no período.")

    diario = (
        time_.groupby(["dia", "editor"]).size().reset_index(name="eventos")
    )
    figura = px.line(diario, x="dia", y="eventos", color="editor", markers=True)
    figura.update_layout(height=320, xaxis_title="", yaxis_title="Eventos por dia",
                         legend_title="")
    figura.update_xaxes(tickformat="%d/%m")
    st.plotly_chart(figura, width="stretch", key="ctl_equipe_dia")

    # Cadência das verificações: intervalo entre duas verificações
    # seguidas do mesmo prazo. A rotina do escritório é dia sim, dia não.
    st.markdown("#### Cadência das verificações")
    verificacoes = todos[todos["evento"] == "verificacao"].sort_values("data_hora")
    verificacoes = verificacoes.assign(
        anterior=verificacoes.groupby("prazo")["data_hora"].shift()
    ).dropna(subset=["anterior"])
    verificacoes = verificacoes[
        (verificacoes["data_hora"] >= recorte["dia"].min())
        & (verificacoes["dia"] <= recorte["dia"].max())
    ]
    if verificacoes.empty:
        st.info("Nenhum prazo com duas verificações seguidas no período.")
    else:
        uteis = np.busday_count(
            verificacoes["anterior"].dt.date.to_numpy(dtype="datetime64[D]"),
            verificacoes["data_hora"].dt.date.to_numpy(dtype="datetime64[D]"),
        )
        colunas = st.columns(3)
        ui.cartao(colunas[0], "Intervalo mediano", f"{np.median(uteis):.0f} dia(s) útil(eis)",
                  "Entre duas verificações seguidas do mesmo prazo.")
        ui.cartao(colunas[1], "Até 2 dias úteis",
                  f"{(uteis <= 2).mean() * 100:.0f}%",
                  "Parcela das reverificações feitas dentro da rotina de dia sim, dia não.")
        ui.cartao(colunas[2], "Mais de 5 dias úteis", int((uteis > 5).sum()),
                  "Reverificações que demoraram mais de uma semana útil.")

    st.markdown("#### O que a controladoria registrou")
    st.dataframe(
        time_.sort_values("data_hora", ascending=False)
        .assign(quando=lambda d: d["data_hora"].dt.strftime("%d/%m/%Y %H:%M"))
        [["quando", "editor", "rotulo", "cliente_autor", "antes", "depois",
          "conteudo_prazo"]]
        .rename(columns={"quando": "Quando", "editor": "Pessoa", "rotulo": "Evento",
                         "cliente_autor": "Autor", "antes": "Antes", "depois": "Depois",
                         "conteudo_prazo": "Conteúdo do prazo"}),
        width="stretch", hide_index=True, height=360,
    )


def _formatar_minutos(minutos) -> str:
    try:
        total = int(round(float(minutos)))
    except (TypeError, ValueError):
        return "—"
    if total <= 0:
        return "—"
    horas, resto = divmod(total, 60)
    return f"{horas}h{resto:02d}" if horas else f"{resto} min"


def _faixa_fatal(dias_uteis) -> str:
    if pd.isna(dias_uteis):
        return "SEM DATA"
    d = int(dias_uteis)
    if d < 0:
        return "ANTECIPADO"
    if d <= 3:
        return "+1 A 3 DIAS ÚTEIS (ajuste)"
    if 8 <= d <= 12:
        return "+8 A 12 DIAS ÚTEIS (5 → 15)"
    return "OUTRA DILAÇÃO"


def _consolidar_sessao(dados: pd.DataFrame, campos_fim: list[str]) -> pd.DataFrame:
    """
    Várias edições seguidas da mesma célula, na mesma sessão de trabalho,
    são uma ocorrência só: vale o valor de antes da primeira e o de depois
    da última. Sem isso, digitar e redigitar contava como várias correções.
    """
    if dados.empty:
        return dados
    ordenado = dados.sort_values("data_hora")
    primeiro = ordenado.groupby(["sessao", "campo"]).head(1).set_index(["sessao", "campo"])
    ultimo = ordenado.groupby(["sessao", "campo"]).tail(1).set_index(["sessao", "campo"])
    for campo in campos_fim:
        primeiro[campo] = ultimo[campo]
    primeiro["edicoes"] = ordenado.groupby(["sessao", "campo"]).size()
    return primeiro.reset_index()


def _retrabalho(recorte: pd.DataFrame) -> None:
    st.caption(
        "Correção de conteúdo é a edição do CONTEÚDO DO PRAZO que já estava "
        f"preenchido. Edição feita até {MINUTOS_AJUSTE} min depois da inclusão da "
        "linha conta como ajuste do próprio lançamento, não como correção. "
        "Alteração de fatal é a troca de uma data fatal por outra data (a saída de "
        "AGUARDA não entra aqui)."
    )

    conteudo = _consolidar_sessao(
        recorte[recorte["evento"] == "correcao_conteudo"], ["depois", "depois_n"]
    )
    if conteudo.empty:
        conteudo = recorte.iloc[0:0].assign(edicoes=0)
    conteudo = conteudo[conteudo["antes_n"] != conteudo["depois_n"]].copy()
    conteudo["tipo_edicao"] = np.where(
        conteudo["minutos_apos_inclusao"].between(0, MINUTOS_AJUSTE),
        "AJUSTE NO LANÇAMENTO", "CORREÇÃO POSTERIOR",
    )
    conteudo["de_quem"] = np.where(
        conteudo["incluido_por"].fillna("") == "", "LINHA ANTIGA (FORA DO LOG)",
        np.where(conteudo["editor"] == conteudo["incluido_por"],
                 "PRÓPRIO LANÇAMENTO", "LANÇAMENTO DE OUTRA PESSOA"),
    )
    correcoes = conteudo[conteudo["tipo_edicao"] == "CORREÇÃO POSTERIOR"]

    fatal = _consolidar_sessao(
        recorte[recorte["evento"] == "alteracao_fatal"], ["depois", "fatal_depois"]
    )
    if fatal.empty:
        fatal = recorte.iloc[0:0].copy()
    fatal = fatal[
        pd.to_datetime(fatal["fatal_antes"]) != pd.to_datetime(fatal["fatal_depois"])
    ].copy()
    if not fatal.empty:
        fatal["dias_uteis_fatal"] = np.busday_count(
            pd.to_datetime(fatal["fatal_antes"]).dt.date.to_numpy(dtype="datetime64[D]"),
            pd.to_datetime(fatal["fatal_depois"]).dt.date.to_numpy(dtype="datetime64[D]"),
        )
        fatal["faixa"] = fatal["dias_uteis_fatal"].map(_faixa_fatal)
    else:
        fatal["faixa"] = pd.Series(dtype=str)

    # Tempo: medido pelas sessões de edição que contêm retrabalho, e
    # estimado por minutos por ocorrência, que a própria equipe informa.
    controles = st.columns(2)
    with controles[0]:
        min_correcao = st.number_input(
            "Minutos estimados por correção de conteúdo", 1, 60, 3, key="ctl_min_corr",
            help="Tempo médio de uma correção, incluindo reler a intimação.",
        )
    with controles[1]:
        min_fatal = st.number_input(
            "Minutos estimados por alteração de fatal", 1, 60, 5, key="ctl_min_fatal",
            help="Tempo médio de uma alteração de fatal, incluindo falar com o advogado "
            "e recalcular o prazo.",
        )

    retrabalho = pd.concat([correcoes, fatal], ignore_index=True)
    medido = (
        retrabalho.drop_duplicates("sessao")["minutos_sessao"].sum()
        if not retrabalho.empty else 0
    )
    estimado = len(correcoes) * min_correcao + len(fatal) * min_fatal
    novos = recorte[recorte["evento"] == "prazo_novo"]

    colunas = st.columns(5)
    ui.cartao(colunas[0], "Correções de conteúdo", len(correcoes),
              f"Fora {int((conteudo['tipo_edicao'] == 'AJUSTE NO LANÇAMENTO').sum())} "
              f"ajuste(s) feitos até {MINUTOS_AJUSTE} min após a inclusão.")
    ui.cartao(
        colunas[1], "Correções por 100 inclusões",
        f"{len(correcoes) / len(novos) * 100:.1f}".replace(".", ",") if len(novos) else "—",
        "Correções posteriores divididas pelas linhas novas do período.",
    )
    ui.cartao(colunas[2], "Alterações de fatal", len(fatal),
              f"{int((fatal['faixa'] == '+8 A 12 DIAS ÚTEIS (5 → 15)').sum())} no "
              "padrão de 5 para 15 dias.")
    ui.cartao(colunas[3], "Tempo medido", _formatar_minutos(medido),
              f"Soma das sessões de edição com retrabalho. Sessão é a sequência de "
              f"edições da mesma pessoa na mesma linha sem pausa maior que "
              f"{MINUTOS_SESSAO} min. Mede só o tempo com a planilha, não a conversa "
              "com o advogado nem a consulta ao processo.")
    ui.cartao(colunas[4], "Tempo estimado", _formatar_minutos(estimado),
              "Ocorrências multiplicadas pelos minutos informados acima.")

    st.markdown("#### Por pessoa")
    pessoas = sorted(set(recorte["editor"]))
    linhas = []
    for pessoa in pessoas:
        corr = correcoes[correcoes["editor"] == pessoa]
        fat = fatal[fatal["editor"] == pessoa]
        incl = novos[novos["editor"] == pessoa]
        proprios = conteudo[
            (conteudo["incluido_por"] == pessoa) & (conteudo["tipo_edicao"] == "CORREÇÃO POSTERIOR")
        ]
        sessoes = pd.concat([corr, fat])
        if corr.empty and fat.empty and incl.empty:
            continue
        linhas.append({
            "pessoa": pessoa,
            "inclusoes": len(incl),
            "correcoes": len(corr),
            "linhas_corrigidas": proprios["chave_origem"].nunique(),
            "taxa": len(proprios) / len(incl) * 100 if len(incl) else pd.NA,
            "fatal": len(fat),
            "medido": sessoes.drop_duplicates("sessao")["minutos_sessao"].sum()
            if not sessoes.empty else 0,
            "estimado": len(corr) * min_correcao + len(fat) * min_fatal,
        })
    tabela = pd.DataFrame(linhas)
    if not tabela.empty:
        total_medido = tabela["medido"].sum()
        total_estimado = tabela["estimado"].sum()
        tabela = ui.adicionar_total(
            tabela, "pessoa", ["inclusoes", "correcoes", "linhas_corrigidas", "fatal"],
            {"taxa": ("linhas_corrigidas", "inclusoes", 100)},
        )
        tabela.loc[tabela.index[-1], ["medido", "estimado"]] = [total_medido, total_estimado]
        tabela["medido"] = tabela["medido"].map(_formatar_minutos)
        tabela["estimado"] = tabela["estimado"].map(_formatar_minutos)
        ui.tabela_compacta(
            tabela,
            {"pessoa": "Pessoa", "inclusoes": "Linhas incluídas",
             "correcoes": "Correções que fez", "linhas_corrigidas": "Suas linhas corrigidas",
             "taxa": "% das suas linhas", "fatal": "Alterações de fatal que fez",
             "medido": "Tempo medido", "estimado": "Tempo estimado"},
            inteiros=["inclusoes", "correcoes", "linhas_corrigidas", "fatal"],
            percentuais=["taxa"],
            total=False,
        )
        st.caption(
            "\"Correções que fez\" conta quem editou. \"Suas linhas corrigidas\" "
            "conta as linhas que a pessoa incluiu e que depois tiveram o conteúdo "
            "corrigido, por ela ou por outra pessoa: é o indicador de erro no lançamento."
        )

    diario = retrabalho.groupby(["dia", "rotulo"]).size().reset_index(name="quantidade") \
        if not retrabalho.empty else pd.DataFrame()
    if not diario.empty:
        figura = px.bar(diario, x="dia", y="quantidade", color="rotulo",
                        color_discrete_map=CORES_EVENTO)
        figura.update_layout(height=300, xaxis_title="", yaxis_title="Ocorrências",
                             legend_title="", legend=dict(orientation="h", y=1.12))
        figura.update_xaxes(tickformat="%d/%m")
        st.plotly_chart(figura, width="stretch", key="ctl_retrabalho_dia")

    st.markdown("#### Alterações de fatal")
    if fatal.empty:
        st.info("Nenhuma troca de data fatal no período.")
    else:
        esquerda, direita = st.columns(2)
        with esquerda:
            faixas = fatal["faixa"].value_counts().reset_index()
            faixas.columns = ["faixa", "quantidade"]
            ui.tabela_compacta(faixas, {"faixa": "Diferença entre o fatal antigo e o novo",
                                        "quantidade": "Alterações"}, inteiros=["quantidade"])
            dias_depois = (fatal["minutos_apos_inclusao"] / 1440).dropna()
            if len(dias_depois):
                st.caption(
                    f"A alteração veio, em mediana, {dias_depois.median():.1f} dia(s) "
                    "depois da inclusão da linha.".replace(".", ",", 1)
                )
        with direita:
            por_adv = (
                fatal.assign(adv=fatal["responsavel_tecnico"].astype(str).str.strip()
                             .replace("", "SEM RESPONSÁVEL"))
                .groupby("adv").agg(
                    alteracoes=("prazo", "count"),
                    padrao_5_15=("faixa", lambda f: int((f == "+8 A 12 DIAS ÚTEIS (5 → 15)").sum())),
                ).reset_index().sort_values("alteracoes", ascending=False)
            )
            ui.tabela_compacta(
                por_adv,
                {"adv": "Responsável técnico da linha", "alteracoes": "Alterações",
                 "padrao_5_15": "De 5 para 15"},
                inteiros=["alteracoes", "padrao_5_15"],
            )
        st.dataframe(
            fatal.sort_values("data_hora", ascending=False).assign(
                quando=lambda d: d["data_hora"].dt.strftime("%d/%m/%Y %H:%M"),
                antes_=lambda d: pd.to_datetime(d["fatal_antes"]).dt.strftime("%d/%m/%Y"),
                depois_=lambda d: pd.to_datetime(d["fatal_depois"]).dt.strftime("%d/%m/%Y"),
            )[["quando", "editor", "autor_ref", "conteudo_prazo", "antes_", "depois_",
               "dias_uteis_fatal", "faixa", "responsavel_tecnico"]]
            .rename(columns={"quando": "Quando", "editor": "Quem alterou",
                             "autor_ref": "Autor", "conteudo_prazo": "Conteúdo",
                             "antes_": "Fatal antigo", "depois_": "Fatal novo",
                             "dias_uteis_fatal": "Dias úteis a mais", "faixa": "Faixa",
                             "responsavel_tecnico": "Responsável técnico"}),
            width="stretch", hide_index=True, height=300,
        )

    st.markdown("#### Correções de conteúdo")
    if correcoes.empty:
        st.info("Nenhuma correção de conteúdo no período.")
    else:
        st.dataframe(
            correcoes.sort_values("data_hora", ascending=False).assign(
                quando=lambda d: d["data_hora"].dt.strftime("%d/%m/%Y %H:%M"),
                depois_de=lambda d: (d["minutos_apos_inclusao"] / 1440).map(
                    lambda v: "—" if pd.isna(v) else f"{v:.1f} dia(s)".replace(".", ",")),
            )[["quando", "editor", "autor_ref", "antes", "depois", "incluido_por",
               "de_quem", "depois_de"]]
            .rename(columns={"quando": "Quando", "editor": "Quem corrigiu",
                             "autor_ref": "Autor", "antes": "Conteúdo antes",
                             "depois": "Conteúdo depois", "incluido_por": "Quem incluiu",
                             "de_quem": "Linha de", "depois_de": "Tempo após inclusão"}),
            width="stretch", hide_index=True, height=320,
        )


def _protocolos(recorte: pd.DataFrame) -> None:
    protocolos = recorte[recorte["evento"] == "protocolo"]
    if protocolos.empty:
        st.info("Nenhum protocolo marcado no período.")
        return

    st.caption(
        "Protocolo é o momento em que o STATUS do prazo passou a PROTOCOLADO. "
        "\"Quem marcou\" é quem editou a célula; \"Responsável\" é o responsável "
        "técnico registrado na linha naquele momento. Os dois costumam coincidir, "
        "e a diferença mostra quando outra pessoa marcou pelo advogado."
    )
    esquerda, direita = st.columns(2)
    with esquerda:
        st.markdown("#### Por responsável técnico")
        por_resp = (
            protocolos.assign(
                responsavel=protocolos["responsavel_tecnico"].astype(str).str.strip()
                .replace("", "SEM RESPONSÁVEL")
            )
            .groupby("responsavel")
            .agg(protocolos=("prazo", "count"), dias=("dia", "nunique"))
            .reset_index()
            .sort_values("protocolos", ascending=False)
        )
        por_resp["media"] = (por_resp["protocolos"] / por_resp["dias"]).map(
            lambda v: f"{v:.1f}".replace(".", ",")
        )
        ui.tabela_compacta(
            por_resp,
            {"responsavel": "Responsável", "protocolos": "Protocolos",
             "dias": "Dias com protocolo", "media": "Média por dia"},
            inteiros=["protocolos", "dias"],
            somar=["protocolos"],
        )
    with direita:
        st.markdown("#### Por quem marcou")
        por_editor = (
            protocolos.groupby(["editor", "grupo"]).size()
            .reset_index(name="protocolos").sort_values("protocolos", ascending=False)
        )
        ui.tabela_compacta(
            por_editor,
            {"editor": "Quem marcou", "grupo": "Grupo", "protocolos": "Protocolos"},
            inteiros=["protocolos"],
        )

    diario = protocolos.groupby(["dia", "responsavel_tecnico"]).size().reset_index(
        name="protocolos"
    )
    figura = px.bar(diario, x="dia", y="protocolos", color="responsavel_tecnico")
    figura.update_layout(height=340, xaxis_title="", yaxis_title="Protocolos",
                         legend_title="Responsável")
    figura.update_xaxes(tickformat="%d/%m")
    st.plotly_chart(figura, width="stretch", key="ctl_protocolos_dia")


def _horarios(recorte: pd.DataFrame) -> None:
    if recorte.empty:
        st.info("Nenhum evento no período.")
        return

    evento = st.segmented_control(
        "Evento", list(TODOS_EVENTOS.values()), default="Verificações",
        key="ctl_hora_evento",
    ) or "Verificações"
    grupo = st.segmented_control(
        "Quem", ["Todos", "CONTROLADORIA", "ADVOGADOS E DEMAIS"], default="Todos",
        key="ctl_hora_grupo",
    ) or "Todos"
    base = recorte[recorte["rotulo"] == evento]
    if grupo != "Todos":
        base = base[base["grupo"] == grupo]
    if base.empty:
        st.info("Nenhum registro para essa combinação.")
        return

    esquerda, direita = st.columns(2)
    with esquerda:
        st.markdown("#### Por hora do dia")
        horas = base.groupby("hora").size().reindex(range(24), fill_value=0)
        horas = horas.loc[horas.index[(horas > 0).argmax():len(horas) - (horas[::-1] > 0).argmax()]]
        figura = px.bar(x=[f"{h:02d}h" for h in horas.index], y=horas.to_numpy(),
                        color_discrete_sequence=[CORES_EVENTO.get(evento, "#1A3762")])
        figura.update_layout(height=320, xaxis_title="", yaxis_title="Registros")
        st.plotly_chart(figura, width="stretch", key="ctl_horas")
    with direita:
        st.markdown("#### Dia da semana × hora")
        mapa = base.pivot_table(index="dia_semana", columns="hora", values="prazo",
                                aggfunc="count", fill_value=0)
        mapa.index = [DIAS[i] for i in mapa.index]
        mapa.columns = [f"{h:02d}h" for h in mapa.columns]
        figura = px.imshow(mapa, color_continuous_scale="Blues", aspect="auto",
                           text_auto=True)
        figura.update_layout(height=320, coloraxis_showscale=False)
        st.plotly_chart(figura, width="stretch", key="ctl_mapa")

    por_pessoa = (
        base.assign(faixa=pd.cut(base["hora"], [-1, 11, 13, 17, 23],
                                 labels=["Manhã (até 12h)", "Almoço (12h-14h)",
                                         "Tarde (14h-18h)", "Após 18h"]))
        .pivot_table(index="editor", columns="faixa", values="prazo",
                     aggfunc="count", fill_value=0, observed=False)
        .reset_index()
    )
    por_pessoa.columns = [str(c) for c in por_pessoa.columns]
    faixas = [c for c in por_pessoa.columns if c != "editor"]
    por_pessoa["total"] = por_pessoa[faixas].sum(axis=1)
    ui.tabela_compacta(
        por_pessoa.sort_values("total", ascending=False),
        {"editor": "Pessoa", **{f: f for f in faixas}, "total": "Total"},
        inteiros=faixas + ["total"],
    )


def _compromissos(recorte: pd.DataFrame, prazos: pd.DataFrame) -> None:
    novos = recorte[recorte["evento"] == "prazo_novo"]
    st.caption(
        "Tipo pelo conteúdo do prazo: AUDIÊNCIA; SESSÃO (sessão, pauta, julgamento "
        "virtual, sustentação oral); PERÍCIA (perícia, perito). Linha que menciona "
        "intimação, publicação, ciência ou notificação é tratada como a linha da "
        "intimação; as demais, como a linha da data do ato."
    )
    grupos = agrupar_compromissos(novos)
    if grupos.empty:
        st.info("Nenhuma audiência, sessão ou perícia incluída no período.")
    else:
        st.markdown("#### Lançamentos do período")
        linhas = novos[novos["compromisso"] != "DEMAIS PRAZOS"]
        resumo = (
            linhas.groupby("compromisso")
            .agg(linhas=("prazo", "count"),
                 intimacao=("papel", lambda s: int((s == "INTIMAÇÃO").sum())),
                 ato=("papel", lambda s: int((s == "DATA DO ATO").sum())))
            .join(grupos.groupby("compromisso").agg(
                distintos=("autor", "count"),
                completos=("situacao", lambda s: int((s == "COMPLETO").sum())),
            ))
            .reindex([t for t, _ in TIPOS_COMPROMISSO]).dropna(how="all")
            .fillna(0).reset_index()
        )
        resumo["pendentes"] = resumo["distintos"] - resumo["completos"]
        resumo["compromisso"] = resumo["compromisso"].map(ROTULO_COMPROMISSO)
        ui.tabela_compacta(
            resumo,
            {"compromisso": "Tipo", "linhas": "Linhas", "intimacao": "Linhas de intimação",
             "ato": "Linhas de data do ato", "distintos": "Compromissos distintos",
             "completos": "Com as duas linhas", "pendentes": "A conferir"},
            inteiros=["linhas", "intimacao", "ato", "distintos", "completos", "pendentes"],
        )

        a_conferir = grupos[grupos["situacao"] != "COMPLETO"]
        st.markdown(f"#### Compromissos a conferir ({len(a_conferir)})")
        st.caption(
            "Compromisso com uma linha só pode estar sem a outra lançada, ou ter a "
            "outra lançada fora do período ou com o nome do autor escrito diferente. "
            "Duas linhas com o mesmo papel indicam que o conteúdo não deixa claro qual "
            "é a intimação."
        )
        if a_conferir.empty:
            st.success("Todos os compromissos do período têm as duas linhas.")
        else:
            st.dataframe(
                a_conferir.sort_values("primeira_inclusao", ascending=False)
                .assign(quando=lambda d: d["primeira_inclusao"].dt.strftime("%d/%m/%Y %H:%M"),
                        tipo=lambda d: d["compromisso"].map(ROTULO_COMPROMISSO))
                [["quando", "autor", "tipo", "situacao", "linhas", "quem", "conteudos"]]
                .rename(columns={"quando": "Incluído em", "autor": "Autor",
                                 "tipo": "Tipo", "situacao": "Situação",
                                 "linhas": "Linhas", "quem": "Quem lançou",
                                 "conteudos": "Conteúdos lançados"}),
                width="stretch", hide_index=True,
            )

    st.markdown("#### Agenda: próximos 30 dias")
    if prazos.empty:
        st.info("Controle de prazos indisponível.")
        return
    agenda = prazos[~prazos["encerrado"]].copy()
    agenda["compromisso"] = agenda["conteudo"].map(tipo_compromisso)
    agenda = agenda[agenda["compromisso"] != "DEMAIS PRAZOS"]
    hoje = pd.Timestamp(date.today())
    agenda = agenda[
        (agenda["data_controle"] >= hoje)
        & (agenda["data_controle"] <= hoje + pd.Timedelta(days=30))
    ].sort_values("data_controle")
    if agenda.empty:
        st.info("Nenhuma audiência, sessão ou perícia em aberto nos próximos 30 dias.")
        return
    colunas = st.columns(3)
    for coluna, (tipo, _) in zip(colunas, TIPOS_COMPROMISSO):
        ui.cartao(coluna, ROTULO_COMPROMISSO[tipo], int((agenda["compromisso"] == tipo).sum()))
    ui.tabela(
        ui.formatar_datas(agenda, ["data_controle"]),
        {
            "data_controle": "Data",
            "compromisso": "Tipo",
            "autor": "Autor",
            "conteudo": "Conteúdo",
            "responsavel": "Responsável",
            "status": "Status",
            "link_bitrix": "Bitrix",
        },
        "Sem compromissos.",
    )
    st.caption(
        "Data é o prazo fatal e, sem ele, a data final. Compromisso com FATAL "
        "em AGUARDA não tem data e não aparece aqui."
    )


# ------------------------------------------------------------- médias


def _medias(recorte: pd.DataFrame, equipe: list, inicio: pd.Timestamp,
            fim: pd.Timestamp) -> None:
    """
    Médias do período escolhido no topo, por dia útil.

    Dia útil sem nenhum registro entra como zero: sem isso, a média só
    olharia os dias em que houve movimento e sairia inflada.
    """
    st.caption(
        "Período do topo da tela. Médias por dia útil (segunda a sexta, sem "
        "descontar feriados); dia útil sem registro conta como zero. Correções e "
        "alterações de fatal já consolidadas por sessão de edição."
    )
    base = pd.concat(
        [recorte[recorte["evento"].isin(list(EVENTOS))],
         _retrabalho_consolidado(recorte)],
        ignore_index=True,
    )
    if base.empty:
        st.info("Nenhum evento no período.")
        return

    fim_util = min(fim, pd.Timestamp(date.today()))
    dias = pd.bdate_range(inicio, fim_util)
    if len(dias) == 0:
        st.info("O período não tem dias úteis.")
        return
    semanas = max(len(dias) / 5, 1)

    diario = (
        base.pivot_table(index="dia", columns="evento", values="data_hora",
                         aggfunc="count", fill_value=0)
        .reindex(index=dias, columns=list(TODOS_EVENTOS), fill_value=0)
    )

    linhas = []
    for codigo, rotulo in TODOS_EVENTOS.items():
        serie = diario[codigo]
        total = int(serie.sum())
        maior_dia = serie.idxmax() if total else None
        da_equipe = base[(base["evento"] == codigo) & base["editor"].isin(equipe)]
        pessoas_ativas = da_equipe.groupby("editor")["dia"].nunique()
        linhas.append({
            "indicador": rotulo,
            "total": total,
            "media_dia": serie.mean(),
            "mediana_dia": serie.median(),
            "media_semana": total / semanas,
            "maior": int(serie.max()),
            "maior_dia": maior_dia.strftime("%d/%m") if maior_dia is not None else "—",
            "dias_zero": int((serie == 0).sum()),
            "por_pessoa_dia": (
                (len(da_equipe) / pessoas_ativas.sum()) if pessoas_ativas.sum() else None
            ),
        })
    tabela = pd.DataFrame(linhas)

    def decimal(valor):
        return "—" if valor is None or pd.isna(valor) else f"{valor:.1f}".replace(".", ",")

    for coluna in ("media_dia", "mediana_dia", "media_semana", "por_pessoa_dia"):
        tabela[coluna] = tabela[coluna].map(decimal)

    st.markdown(f"#### Médias do período · {len(dias)} dia(s) útil(eis)")
    ui.tabela_compacta(
        tabela,
        {
            "indicador": "Indicador",
            "total": "Total",
            "media_dia": "Média por dia útil",
            "mediana_dia": "Mediana por dia útil",
            "media_semana": "Média por semana",
            "maior": "Maior dia",
            "maior_dia": "Data do maior dia",
            "dias_zero": "Dias úteis sem registro",
            "por_pessoa_dia": "Por pessoa da equipe, por dia com registro",
        },
        inteiros=["total", "maior", "dias_zero"],
        total=False,
    )
    st.caption(
        "Mediana: o dia típico, sem a distorção de um dia fora da curva. "
        "\"Por pessoa da equipe\" divide o que a controladoria registrou pelos "
        "dias em que cada pessoa registrou algo, e só considera quem está marcado "
        "como equipe no topo."
    )

    # Média por dia da semana: mostra se segunda, por exemplo, concentra
    # as inclusões do fim de semana.
    st.markdown("#### Média por dia da semana")
    por_semana = diario.copy()
    por_semana["dia_semana"] = por_semana.index.dayofweek
    media_semana = (
        por_semana.groupby("dia_semana")[list(TODOS_EVENTOS)].mean()
        .reindex(range(5)).reset_index()
    )
    media_semana["dia_semana"] = media_semana["dia_semana"].map(lambda d: DIAS[d])
    for codigo in TODOS_EVENTOS:
        media_semana[codigo] = media_semana[codigo].map(decimal)
    ui.tabela_compacta(
        media_semana,
        {"dia_semana": "Dia", **TODOS_EVENTOS},
        total=False,
    )

    # Por pessoa: média nos dias em que a pessoa trabalhou no controle.
    st.markdown("#### Média por pessoa, por dia com registro")
    so_equipe = st.toggle("Só a equipe da controladoria", value=bool(equipe),
                          key="ctl_medias_equipe")
    pessoas = base[base["editor"].isin(equipe)] if (so_equipe and equipe) else base
    if pessoas.empty:
        st.info("Nenhum registro para as pessoas selecionadas.")
        return
    dias_ativos = pessoas.groupby("editor")["dia"].nunique()
    contagem = (
        pessoas.pivot_table(index="editor", columns="evento", values="data_hora",
                            aggfunc="count", fill_value=0)
        .reindex(columns=list(TODOS_EVENTOS), fill_value=0)
    )
    medias = contagem.div(dias_ativos, axis=0)
    medias = medias.map(decimal)
    medias.insert(0, "dias", dias_ativos)
    medias = medias.reset_index()
    ui.tabela_compacta(
        medias,
        {"editor": "Pessoa", "dias": "Dias com registro", **TODOS_EVENTOS},
        inteiros=["dias"],
        total=False,
    )
    st.caption(
        "Dias com registro: dias (inclusive fim de semana) com pelo menos um registro da pessoa no controle de "
        "prazos. Cada média é o total do indicador dividido por esses dias."
    )

    diario_grafico = diario[["prazo_novo", "verificacao", "aguarda_fatal"]].copy()
    diario_grafico = diario_grafico.rolling(5, min_periods=1).mean().reset_index(
        names="dia"
    ).melt(id_vars="dia", var_name="evento", value_name="media")
    diario_grafico["evento"] = diario_grafico["evento"].map(TODOS_EVENTOS)
    figura = px.line(diario_grafico, x="dia", y="media", color="evento",
                     color_discrete_map=CORES_EVENTO)
    figura.update_layout(height=320, xaxis_title="", legend_title="",
                         yaxis_title="Média móvel de 5 dias úteis",
                         legend=dict(orientation="h", y=1.12))
    figura.update_xaxes(tickformat="%d/%m")
    st.plotly_chart(figura, width="stretch", key="ctl_media_movel")


# ------------------------------------------------------------ mensal

MESES_ABREV = ("jan", "fev", "mar", "abr", "mai", "jun", "jul", "ago", "set",
               "out", "nov", "dez")


def _rotulo_mes(periodo) -> str:
    periodo = pd.Period(periodo, "M")
    return f"{MESES_ABREV[periodo.month - 1]}/{periodo.year}"


def _dias_uteis_mes(periodo, hoje: pd.Timestamp, inicio_log=None) -> int:
    """
    Dias úteis do mês cobertos pelo log: no mês corrente, só até hoje; no
    primeiro mês do log, só a partir do primeiro registro. Sem esse corte
    a média por dia útil dos meses incompletos sairia artificialmente baixa.
    """
    periodo = pd.Period(periodo, "M")
    inicio = periodo.start_time.date()
    if inicio_log is not None and pd.Period(inicio_log, "M") == periodo:
        inicio = pd.Timestamp(inicio_log).date()
    fim = min(periodo.end_time.normalize(), hoje).date() + timedelta(days=1)
    return max(int(np.busday_count(inicio, fim)), 1)


def _retrabalho_consolidado(eventos: pd.DataFrame) -> pd.DataFrame:
    """Correções posteriores e alterações de fatal já consolidadas por sessão."""
    conteudo = _consolidar_sessao(
        eventos[eventos["evento"] == "correcao_conteudo"], ["depois", "depois_n"]
    )
    if not conteudo.empty:
        conteudo = conteudo[
            (conteudo["antes_n"] != conteudo["depois_n"])
            & ~conteudo["minutos_apos_inclusao"].between(0, MINUTOS_AJUSTE)
        ]
    fatal = _consolidar_sessao(
        eventos[eventos["evento"] == "alteracao_fatal"], ["depois", "fatal_depois"]
    )
    if not fatal.empty:
        fatal = fatal[
            pd.to_datetime(fatal["fatal_antes"]) != pd.to_datetime(fatal["fatal_depois"])
        ]
    partes = [p for p in (conteudo, fatal) if not p.empty]
    return pd.concat(partes, ignore_index=True) if partes else eventos.iloc[0:0]


def _mensal(eventos: pd.DataFrame, equipe: list) -> None:
    st.caption(
        "Todos os meses cobertos pelo log, independentemente do período escolhido "
        "no topo. As mesmas regras das outras abas: prazo novo é uma linha por dia; "
        "correção e alteração de fatal já consolidadas por sessão de edição."
    )
    hoje = pd.Timestamp(date.today())

    principais = eventos[eventos["evento"].isin(list(EVENTOS))].copy()
    retrab = _retrabalho_consolidado(eventos).copy()
    base = pd.concat([principais, retrab], ignore_index=True)
    if base.empty:
        st.info("Sem eventos no log.")
        return
    base["mes"] = base["data_hora"].dt.to_period("M")

    novos = base[base["evento"] == "prazo_novo"]
    grupos = agrupar_compromissos(novos)
    if not grupos.empty:
        grupos["mes"] = pd.to_datetime(grupos["primeira_inclusao"]).dt.to_period("M")

    meses = sorted(base["mes"].unique())
    inicio_log = eventos["data_hora"].min()
    linhas = []
    for mes in meses:
        doms = base[base["mes"] == mes]
        n = doms[doms["evento"] == "prazo_novo"]
        uteis = _dias_uteis_mes(mes, hoje, inicio_log)
        linhas.append({
            "mes": mes,
            "rotulo": _rotulo_mes(mes)
            + (" *" if pd.Period(inicio_log, "M") == mes and inicio_log.day > 1 else "")
            + (" (em curso)" if mes == pd.Period(hoje, "M") else ""),
            "dias_uteis": uteis,
            "prazo_novo": len(n),
            "comuns": int((n["compromisso"] == "DEMAIS PRAZOS").sum()),
            "compromissos": int((grupos["mes"] == mes).sum()) if not grupos.empty else 0,
            "verificacao": int((doms["evento"] == "verificacao").sum()),
            "aguarda_fatal": int((doms["evento"] == "aguarda_fatal").sum()),
            "protocolo": int((doms["evento"] == "protocolo").sum()),
            "correcao_conteudo": int((doms["evento"] == "correcao_conteudo").sum()),
            "alteracao_fatal": int((doms["evento"] == "alteracao_fatal").sum()),
        })
    tabela = pd.DataFrame(linhas)
    tabela["media_novos"] = tabela["prazo_novo"] / tabela["dias_uteis"]
    tabela["media_verif"] = tabela["verificacao"] / tabela["dias_uteis"]

    # Resumo geral: mês corrente contra o anterior, na média por dia útil,
    # para o mês em curso não parecer queda só por estar incompleto.
    st.markdown("#### Resumo geral")
    if len(tabela) >= 2:
        atual, anterior = tabela.iloc[-1], tabela.iloc[-2]
        colunas = st.columns(5)
        for coluna, (campo, titulo) in zip(colunas, [
            ("prazo_novo", "Prazos novos / dia útil"),
            ("verificacao", "Verificações / dia útil"),
            ("aguarda_fatal", "AGUARDA → fatal / dia útil"),
            ("protocolo", "Protocolos / dia útil"),
            ("correcao_conteudo", "Correções / dia útil"),
        ]):
            agora = atual[campo] / atual["dias_uteis"]
            antes = anterior[campo] / anterior["dias_uteis"]
            coluna.metric(
                f"{titulo} · {_rotulo_mes(atual['mes'])}",
                f"{agora:.1f}".replace(".", ","),
                delta=f"{agora - antes:+.1f} vs {_rotulo_mes(anterior['mes'])}".replace(".", ","),
                delta_color="inverse" if campo == "correcao_conteudo" else "normal",
            )
    total_novos = int(tabela["prazo_novo"].sum())
    st.caption(
        f"No log inteiro: {total_novos} linhas novas, "
        f"{int(tabela['verificacao'].sum())} verificações, "
        f"{int(tabela['protocolo'].sum())} protocolos e "
        f"{int(tabela['correcao_conteudo'].sum())} correções de conteúdo, em "
        f"{len(tabela)} mês(es). Mês com * começa depois do dia 1 no log: os totais "
        "estão incompletos, mas as médias por dia útil contam só os dias cobertos. "
        "O mês em curso vai só até hoje."
    )

    figura = px.bar(
        tabela.melt(id_vars=["rotulo"], value_vars=["prazo_novo", "verificacao",
                                                    "aguarda_fatal", "protocolo"],
                    var_name="evento", value_name="quantidade")
        .assign(evento=lambda d: d["evento"].map(EVENTOS)),
        x="rotulo", y="quantidade", color="evento", barmode="group",
        color_discrete_map=CORES_EVENTO,
    )
    figura.update_layout(height=360, xaxis_title="", yaxis_title="Por mês",
                         legend_title="", legend=dict(orientation="h", y=1.12))
    st.plotly_chart(figura, width="stretch", key="ctl_mensal")

    st.markdown("#### Mês a mês")
    ui.tabela_compacta(
        tabela.sort_values("mes", ascending=False).assign(
            media_novos=lambda d: d["media_novos"].map(lambda v: f"{v:.1f}".replace(".", ",")),
            media_verif=lambda d: d["media_verif"].map(lambda v: f"{v:.1f}".replace(".", ",")),
        ),
        {
            "rotulo": "Mês", "dias_uteis": "Dias úteis",
            "prazo_novo": "Linhas novas", "comuns": "Prazos comuns",
            "compromissos": "Aud./sessões/perícias", "media_novos": "Novos por dia útil",
            "verificacao": "Verificações", "media_verif": "Verif. por dia útil",
            "aguarda_fatal": "AGUARDA → fatal", "protocolo": "Protocolos",
            "correcao_conteudo": "Correções", "alteracao_fatal": "Alt. de fatal",
        },
        inteiros=["dias_uteis", "prazo_novo", "comuns", "compromissos", "verificacao",
                  "aguarda_fatal", "protocolo", "correcao_conteudo", "alteracao_fatal"],
    )

    st.markdown("#### Por responsável, mês a mês")
    opcoes = {**EVENTOS, **RETRABALHO}
    escolha = st.segmented_control(
        "Indicador", list(opcoes.values()), default="Prazos novos", key="ctl_mensal_ind",
    ) or "Prazos novos"
    codigo = next(k for k, v in opcoes.items() if v == escolha)
    medida = st.segmented_control(
        "Mostrar", ["Total no mês", "Média por dia útil"], default="Total no mês",
        key="ctl_mensal_medida",
    ) or "Total no mês"

    # Protocolo é atribuído ao responsável técnico da linha; os demais
    # indicadores, a quem fez a edição.
    recorte = base[base["evento"] == codigo].copy()
    if codigo == "protocolo":
        recorte["pessoa"] = (recorte["responsavel_tecnico"].astype(str).str.strip()
                             .replace("", "SEM RESPONSÁVEL"))
        st.caption("Protocolos por responsável técnico da linha no momento do protocolo.")
    else:
        recorte["pessoa"] = recorte["editor"]
        if equipe:
            so_equipe = st.toggle("Só a equipe da controladoria", value=codigo != "protocolo",
                                  key="ctl_mensal_equipe")
            if so_equipe:
                recorte = recorte[recorte["pessoa"].isin(equipe)]
    if recorte.empty:
        st.info("Nenhum registro desse indicador.")
        return

    matriz = recorte.pivot_table(index="mes", columns="pessoa", values="data_hora",
                                 aggfunc="count", fill_value=0).sort_index(ascending=False)
    pessoas = list(matriz.columns)
    matriz["TOTAL"] = matriz.sum(axis=1)
    if medida == "Média por dia útil":
        inicio_log = eventos["data_hora"].min()
        uteis = pd.Series({m: _dias_uteis_mes(m, hoje, inicio_log) for m in matriz.index})
        media = matriz.div(uteis, axis=0)
        exibicao = media.map(lambda v: "—" if v == 0 else f"{v:.1f}".replace(".", ","))
        exibicao.insert(0, "rotulo", [_rotulo_mes(m) for m in exibicao.index])
        ui.tabela_compacta(
            exibicao.reset_index(drop=True),
            {"rotulo": "Mês", **{p: p for p in pessoas}, "TOTAL": "TOTAL"},
            total=False,
        )
        st.caption("Média = total do mês dividido pelos dias úteis do mês (sem feriados).")
    else:
        matriz.insert(0, "rotulo", [_rotulo_mes(m) for m in matriz.index])
        ui.tabela_compacta(
            matriz.reset_index(drop=True),
            {"rotulo": "Mês", **{p: p for p in pessoas}, "TOTAL": "TOTAL"},
            inteiros=pessoas + ["TOTAL"],
        )

    evolucao = recorte.groupby(["mes", "pessoa"]).size().reset_index(name="quantidade")
    evolucao["mes"] = evolucao["mes"].map(_rotulo_mes)
    figura = px.line(evolucao, x="mes", y="quantidade", color="pessoa", markers=True)
    figura.update_layout(height=320, xaxis_title="", yaxis_title=escolha, legend_title="")
    st.plotly_chart(figura, width="stretch", key="ctl_mensal_pessoa")
