"""
Camada de acesso ao Google Sheets.

Regra da arquitetura: escopo somente leitura. A credencial usada aqui
nao tem permissao de escrita em nenhuma das planilhas, de modo que o
painel nao consegue alterar dado operacional nem por engano.
"""

from __future__ import annotations

from datetime import datetime

import gspread
import streamlit as st
from google.oauth2.service_account import Credentials

ESCOPOS = [
    "https://www.googleapis.com/auth/spreadsheets.readonly",
    "https://www.googleapis.com/auth/drive.readonly",
]


def _ttl_cache() -> int:
    """
    TTL do cache, em segundos, lido dos Secrets.

    A leitura acontece no momento em que o modulo e importado, porque o
    decorador st.cache_data exige o TTL fixo. Se os Secrets ainda nao
    estiverem disponiveis, cai no padrao de 120 segundos.
    """
    try:
        return int(st.secrets.get("CACHE_TTL_SEGUNDOS", 120))
    except Exception:  # noqa: BLE001 - secrets indisponivel na importacao
        return 120


TTL_CACHE = _ttl_cache()


@st.cache_resource(show_spinner=False)
def _cliente_gspread():
    credenciais = Credentials.from_service_account_info(
        dict(st.secrets["gcp_service_account"]),
        scopes=ESCOPOS,
    )
    return gspread.authorize(credenciais)


@st.cache_data(ttl=TTL_CACHE, show_spinner=False)
def ler_aba(id_planilha: str, nome_aba: str) -> list[list[str]]:
    """
    Devolve a matriz bruta da aba, com a primeira linha de cabecalhos.

    Retorna valores formatados (get_all_values), nao valores brutos.
    Isso e proposital: as datas chegam em dd/mm/aaaa e os valores em
    formato brasileiro, exatamente como a equipe ve na planilha, e o
    modulo de normalizacao converte a partir dai.
    """
    cliente = _cliente_gspread()
    try:
        planilha = cliente.open_by_key(id_planilha)
    except gspread.exceptions.APIError as erro:
        raise RuntimeError(
            f"Falha ao abrir a planilha {id_planilha}. "
            "Verifique se ela foi compartilhada com o e-mail da conta de servico. "
            f"Detalhe: {erro}"
        ) from erro

    try:
        aba = planilha.worksheet(nome_aba)
    except gspread.exceptions.WorksheetNotFound as erro:
        raise RuntimeError(
            f"A aba '{nome_aba}' nao existe na planilha {id_planilha}."
        ) from erro

    return aba.get_all_values()


@st.cache_data(ttl=TTL_CACHE, show_spinner=False)
def ler_aba_recente(id_planilha: str, nome_aba: str, limite: int) -> list[list[str]]:
    """
    Le o cabecalho e apenas as ultimas `limite` linhas da aba.

    Existe para o LOG_ALTERACOES, que cresce sem teto. Ler cem mil
    linhas a cada consulta e o que deixa o painel lento, e o historico
    antigo raramente e consultado na tela. Quando for preciso o log
    inteiro, use ler_aba.
    """
    cliente = _cliente_gspread()
    try:
        aba = cliente.open_by_key(id_planilha).worksheet(nome_aba)
    except gspread.exceptions.WorksheetNotFound as erro:
        raise RuntimeError(
            f"A aba '{nome_aba}' nao existe na planilha {id_planilha}."
        ) from erro
    except gspread.exceptions.APIError as erro:
        raise RuntimeError(
            f"Falha ao abrir a planilha {id_planilha}. "
            "Verifique o compartilhamento com a conta de servico. "
            f"Detalhe: {erro}"
        ) from erro

    # Uma unica coluna e barata de ler e da a ultima linha real.
    ultima = len(aba.col_values(1))
    if ultima <= 1:
        return aba.get_all_values()

    if ultima - 1 <= limite:
        return aba.get_all_values()

    cabecalho = aba.get("1:1")
    largura = len(cabecalho[0]) if cabecalho else 26
    fim_coluna = chr(ord("A") + largura - 1) if largura <= 26 else "AZ"
    inicio = ultima - limite + 1
    corpo = aba.get(f"A{inicio}:{fim_coluna}{ultima}")
    return (cabecalho or [[]]) + corpo


def limite_log() -> int:
    try:
        return int(st.secrets.get("MAX_LINHAS_LOG", 30000))
    except Exception:  # noqa: BLE001
        return 30000


def limpar_cache() -> None:
    """Forca releitura na proxima consulta."""
    ler_aba.clear()
    ler_aba_recente.clear()
    st.session_state["ultima_leitura"] = None


def marcar_leitura() -> None:
    st.session_state["ultima_leitura"] = datetime.now()


def rotulo_ultima_leitura() -> str:
    momento = st.session_state.get("ultima_leitura")
    if not momento:
        return "sem leitura nesta sessão"
    return momento.strftime("%d/%m/%Y %H:%M:%S")


def id_planilha_auxiliar() -> str:
    """
    A unica planilha que o painel enxerga.

    A planilha principal NAO e compartilhada com a conta de servico e
    nao e lida por aqui. O painel le apenas os espelhos e o log que
    vivem na auxiliar.
    """
    return st.secrets["ID_PLANILHA_AUXILIAR"]


def id_planilha_logs() -> str:
    """
    Onde vive o LOG_ALTERACOES.

    Por padrao e a propria auxiliar, que e o desenho mais simples. Se
    um dia o log crescer a ponto de disputar espaco com os espelhos,
    basta criar uma terceira planilha so de log, gravar o ID em
    ID_PLANILHA_LOGS e repontar o LOG_SPREADSHEET_ID no Apps Script.
    Nada mais muda.
    """
    return st.secrets.get("ID_PLANILHA_LOGS") or id_planilha_auxiliar()
