"""
Controladoria.

Mede o trabalho feito no CONTROLE DE PRAZOS a partir do log de
alterações: prazos novos incluídos, verificações da controladoria,
troca de AGUARDA por data fatal e protocolos marcados pelos advogados.

Cada evento é reconhecido pela mudança de valor na célula, e não pelo
rótulo TIPO_EVENTO, porque a mudança de célula está registrada em
todas as versões do Apps Script:

    PRAZO NOVO      primeira vez que a linha recebe AUTOR, CONTEÚDO DO
                    PRAZO ou DATA EVENTO (célula vazia -> preenchida),
                    ou evento LANCAMENTO_PRAZO. Conta uma vez por prazo.
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

from db.normalizacao import converter_data, normalizar_texto
from paginas import componentes as ui

CAMPOS_ABERTURA = {"AUTOR", "CONTEUDO DO PRAZO", "DATA EVENTO", "CLIENTE"}
CAMPOS_FATAL = {"FATAL", "PRAZO FATAL", "DATA FATAL"}
CAMPO_VERIFICACAO = "VERIFICACAO CONTROLADORIA"

EVENTOS = {
    "prazo_novo": "Prazos novos",
    "verificacao": "Verificações",
    "aguarda_fatal": "AGUARDA → fatal",
    "protocolo": "Protocolos",
}
CORES_EVENTO = {
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

DIAS = ("Segunda", "Terça", "Quarta", "Quinta", "Sexta", "Sábado", "Domingo")
PADRAO_CONTROLADORIA = ("MARIANA", "CAROL")


def tipo_compromisso(texto) -> str:
    normal = normalizar_texto(texto)
    for rotulo, termos in TIPOS_COMPROMISSO:
        if any(t in normal for t in termos):
            return rotulo
    return "DEMAIS PRAZOS"


# ------------------------------------------------------------ eventos


def extrair_eventos(logs: pd.DataFrame) -> pd.DataFrame:
    """Uma linha por evento reconhecido, com quem, quando e qual prazo."""
    if logs.empty:
        return pd.DataFrame()

    base = logs[
        logs["aba_origem"].astype(str).map(normalizar_texto).str.contains("PRAZO", na=False)
    ].copy()
    if base.empty:
        return pd.DataFrame()

    base["data_hora"] = pd.to_datetime(base["data_hora"], errors="coerce")
    base = base.dropna(subset=["data_hora"])
    base["campo"] = base["cabecalho"].astype(str).map(normalizar_texto)
    base["antes"] = base["valor_anterior"].astype(str).str.strip()
    base["depois"] = base["valor_novo"].astype(str).str.strip()
    base["antes_n"] = base["antes"].map(normalizar_texto)
    base["depois_n"] = base["depois"].map(normalizar_texto)

    # Identidade do prazo: ID permanente quando o Apps Script grava, e
    # na falta dele a linha somada ao nome do autor no momento da edição.
    # O nome evita somar dois prazos diferentes que ocuparam a mesma
    # linha em épocas distintas.
    id_prazo = base.get("id_prazo", pd.Series("", index=base.index)).astype(str).str.strip()
    base["prazo"] = np.where(
        id_prazo != "",
        "ID:" + id_prazo,
        base["chave_origem"].astype(str) + "|" + base["cliente_autor"].map(normalizar_texto),
    )

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
    data_nova = base["depois"].map(lambda v: pd.notna(converter_data(v)))
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

    partes = []
    novos = base[abertura].sort_values("data_hora").drop_duplicates("prazo")
    partes.append(novos.assign(evento="prazo_novo"))
    partes.append(base[verificacao].assign(evento="verificacao"))
    partes.append(base[aguarda].assign(evento="aguarda_fatal"))
    # Mesmo prazo protocolado duas vezes no mesmo dia (status desfeito e
    # refeito) conta uma vez.
    protocolos = base[protocolo].assign(dia=base["data_hora"].dt.date)
    partes.append(
        protocolos.drop_duplicates(["prazo", "dia"]).drop(columns="dia")
        .assign(evento="protocolo")
    )

    eventos = pd.concat(partes, ignore_index=True)
    if eventos.empty:
        return eventos

    # Conteúdo do prazo: o último texto não vazio registrado no log para
    # aquele prazo, porque na hora da inclusão o conteúdo pode ainda
    # não ter sido digitado.
    conteudo = (
        base[base["conteudo"].astype(str).str.strip() != ""]
        .sort_values("data_hora")
        .groupby("prazo")["conteudo"].last()
    )
    eventos["conteudo_prazo"] = eventos["prazo"].map(conteudo).fillna(eventos["conteudo"])
    eventos["compromisso"] = eventos["conteudo_prazo"].map(tipo_compromisso)
    eventos["rotulo"] = eventos["evento"].map(EVENTOS)
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

    abas = st.tabs(["Por dia", "Controladoria", "Protocolos dos advogados",
                    "Horários", "Audiências, sessões e perícias"])
    with abas[0]:
        _por_dia(recorte)
    with abas[1]:
        _controladoria(recorte, equipe, eventos)
    with abas[2]:
        _protocolos(recorte)
    with abas[3]:
        _horarios(recorte)
    with abas[4]:
        _compromissos(recorte, prazos)


def _cartoes(recorte: pd.DataFrame, dias_uteis: int) -> None:
    colunas = st.columns(4)
    for coluna, (codigo, rotulo) in zip(colunas, EVENTOS.items()):
        total = int((recorte["evento"] == codigo).sum())
        ui.cartao(
            coluna, rotulo, f"{total}",
            f"Média de {total / dias_uteis:.1f} por dia útil no período "
            f"({dias_uteis} dias úteis).".replace(".", ",", 1),
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
        recorte.groupby(["dia", "rotulo"]).size().reset_index(name="quantidade")
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
    tabela["dia_semana"] = tabela["dia"].dt.dayofweek.map(lambda d: DIAS[d])
    tabela["dia"] = tabela["dia"].dt.strftime("%d/%m/%Y")
    ui.tabela_compacta(
        tabela,
        {"dia": "Dia", "dia_semana": "", **EVENTOS},
        inteiros=list(EVENTOS),
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
        "Evento", list(EVENTOS.values()), default="Verificações", key="ctl_hora_evento",
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
    st.markdown("#### Incluídos no período, por tipo")
    st.caption(
        "Tipo identificado pelo conteúdo do prazo: AUDIÊNCIA; SESSÃO (sessão, "
        "pauta, julgamento virtual, sustentação oral); PERÍCIA (perícia, perito). "
        "O restante entra em DEMAIS PRAZOS."
    )
    if novos.empty:
        st.info("Nenhum prazo novo no período.")
    else:
        contagem = (
            novos.pivot_table(index="dia", columns="compromisso", values="prazo",
                              aggfunc="count", fill_value=0)
            .sort_index(ascending=False).reset_index()
        )
        contagem["dia"] = contagem["dia"].dt.strftime("%d/%m/%Y")
        tipos = [c for c in ["AUDIÊNCIA", "SESSÃO DE JULGAMENTO", "PERÍCIA",
                             "DEMAIS PRAZOS"] if c in contagem.columns]
        ui.tabela_compacta(
            contagem, {"dia": "Dia", **{t: ROTULO_COMPROMISSO[t] for t in tipos}},
            inteiros=tipos,
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
