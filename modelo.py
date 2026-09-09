"""
Modelo de dados do painel.

Cada funcao le uma aba e devolve um DataFrame ja derivado. O mapeamento
de colunas reproduz obterContextoLinha_ do Apps Script na versao V12.3,
inclusive os aliases de compatibilidade (FATAL / PRAZO FATAL) e o
tratamento das duas colunas RESPONSAVEL do CONTROLE DE PRAZOS.
"""

from __future__ import annotations

from datetime import datetime

import pandas as pd
import streamlit as st

from db import conexao
from db.normalizacao import (
    classificar_resultado_sentenca,
    classificar_tipo_prazo,
    converter_data,
    converter_numero,
    converter_percentual,
    diferenca_dias,
    inicio_do_dia,
    mapa_cabecalhos,
    normalizar_texto,
    primeiro_preenchido,
    valor_por_cabecalho,
    valor_por_cabecalho_parcial,
    valor_preenchido,
)

# Abas da planilha auxiliar. BI_PRAZOS e BI_CLIENTES sao espelhos
# criados por IMPORTRANGE e mantem os mesmos cabecalhos da origem, de
# modo que o mapeamento de colunas abaixo continua valendo.
ABA_PRAZOS = "BI_PRAZOS"
ABA_CLIENTES = "BI_CLIENTES"
ABA_LOG = "LOG_ALTERACOES"
ABA_BASE_IDS = "BI_BASE_IDS"
ABA_CONTROLE = "CONTROLE_BI"

STATUS_ENCERRADOS_PRAZOS = {"PROTOCOLADO", "CONCLUIDO", "OK"}
STATUS_ENCERRADOS_CLIENTES = {"PROTOCOLADO", "DESCARTADO", "CONCLUIDO"}


# --------------------------------------------------------------- prazos


def _situacao_prazo(prazo_fatal, encerrado: bool, hoje) -> tuple[str, int | None]:
    dias = diferenca_dias(hoje, prazo_fatal)
    if dias is None:
        return "SEM PRAZO FATAL", None
    if encerrado:
        return "ENCERRADO", dias
    if dias < 0:
        return "VENCIDO", dias
    if dias == 0:
        return "VENCE HOJE", dias
    if dias <= 7:
        return "VENCE EM 7 DIAS", dias
    return "NO PRAZO", dias


@st.cache_data(ttl=conexao.TTL_CACHE, show_spinner="Lendo o controle de prazos...")
def carregar_prazos() -> pd.DataFrame:
    matriz = conexao.ler_aba(conexao.id_planilha_auxiliar(), ABA_PRAZOS)
    if len(matriz) < 2:
        return pd.DataFrame()

    mapa = mapa_cabecalhos(matriz[0])
    hoje = inicio_do_dia(datetime.now())
    registros = []

    for indice, linha in enumerate(matriz[1:], start=2):
        autor = valor_por_cabecalho(linha, mapa, ["AUTOR", "CLIENTE"])
        data_evento = valor_por_cabecalho(linha, mapa, ["DATA EVENTO"])
        data_final = valor_por_cabecalho(linha, mapa, ["DATA FINAL"])
        # V12.3: a estrutura atual usa FATAL. Os demais sao compatibilidade.
        prazo_fatal = valor_por_cabecalho(
            linha, mapa, ["FATAL", "PRAZO FATAL", "DATA FATAL"]
        )
        observacao = valor_por_cabecalho(linha, mapa, ["OBSERVACAO"])
        conteudo = primeiro_preenchido(
            [valor_por_cabecalho(linha, mapa, ["CONTEUDO DO PRAZO"]), observacao]
        )
        status = valor_por_cabecalho(linha, mapa, ["STATUS"])

        if not any(
            valor_preenchido(v)
            for v in (autor, data_evento, data_final, prazo_fatal, conteudo, status)
        ):
            continue

        # Primeira ocorrencia de RESPONSAVEL e o tecnico, a ultima e a
        # controladoria. Mesma regra do obterContextoLinha_.
        responsavel = valor_por_cabecalho(linha, mapa, ["RESPONSAVEL"], ocorrencia=0)
        indices_resp = mapa.get(normalizar_texto("RESPONSAVEL"), [])
        responsavel_controladoria = valor_por_cabecalho(
            linha, mapa, ["RESPONSAVEL CONTROLADORIA", "RESPONSAVEL DA CONTROLADORIA"]
        )
        if not valor_preenchido(responsavel_controladoria) and len(indices_resp) > 1:
            posicao = indices_resp[-1]
            responsavel_controladoria = linha[posicao] if posicao < len(linha) else ""

        encerrado = normalizar_texto(status) in STATUS_ENCERRADOS_PRAZOS
        situacao, dias_para_fatal = _situacao_prazo(prazo_fatal, encerrado, hoje)
        data_referencia = converter_data(
            primeiro_preenchido([data_evento, prazo_fatal, data_final])
        )

        registros.append(
            {
                "linha_origem": indice,
                "autor": str(autor).strip(),
                "link_bitrix": valor_por_cabecalho(linha, mapa, ["LINK BITRIX"]),
                "data_evento": converter_data(data_evento),
                "prazo_texto": valor_por_cabecalho(linha, mapa, ["PRAZO"]),
                "data_final": converter_data(data_final),
                "prazo_fatal": converter_data(prazo_fatal),
                "conteudo": str(conteudo).strip(),
                "observacao": str(observacao).strip(),
                "responsavel": str(responsavel).strip() or "SEM RESPONSÁVEL",
                "delegado": str(valor_por_cabecalho(linha, mapa, ["DELEGADO"])).strip(),
                "responsavel_controladoria": str(responsavel_controladoria).strip(),
                "status": str(status).strip().upper() or "SEM STATUS",
                "verificacao_controladoria": valor_por_cabecalho(
                    linha, mapa, ["VERIFICACAO CONTROLADORIA"]
                ),
                "tipo_prazo": classificar_tipo_prazo(conteudo),
                "situacao": situacao,
                "dias_para_fatal": dias_para_fatal,
                "encerrado": encerrado,
                "data_referencia": data_referencia,
            }
        )

    dados = pd.DataFrame(registros)
    if dados.empty:
        return dados

    dados["ano"] = dados["data_referencia"].dt.year
    dados["mes"] = dados["data_referencia"].dt.month
    dados["ano_fatal"] = dados["prazo_fatal"].dt.year
    dados["mes_fatal"] = dados["prazo_fatal"].dt.month
    return dados


# -------------------------------------------------------------- clientes


@st.cache_data(ttl=conexao.TTL_CACHE, show_spinner="Lendo o controle de clientes...")
def carregar_clientes() -> pd.DataFrame:
    matriz = conexao.ler_aba(conexao.id_planilha_auxiliar(), ABA_CLIENTES)
    if len(matriz) < 2:
        return pd.DataFrame()

    mapa = mapa_cabecalhos(matriz[0])
    registros = []

    for indice, linha in enumerate(matriz[1:], start=2):
        cliente = valor_por_cabecalho(linha, mapa, ["CLIENTE", "AUTOR"])
        status = valor_por_cabecalho(linha, mapa, ["STATUS"])
        servico = valor_por_cabecalho(linha, mapa, ["SERVICO"])
        data_contrato = valor_por_cabecalho(linha, mapa, ["DATA CONTRATO"])
        data_ajuizamento = valor_por_cabecalho(linha, mapa, ["DATA AJUIZAMENTO"])

        if not any(
            valor_preenchido(v)
            for v in (cliente, status, servico, data_contrato, data_ajuizamento)
        ):
            continue

        honorario_previsto = converter_numero(
            valor_por_cabecalho(linha, mapa, ["HONORARIO PREVISTO"])
        )
        honorario_sucumbencial = converter_numero(
            valor_por_cabecalho(linha, mapa, ["HONORARIO SUCUMBENCIAL PREVISTO"])
        )
        valor_ajuizado = converter_numero(
            valor_por_cabecalho(linha, mapa, ["VALOR AJUIZADO CONSOLIDADO"])
        )
        calculo_real = primeiro_preenchido(
            [
                valor_por_cabecalho_parcial(linha, mapa, "RT CONFIRMADA CONTABILISTA"),
                valor_por_cabecalho_parcial(linha, mapa, "CALCULO REAL"),
            ]
        )
        sentenca = valor_por_cabecalho(linha, mapa, ["SENTENCA PROCEDENTE"])
        data_referencia = converter_data(
            primeiro_preenchido([data_ajuizamento, data_contrato])
        )

        registros.append(
            {
                "linha_origem": indice,
                "cliente": str(cliente).strip(),
                "status": str(status).strip().upper() or "SEM STATUS",
                "servico": str(servico).strip().upper() or "NÃO INFORMADO",
                "responsavel": str(
                    valor_por_cabecalho(linha, mapa, ["RESPONSAVEL"])
                ).strip()
                or "SEM RESPONSÁVEL",
                "link_bitrix": valor_por_cabecalho(linha, mapa, ["LINK BITRIX"]),
                "data_contrato": converter_data(data_contrato),
                "data_ajuizamento": converter_data(data_ajuizamento),
                "calculo_real": calculo_real,
                "percentual_honorarios": converter_percentual(
                    valor_por_cabecalho(
                        linha, mapa, ["% HONORARIOS", "PERCENTUAL HONORARIOS"]
                    )
                ),
                "numero_parcelas": converter_numero(
                    valor_por_cabecalho(
                        linha, mapa, ["N DE PARCELAS", "NUMERO DE PARCELAS"]
                    )
                ),
                "valor_parcela_irrf": converter_numero(
                    valor_por_cabecalho(
                        linha, mapa, ["VALOR PARCELA IRRF", "VALOR PARCELA"]
                    )
                ),
                "honorario_previsto": honorario_previsto,
                "honorario_sucumbencial": honorario_sucumbencial,
                "honorario_total": honorario_previsto + honorario_sucumbencial,
                "valor_ajuizado": valor_ajuizado,
                "tribunal": valor_por_cabecalho(linha, mapa, ["TRIBUNAL AJUIZAMENTO"]),
                "liminar": valor_por_cabecalho_parcial(linha, mapa, "LIMINAR CONCEDIDA"),
                "data_primeiro_faturamento": converter_data(
                    valor_por_cabecalho(
                        linha, mapa, ["DATA 1 FATURAMENTO", "DATA PRIMEIRO FATURAMENTO"]
                    )
                ),
                "sentenca_procedente": sentenca,
                "resultado_sentenca": classificar_resultado_sentenca(sentenca),
                "data_sentenca": converter_data(
                    valor_por_cabecalho(linha, mapa, ["DATA SENTENCA"])
                ),
                "data_transito": converter_data(
                    valor_por_cabecalho_parcial(linha, mapa, "DATA TJ PROC CONHECIMENTO")
                ),
                "data_faturamento_restituicao": converter_data(
                    valor_por_cabecalho_parcial(
                        linha, mapa, "DATA FATURAMENTO RESTITUICAO"
                    )
                ),
                "data_conclusao_execucao": converter_data(
                    valor_por_cabecalho_parcial(linha, mapa, "DATA CONCLUSAO EXECUCAO")
                ),
                "diagnostico": valor_por_cabecalho(linha, mapa, ["DIAGNOSTICO"]),
                "comentario": valor_por_cabecalho(linha, mapa, ["COMENTARIO"]),
                "encerrado": normalizar_texto(status) in STATUS_ENCERRADOS_CLIENTES,
                "ajuizado": pd.notna(converter_data(data_ajuizamento)),
                "possui_calculo": valor_preenchido(calculo_real) or valor_ajuizado != 0,
                "data_referencia": data_referencia,
            }
        )

    dados = pd.DataFrame(registros)
    if dados.empty:
        return dados

    dados["ano"] = dados["data_referencia"].dt.year
    dados["mes"] = dados["data_referencia"].dt.month
    dados["ano_ajuizamento"] = dados["data_ajuizamento"].dt.year
    dados["mes_ajuizamento"] = dados["data_ajuizamento"].dt.month
    return dados


# ------------------------------------------------------------------ log

COLUNAS_LOG = {
    "ID": "id",
    "DATA_HORA": "data_hora",
    "DATA": "data",
    "HORA": "hora",
    "ABA_ORIGEM": "aba_origem",
    "LINHA": "linha",
    "COLUNA": "coluna",
    "CABECALHO": "cabecalho",
    "VALOR_ANTERIOR": "valor_anterior",
    "VALOR_NOVO": "valor_novo",
    "TIPO_EVENTO": "tipo_evento",
    "CLIENTE_AUTOR": "cliente_autor",
    "LINK_BITRIX": "link_bitrix",
    "RESPONSAVEL_TECNICO": "responsavel_tecnico",
    "RESPONSAVEL_CONTROLADORIA": "responsavel_controladoria",
    "STATUS_ATUAL": "status_atual",
    "DATA_EVENTO": "data_evento",
    "DATA_FINAL": "data_final",
    "PRAZO_FATAL": "prazo_fatal",
    "CONTEUDO_PRAZO_SERVICO": "conteudo",
    "EDITOR_EMAIL": "editor_email",
    "CHAVE_EDITOR": "chave_editor",
    "OBSERVACAO": "observacao",
}


@st.cache_data(ttl=conexao.TTL_CACHE, show_spinner="Lendo o histórico de alterações...")
def carregar_logs() -> pd.DataFrame:
    matriz = conexao.ler_aba_recente(
        conexao.id_planilha_logs(), ABA_LOG, conexao.limite_log()
    )
    if len(matriz) < 2:
        return pd.DataFrame()

    mapa = mapa_cabecalhos(matriz[0])
    registros = []

    for linha in matriz[1:]:
        registro = {}
        for cabecalho, campo in COLUNAS_LOG.items():
            registro[campo] = valor_por_cabecalho(linha, mapa, [cabecalho])
        if not valor_preenchido(registro.get("data_hora")) and not valor_preenchido(
            registro.get("aba_origem")
        ):
            continue
        registros.append(registro)

    dados = pd.DataFrame(registros)
    if dados.empty:
        return dados

    dados["data_hora"] = dados["data_hora"].apply(converter_data)
    dados["linha"] = pd.to_numeric(dados["linha"], errors="coerce")
    dados = dados.sort_values("data_hora", ascending=False, na_position="last")
    dados["chave_origem"] = (
        dados["aba_origem"].astype(str).str.strip()
        + "|"
        + dados["linha"].fillna(0).astype(int).astype(str)
    )
    return dados.reset_index(drop=True)


@st.cache_data(ttl=conexao.TTL_CACHE, show_spinner=False)
def carregar_base_ids() -> pd.DataFrame:
    """
    Le a BASE IDS espelhada, se ela existir.

    E opcional: a BASE IDS continua vivendo na planilha principal e so
    aparece no painel se voce criar um espelho BI_BASE_IDS na auxiliar.
    A ausencia nao e tratada como erro.
    """
    try:
        matriz = conexao.ler_aba(conexao.id_planilha_auxiliar(), ABA_BASE_IDS)
    except RuntimeError:
        return pd.DataFrame()

    if len(matriz) < 2:
        return pd.DataFrame()

    colunas = [normalizar_texto(c).replace(" ", "_").lower() for c in matriz[0]]
    dados = pd.DataFrame(matriz[1:], columns=colunas)
    return dados[dados.iloc[:, 0].astype(str).str.strip() != ""].reset_index(drop=True)


# ------------------------------------------------- estado do espelho


@st.cache_data(ttl=60, show_spinner=False)
def estado_do_espelho() -> dict:
    """
    Le a aba CONTROLE_BI, escrita pelo Apps Script da auxiliar a cada
    atualizacao forcada do IMPORTRANGE.

    Serve para o painel mostrar a idade real do dado. Sem isso, o
    espelho poderia estar defasado e o usuario nao teria como saber.
    """
    try:
        matriz = conexao.ler_aba(conexao.id_planilha_auxiliar(), ABA_CONTROLE)
    except RuntimeError:
        return {}

    estado = {}
    for linha in matriz[1:]:
        if len(linha) >= 2 and str(linha[0]).strip():
            estado[str(linha[0]).strip()] = str(linha[1]).strip()
    return estado


def minutos_desde_atualizacao(estado: dict) -> float | None:
    """Idade do espelho em minutos, ou None se nao houver registro."""
    momento = converter_data(estado.get("ATUALIZADO_EM"))
    if pd.isna(momento):
        return None
    return (datetime.now() - momento).total_seconds() / 60.0
