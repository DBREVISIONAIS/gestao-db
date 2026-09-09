"""
Gestão DB — painel de consulta (versão de arquivo único).

Este arquivo reúne todos os módulos do projeto num só, para que a
publicação no Streamlit Cloud não dependa de subir pastas para o
repositório. O conteúdo é idêntico ao da versão modular; apenas as
funções de tela ganharam sufixo para evitar colisão de nomes.

Arquitetura:
    PLANILHA PRINCIPAL  -> onde a equipe trabalha. Não é compartilhada
                           com a conta de serviço e não é lida daqui.
    PLANILHA AUXILIAR   -> BI_PRAZOS e BI_CLIENTES por IMPORTRANGE,
                           mais LOG_ALTERACOES gravado pelo Apps Script.
    STREAMLIT           -> lê somente a auxiliar, calcula em memória.
"""

from __future__ import annotations

from datetime import date, datetime
from datetime import datetime
from google.oauth2.service_account import Credentials
import gspread
import hashlib
import hmac
import pandas as pd
import plotly.express as px
import re
import streamlit as st
import unicodedata

# Os módulos originais chamavam uns aos outros por prefixo (ui.tabela,
# auth.aplicar_recorte, conexao.ler_aba, modelo.carregar_prazos). Como
# agora tudo vive no mesmo módulo, os prefixos apontam para ele mesmo.
# Isso preserva o código original sem reescrever cada chamada.
import sys as _sys

_ESTE_MODULO = _sys.modules[__name__]
ui = auth = conexao = modelo = _ESTE_MODULO


# ==================================================================
# NORMALIZACAO
# ==================================================================
"""
Normalizacao de texto, numeros e datas.

Estas funcoes espelham deliberadamente o comportamento do Apps Script
(normalizarTexto_, converterNumero_, converterPercentual_) para que o
Streamlit produza exatamente os mesmos agrupamentos que o dashboard
atual do Google Sheets. Nao alterar sem alterar o .gs correspondente.
"""




# ---------------------------------------------------------------- texto


def normalizar_texto(valor) -> str:
    """Maiuscula, sem acento, sem pontuacao. Espelha normalizarTexto_."""
    if valor is None:
        return ""
    texto = str(valor).strip().upper()
    texto = unicodedata.normalize("NFD", texto)
    texto = "".join(c for c in texto if unicodedata.category(c) != "Mn")
    texto = re.sub(r"[^A-Z0-9]+", " ", texto)
    return texto.strip()


def valor_preenchido(valor) -> bool:
    if valor is None:
        return False
    if isinstance(valor, float) and pd.isna(valor):
        return False
    return str(valor).strip() != ""


def primeiro_preenchido(valores):
    for valor in valores:
        if valor_preenchido(valor):
            return valor
    return ""


# --------------------------------------------------------------- numeros


def converter_numero(valor) -> float:
    """Le '1.234,56', 'R$ 1.234,56', '1234.56' e numeros nativos."""
    if isinstance(valor, (int, float)) and not isinstance(valor, bool):
        if pd.isna(valor):
            return 0.0
        return float(valor)
    if not valor_preenchido(valor):
        return 0.0

    texto = re.sub(r"[R$\s]", "", str(valor).strip())
    if "," in texto:
        texto = texto.replace(".", "").replace(",", ".")
    else:
        texto = re.sub(r"[^0-9.\-]", "", texto)

    try:
        numero = float(texto)
    except ValueError:
        return 0.0
    return numero if pd.notna(numero) else 0.0


def converter_percentual(valor) -> float:
    """Devolve sempre fracao. 30, '30%' e 0,30 viram 0.30."""
    if not valor_preenchido(valor):
        return 0.0
    texto = str(valor)
    numero = converter_numero(valor)
    if "%" in texto or numero > 1:
        return numero / 100.0
    return numero


# ----------------------------------------------------------------- datas

_FORMATOS_DATA = (
    "%d/%m/%Y %H:%M:%S",
    "%d/%m/%Y %H:%M",
    "%d/%m/%Y",
    "%d/%m/%y",
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d",
)


def converter_data(valor):
    """Devolve datetime ou pd.NaT. Aceita o formato de exibicao do Sheets."""
    if valor is None:
        return pd.NaT
    if isinstance(valor, datetime):
        return valor
    if isinstance(valor, date):
        return datetime(valor.year, valor.month, valor.day)
    if isinstance(valor, (int, float)) and not isinstance(valor, bool):
        if pd.isna(valor) or valor <= 0:
            return pd.NaT
        # Serial do Google Sheets: dias desde 30/12/1899.
        return datetime(1899, 12, 30) + pd.Timedelta(days=float(valor))

    texto = str(valor).strip()
    if not texto:
        return pd.NaT

    for formato in _FORMATOS_DATA:
        try:
            return datetime.strptime(texto, formato)
        except ValueError:
            continue

    convertido = pd.to_datetime(texto, dayfirst=True, errors="coerce")
    return convertido if pd.notna(convertido) else pd.NaT


def inicio_do_dia(valor):
    data = converter_data(valor)
    if pd.isna(data):
        return pd.NaT
    return datetime(data.year, data.month, data.day)


def diferenca_dias(inicio, fim):
    a, b = inicio_do_dia(inicio), inicio_do_dia(fim)
    if pd.isna(a) or pd.isna(b):
        return None
    return (b - a).days


# ------------------------------------------------------- cabecalhos


def mapa_cabecalhos(headers) -> dict:
    """
    Indice normalizado -> lista de posicoes.

    A lista existe porque CONTROLE DE PRAZOS tem mais de uma coluna
    RESPONSAVEL. A primeira ocorrencia e o responsavel tecnico e a
    ultima e a controladoria, exatamente como no Apps Script.
    """
    mapa: dict[str, list[int]] = {}
    for indice, header in enumerate(headers):
        chave = normalizar_texto(header)
        if not chave:
            continue
        mapa.setdefault(chave, []).append(indice)
    return mapa


def valor_por_cabecalho(linha, mapa, aliases, ocorrencia: int = 0):
    """Primeiro alias que existir no mapa. Espelha valorPorCabecalho_."""
    for alias in aliases:
        indices = mapa.get(normalizar_texto(alias))
        if not indices:
            continue
        posicao = indices[ocorrencia] if ocorrencia < len(indices) else indices[-1]
        if posicao < len(linha):
            return linha[posicao]
    return ""


def valor_por_cabecalho_parcial(linha, mapa, trecho):
    """Busca por conteudo parcial do cabecalho. Espelha valorPorCabecalhoParcial_."""
    alvo = normalizar_texto(trecho)
    for chave, indices in mapa.items():
        if alvo in chave:
            posicao = indices[0]
            if posicao < len(linha):
                return linha[posicao]
    return ""


# ------------------------------------------------------- classificacao

_REGRAS_TIPO_PRAZO = (
    ("SENTENÇA", ("SENTENCA",)),
    ("EMBARGOS DE DECLARAÇÃO", ("EMBARGOS DE DECLARACAO", "EDCL")),
    ("RECURSO", ("RECURSO", "APELACAO", "AGRAVO", "CONTRARRAZOES")),
    ("RÉPLICA OU MANIFESTAÇÃO", ("REPLICA", "MANIFESTACAO", "PETICAO")),
    ("DESPACHO OU ATO ORDINATÓRIO", ("DESPACHO", "ATO ORDINATORIO")),
    ("PERÍCIA", ("PERICIA", "PERICIAL")),
    ("LAUDO", ("LAUDO",)),
    ("RPV OU PRECATÓRIO", ("RPV", "PRECATORIO")),
    ("CUMPRIMENTO OU EXECUÇÃO", ("CUMPRIMENTO", "EXECUCAO", "CALCULO CONTADORIA")),
    ("TUTELA OU LIMINAR", ("TUTELA", "LIMINAR")),
    ("EMENDA À INICIAL", ("EMENDA",)),
    ("QUESITOS", ("QUESITO",)),
    ("TRÂNSITO EM JULGADO", ("TRANSITO EM JULGADO",)),
    ("AUDIÊNCIA", ("AUDIENCIA",)),
    ("INTIMAÇÃO", ("INTIMACAO",)),
    ("HONORÁRIOS", ("HONORARIO",)),
)


def classificar_tipo_prazo(conteudo) -> str:
    """Espelha classificarTipoPrazo_ do Apps Script."""
    texto = normalizar_texto(conteudo)
    if not texto:
        return "NÃO INFORMADO"
    for rotulo, termos in _REGRAS_TIPO_PRAZO:
        for termo in termos:
            if termo in texto:
                return rotulo
    return "OUTROS"


def classificar_resultado_sentenca(valor) -> str:
    """Espelha classificarResultadoSentenca_ do Apps Script."""
    texto = normalizar_texto(valor)
    if not texto:
        return "SEM SENTENÇA"
    if "PARCIAL" in texto:
        return "PARCIALMENTE PROCEDENTE"
    if "IMPROCEDENTE" in texto or texto in ("NAO", "N"):
        return "IMPROCEDENTE"
    if "PROCEDENTE" in texto or texto in ("SIM", "S"):
        return "PROCEDENTE"
    if "EXTINT" in texto:
        return "EXTINTO"
    return texto


def formatar_moeda(valor) -> str:
    numero = converter_numero(valor)
    texto = f"{numero:,.2f}".replace(",", "@").replace(".", ",").replace("@", ".")
    return f"R$ {texto}"


def formatar_data(valor) -> str:
    data = converter_data(valor)
    if pd.isna(data):
        return ""
    return data.strftime("%d/%m/%Y")


def formatar_data_hora(valor) -> str:
    data = converter_data(valor)
    if pd.isna(data):
        return ""
    return data.strftime("%d/%m/%Y %H:%M:%S")



# ==================================================================
# CONEXAO
# ==================================================================
"""
Camada de acesso ao Google Sheets.

Regra da arquitetura: escopo somente leitura. A credencial usada aqui
nao tem permissao de escrita em nenhuma das planilhas, de modo que o
painel nao consegue alterar dado operacional nem por engano.
"""




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



# ==================================================================
# MODELO
# ==================================================================
"""
Modelo de dados do painel.

Cada funcao le uma aba e devolve um DataFrame ja derivado. O mapeamento
de colunas reproduz obterContextoLinha_ do Apps Script na versao V12.3,
inclusive os aliases de compatibilidade (FATAL / PRAZO FATAL) e o
tratamento das duas colunas RESPONSAVEL do CONTROLE DE PRAZOS.
"""





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



# ==================================================================
# AUTH
# ==================================================================
"""
Controle de acesso.

Dois modos, escolhidos pelo conteudo dos Secrets:

  SENHA UNICA  -> define-se SENHA_ACESSO e pronto. Quem tem a senha ve
                  tudo. E o modo indicado quando a planilha auxiliar ja
                  e restrita a uma unica pessoa.

  POR USUARIO  -> define-se o bloco [usuarios]. Cada pessoa tem senha
                  propria e um perfil que limita telas e campos.

O modo por usuario so entra em cena se existir ao menos um usuario
cadastrado. Nao e preciso apagar nada para trocar de modo.
"""




PAGINAS_DISPONIVEIS = (
    "Visão geral",
    "Prazos",
    "Clientes",
    "Financeiro",
    "Produção",
    "Histórico e auditoria",
)

PERFIL_TOTAL = {
    "paginas": list(PAGINAS_DISPONIVEIS),
    "somente_proprios": False,
    "ver_financeiro": True,
    "ver_editor": True,
}


def _hash(texto: str) -> str:
    return hashlib.sha256(texto.encode("utf-8")).hexdigest()


def _confere(informada: str, guardada: str) -> bool:
    """
    Compara a senha informada com a guardada nos Secrets.

    Aceita hash SHA-256 (recomendado) ou texto puro, para nao travar
    quem esta so testando a instalacao. A comparacao usa compare_digest
    para nao vazar informacao pelo tempo de resposta.
    """
    guardada = str(guardada or "")
    if not guardada:
        return False
    if len(guardada) == 64 and all(c in "0123456789abcdefABCDEF" for c in guardada):
        return hmac.compare_digest(guardada.lower(), _hash(informada))
    return hmac.compare_digest(guardada, informada)


def _usuarios() -> dict:
    return dict(st.secrets.get("usuarios", {}))


def _perfis() -> dict:
    return dict(st.secrets.get("perfis", {}))


def modo_senha_unica() -> bool:
    return not _usuarios()


def autenticar(login: str, senha: str) -> dict | None:
    if modo_senha_unica():
        if _confere(senha, st.secrets.get("SENHA_ACESSO", "")):
            return {
                "login": "acesso",
                "nome": st.secrets.get("NOME_EXIBICAO", "Gestão DB"),
                "perfil": "ADMIN",
                "responsavel": "",
            }
        return None

    usuarios = _usuarios()
    chave = str(login or "").strip().lower()
    if chave not in usuarios:
        return None

    dados = dict(usuarios[chave])
    if not _confere(senha, dados.get("senha_sha256") or dados.get("senha", "")):
        return None

    return {
        "login": chave,
        "nome": dados.get("nome", chave.title()),
        "perfil": str(dados.get("perfil", "ADMIN")).upper(),
        "responsavel": dados.get("responsavel", ""),
    }


def regras_do_perfil(perfil: str) -> dict:
    """
    Sem bloco [perfis] configurado, o perfil ve tudo. A restricao e
    opcional e so existe se for declarada.
    """
    configuracao = _perfis().get(perfil)
    if not configuracao:
        return dict(PERFIL_TOTAL)

    configuracao = dict(configuracao)
    paginas = configuracao.get("paginas") or list(PAGINAS_DISPONIVEIS)
    return {
        "paginas": [p for p in PAGINAS_DISPONIVEIS if p in paginas],
        "somente_proprios": bool(configuracao.get("somente_proprios", False)),
        "ver_financeiro": bool(configuracao.get("ver_financeiro", True)),
        "ver_editor": bool(configuracao.get("ver_editor", True)),
    }


def usuario_logado() -> dict | None:
    return st.session_state.get("usuario")


def regras_atuais() -> dict:
    usuario = usuario_logado()
    return regras_do_perfil(usuario["perfil"]) if usuario else dict(PERFIL_TOTAL)


def tela_de_login() -> None:
    st.title("Gestão DB")
    st.caption("Painel de consulta. Nenhum dado é alterado por aqui.")

    unica = modo_senha_unica()

    with st.form("login"):
        login = "" if unica else st.text_input("Usuário")
        senha = st.text_input("Senha", type="password")
        entrar = st.form_submit_button("Entrar", width="stretch")

    if not entrar:
        return

    usuario = autenticar(login, senha)
    if usuario:
        st.session_state["usuario"] = usuario
        st.rerun()
    else:
        st.error("Senha inválida." if unica else "Usuário ou senha inválidos.")


def aplicar_recorte(
    dados: pd.DataFrame, coluna_responsavel: str = "responsavel"
) -> pd.DataFrame:
    """Recorte por responsavel, quando o perfil exigir."""
    usuario = usuario_logado()
    if dados.empty or not usuario:
        return dados

    if not regras_do_perfil(usuario["perfil"])["somente_proprios"]:
        return dados

    alvo = str(usuario.get("responsavel", "")).strip().upper()
    if not alvo or coluna_responsavel not in dados.columns:
        return dados.iloc[0:0]

    return dados[dados[coluna_responsavel].astype(str).str.upper() == alvo]



# ==================================================================
# COMPONENTES
# ==================================================================
"""Componentes de filtro e indicadores reutilizados pelas paginas."""



MESES = (
    "JANEIRO",
    "FEVEREIRO",
    "MARÇO",
    "ABRIL",
    "MAIO",
    "JUNHO",
    "JULHO",
    "AGOSTO",
    "SETEMBRO",
    "OUTUBRO",
    "NOVEMBRO",
    "DEZEMBRO",
)

CORES_SITUACAO = {
    "VENCIDO": "#C62828",
    "VENCE HOJE": "#F7BD2E",
    "VENCE EM 7 DIAS": "#4DA2DA",
    "NO PRAZO": "#2E7D32",
    "ENCERRADO": "#C1B7AD",
    "SEM PRAZO FATAL": "#4A5568",
}


def opcoes(dados: pd.DataFrame, coluna: str) -> list[str]:
    if dados.empty or coluna not in dados.columns:
        return []
    valores = (
        dados[coluna].astype(str).str.strip().replace("", pd.NA).dropna().unique()
    )
    return sorted(valores)


def multiselecao(rotulo: str, valores: list[str], chave: str) -> list[str]:
    if not valores:
        return []
    return st.multiselect(rotulo, valores, default=[], key=chave)


def aplicar_multiselecao(
    dados: pd.DataFrame, coluna: str, selecionados: list[str]
) -> pd.DataFrame:
    if dados.empty or not selecionados or coluna not in dados.columns:
        return dados
    return dados[dados[coluna].astype(str).str.strip().isin(selecionados)]


def filtro_periodo(
    dados: pd.DataFrame, coluna: str, rotulo: str, chave: str
) -> pd.DataFrame:
    """Filtro por intervalo de datas. Registros sem data sao preservados."""
    if dados.empty or coluna not in dados.columns:
        return dados

    serie = pd.to_datetime(dados[coluna], errors="coerce")
    validas = serie.dropna()
    if validas.empty:
        return dados

    inicio_padrao = validas.min().date()
    fim_padrao = validas.max().date()

    intervalo = st.date_input(
        rotulo,
        value=(inicio_padrao, fim_padrao),
        min_value=inicio_padrao,
        max_value=fim_padrao,
        format="DD/MM/YYYY",
        key=chave,
    )

    if not isinstance(intervalo, (tuple, list)) or len(intervalo) != 2:
        return dados

    inicio = pd.Timestamp(intervalo[0])
    fim = pd.Timestamp(intervalo[1]) + pd.Timedelta(days=1)
    return dados[serie.isna() | ((serie >= inicio) & (serie < fim))]


def cartao(coluna, titulo: str, valor, subtitulo: str = "") -> None:
    coluna.metric(titulo, valor, help=subtitulo or None)


def tabela(dados: pd.DataFrame, colunas: dict, vazio: str) -> None:
    """Renderiza a tabela ja renomeada, sem indice."""
    if dados.empty:
        st.info(vazio)
        return

    presentes = {k: v for k, v in colunas.items() if k in dados.columns}
    visao = dados[list(presentes)].rename(columns=presentes)
    st.dataframe(visao, width="stretch", hide_index=True)


def formatar_datas(dados: pd.DataFrame, colunas: list[str]) -> pd.DataFrame:
    saida = dados.copy()
    for coluna in colunas:
        if coluna in saida.columns:
            saida[coluna] = pd.to_datetime(saida[coluna], errors="coerce").dt.strftime(
                "%d/%m/%Y"
            )
    return saida


def formatar_moedas(dados: pd.DataFrame, colunas: list[str]) -> pd.DataFrame:
    saida = dados.copy()
    for coluna in colunas:
        if coluna in saida.columns:
            saida[coluna] = saida[coluna].apply(
                lambda v: f"R$ {v:,.2f}".replace(",", "@")
                .replace(".", ",")
                .replace("@", ".")
            )
    return saida


def busca_texto(dados: pd.DataFrame, colunas: list[str], termo: str) -> pd.DataFrame:
    """Busca livre em varias colunas ao mesmo tempo, sem diferenciar caixa."""
    termo = str(termo or "").strip()
    if dados.empty or not termo:
        return dados

    presentes = [c for c in colunas if c in dados.columns]
    if not presentes:
        return dados

    mascara = pd.Series(False, index=dados.index)
    for coluna in presentes:
        mascara |= (
            dados[coluna].astype(str).str.contains(termo, case=False, na=False, regex=False)
        )
    return dados[mascara]


def resumo_filtro(total: int, filtrado: int) -> None:
    """Linha curta informando o efeito do filtro atual."""
    if total == filtrado:
        st.caption(f"{total} registro(s).")
    else:
        st.caption(f"{filtrado} de {total} registro(s) após os filtros.")



# ==================================================================
# VISAO_GERAL
# ==================================================================
"""Visao geral: os numeros que a equipe precisa ver ao abrir o painel."""





def render_visao_geral(prazos: pd.DataFrame, clientes: pd.DataFrame) -> None:
    st.subheader("Visão geral")

    prazos = auth.aplicar_recorte(prazos)
    clientes = auth.aplicar_recorte(clientes)
    regras = auth.regras_atuais()

    abertos = prazos[~prazos["encerrado"]] if not prazos.empty else prazos

    colunas = st.columns(5)
    ui.cartao(colunas[0], "Prazos em aberto", len(abertos))
    ui.cartao(
        colunas[1],
        "Vencidos",
        int((abertos["situacao"] == "VENCIDO").sum()) if not abertos.empty else 0,
        "Prazo fatal ultrapassado e status ainda não encerrado.",
    )
    ui.cartao(
        colunas[2],
        "Vencem hoje",
        int((abertos["situacao"] == "VENCE HOJE").sum()) if not abertos.empty else 0,
    )
    ui.cartao(
        colunas[3],
        "Próximos 7 dias",
        int((abertos["situacao"] == "VENCE EM 7 DIAS").sum())
        if not abertos.empty
        else 0,
    )
    ui.cartao(
        colunas[4],
        "Clientes ativos",
        int((~clientes["encerrado"]).sum()) if not clientes.empty else 0,
    )

    if not abertos.empty:
        criticos = abertos[
            abertos["situacao"].isin(["VENCIDO", "VENCE HOJE", "VENCE EM 7 DIAS"])
        ].sort_values("prazo_fatal", na_position="last")

        st.markdown("#### Prazos críticos")
        ui.tabela(
            ui.formatar_datas(criticos.head(50), ["prazo_fatal", "data_evento"]),
            {
                "prazo_fatal": "Fatal",
                "autor": "Autor",
                "conteudo": "Conteúdo",
                "responsavel": "Responsável",
                "delegado": "Delegado",
                "status": "Status",
                "situacao": "Situação",
                "linha_origem": "Linha",
            },
            "Não há prazos críticos no recorte atual.",
        )

    esquerda, direita = st.columns(2)

    if not abertos.empty:
        with esquerda:
            st.markdown("#### Situação dos prazos em aberto")
            resumo = abertos["situacao"].value_counts().reset_index()
            resumo.columns = ["Situação", "Quantidade"]
            figura = px.bar(
                resumo,
                x="Situação",
                y="Quantidade",
                color="Situação",
                color_discrete_map=ui.CORES_SITUACAO,
            )
            figura.update_layout(showlegend=False, height=320)
            st.plotly_chart(figura, width="stretch")

    if not clientes.empty and regras["ver_financeiro"]:
        with direita:
            st.markdown("#### Honorários previstos por mês de ajuizamento")
            base = clientes.dropna(subset=["data_ajuizamento"]).copy()
            if base.empty:
                st.info("Nenhum ajuizamento com data preenchida.")
            else:
                base["competencia"] = base["data_ajuizamento"].dt.to_period("M").astype(
                    str
                )
                serie = (
                    base.groupby("competencia")["honorario_total"].sum().reset_index()
                )
                figura = px.bar(serie, x="competencia", y="honorario_total")
                figura.update_layout(
                    height=320,
                    xaxis_title="Competência",
                    yaxis_title="Honorários previstos",
                )
                st.plotly_chart(figura, width="stretch")



# ==================================================================
# PRAZOS
# ==================================================================
"""
Dashboard de prazos. Substitui a aba DASH PRAZOS do Google Sheets.

O corpo da tela e um fragmento: mexer em qualquer filtro reexecuta
apenas esta funcao, sem reprocessar a barra lateral nem consultar o
Google Sheets de novo.
"""




ATALHOS = {
    "Todos": [],
    "Vencidos": ["VENCIDO"],
    "Vencem hoje": ["VENCE HOJE"],
    "Próximos 7 dias": ["VENCE HOJE", "VENCE EM 7 DIAS"],
    "Críticos": ["VENCIDO", "VENCE HOJE", "VENCE EM 7 DIAS"],
}

BUSCA_PRAZOS = ["autor", "conteudo", "observacao", "responsavel", "delegado", "status"]

COLUNAS_TABELA_PRAZOS = {
    "prazo_fatal": "Fatal",
    "data_evento": "Data do evento",
    "data_final": "Data final",
    "autor": "Autor",
    "conteudo": "Conteúdo",
    "tipo_prazo": "Tipo",
    "responsavel": "Responsável",
    "delegado": "Delegado",
    "status": "Status",
    "situacao": "Situação",
    "verificacao_controladoria": "Controladoria",
    "link_bitrix": "Bitrix",
    "linha_origem": "Linha",
}


def render_prazos(prazos: pd.DataFrame) -> None:
    st.subheader("Prazos")

    prazos = auth.aplicar_recorte(prazos)
    if prazos.empty:
        st.info("Nenhum prazo disponível.")
        return

    painel_prazos(prazos)


@st.fragment
def painel_prazos(prazos: pd.DataFrame) -> None:
    atalho = st.segmented_control(
        "Atalhos",
        list(ATALHOS),
        default="Todos",
        key="pz_atalho",
        label_visibility="collapsed",
    )

    busca = st.text_input(
        "Buscar",
        placeholder="Autor, conteúdo, responsável...",
        key="pz_busca",
        label_visibility="collapsed",
    )

    with st.expander("Filtros", expanded=False):
        linha1 = st.columns(4)
        with linha1[0]:
            responsaveis = ui.multiselecao(
                "Responsável", ui.opcoes(prazos, "responsavel"), "pz_resp"
            )
        with linha1[1]:
            delegados = ui.multiselecao(
                "Delegado", ui.opcoes(prazos, "delegado"), "pz_deleg"
            )
        with linha1[2]:
            status = ui.multiselecao("Status", ui.opcoes(prazos, "status"), "pz_status")
        with linha1[3]:
            tipos = ui.multiselecao(
                "Tipo de prazo", ui.opcoes(prazos, "tipo_prazo"), "pz_tipo"
            )

        linha2 = st.columns([1, 2])
        with linha2[0]:
            somente_abertos = st.checkbox("Somente em aberto", value=True, key="pz_ab")
        with linha2[1]:
            filtrados = ui.filtro_periodo(
                prazos, "prazo_fatal", "Prazo fatal entre", "pz_periodo"
            )

    situacoes = ATALHOS.get(atalho or "Todos", [])
    filtrados = ui.aplicar_multiselecao(filtrados, "situacao", situacoes)
    filtrados = ui.aplicar_multiselecao(filtrados, "responsavel", responsaveis)
    filtrados = ui.aplicar_multiselecao(filtrados, "delegado", delegados)
    filtrados = ui.aplicar_multiselecao(filtrados, "status", status)
    filtrados = ui.aplicar_multiselecao(filtrados, "tipo_prazo", tipos)
    filtrados = ui.busca_texto(filtrados, BUSCA_PRAZOS, busca)
    if somente_abertos:
        filtrados = filtrados[~filtrados["encerrado"]]

    ui.resumo_filtro(len(prazos), len(filtrados))

    colunas = st.columns(5)
    ui.cartao(colunas[0], "No filtro", len(filtrados))
    for indice, situacao in enumerate(
        ["VENCIDO", "VENCE HOJE", "VENCE EM 7 DIAS", "NO PRAZO"], start=1
    ):
        ui.cartao(
            colunas[indice],
            situacao.capitalize(),
            int((filtrados["situacao"] == situacao).sum()),
        )

    esquerda, direita = st.columns(2)
    with esquerda:
        st.markdown("#### Por responsável")
        _barra_prazos(filtrados, "responsavel", "Responsável")
    with direita:
        st.markdown("#### Por tipo de prazo")
        _barra_prazos(filtrados, "tipo_prazo", "Tipo")

    st.markdown("#### Detalhamento")
    ordenado = filtrados.sort_values("prazo_fatal", na_position="last")
    ui.tabela(
        ui.formatar_datas(ordenado, ["data_evento", "data_final", "prazo_fatal"]),
        COLUNAS_TABELA_PRAZOS,
        "Nenhum prazo corresponde aos filtros aplicados.",
    )

    st.download_button(
        "Exportar CSV do recorte",
        ordenado.to_csv(index=False, sep=";").encode("utf-8-sig"),
        file_name="prazos.csv",
        mime="text/csv",
    )


def _barra_prazos(dados: pd.DataFrame, coluna: str, rotulo: str) -> None:
    resumo = dados[coluna].value_counts().reset_index()
    resumo.columns = [rotulo, "Quantidade"]
    if resumo.empty:
        st.info("Sem registros no filtro.")
        return
    figura = px.bar(resumo, x="Quantidade", y=rotulo, orientation="h")
    figura.update_layout(height=340, yaxis={"categoryorder": "total ascending"})
    st.plotly_chart(figura, width="stretch")



# ==================================================================
# CLIENTES
# ==================================================================
"""Dashboard de clientes. Substitui a aba DASH CLIENTES do Google Sheets."""





BUSCA_CLIENTES = ["cliente", "servico", "responsavel", "tribunal", "comentario"]


def render_clientes(clientes: pd.DataFrame) -> None:
    st.subheader("Clientes")

    clientes = auth.aplicar_recorte(clientes)
    if clientes.empty:
        st.info("Nenhum cliente disponível.")
        return

    painel_clientes(clientes)


@st.fragment
def painel_clientes(clientes: pd.DataFrame) -> None:
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
    filtrados = ui.busca_texto(filtrados, BUSCA_CLIENTES, busca)

    ui.resumo_filtro(len(clientes), len(filtrados))

    colunas = st.columns(4)
    ui.cartao(colunas[0], "Clientes no filtro", len(filtrados))
    ui.cartao(colunas[1], "Ativos", int((~filtrados["encerrado"]).sum()))
    ui.cartao(colunas[2], "Ajuizados", int(filtrados["ajuizado"].sum()))
    ui.cartao(
        colunas[3],
        "Com cálculo",
        int(filtrados["possui_calculo"].sum()),
        "Possui RT confirmada pela contabilista ou valor ajuizado consolidado.",
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

    st.markdown("#### Detalhamento")
    visao = ui.formatar_datas(
        filtrados, ["data_contrato", "data_ajuizamento", "data_sentenca"]
    )
    colunas_tabela = {
        "cliente": "Cliente",
        "servico": "Serviço",
        "status": "Status",
        "responsavel": "Responsável",
        "data_contrato": "Contrato",
        "data_ajuizamento": "Ajuizamento",
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

    st.download_button(
        "Exportar CSV do recorte",
        filtrados.to_csv(index=False, sep=";").encode("utf-8-sig"),
        file_name="clientes.csv",
        mime="text/csv",
    )



# ==================================================================
# FINANCEIRO
# ==================================================================
"""
Financeiro.

Todos os valores exibidos aqui sao previsoes lancadas no CONTROLE DE
CLIENTES, e nao valores efetivamente recebidos. O rotulo deixa isso
explicito para nao gerar leitura equivocada de faturamento.
"""





def render_financeiro(clientes: pd.DataFrame) -> None:
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

    painel_financeiro(clientes)


@st.fragment
def painel_financeiro(clientes: pd.DataFrame) -> None:
    filtrados = ui.filtro_periodo(
        clientes, "data_ajuizamento", "Ajuizamento entre", "fin_periodo"
    )
    responsaveis = ui.multiselecao(
        "Responsável", ui.opcoes(filtrados, "responsavel"), "fin_resp"
    )
    filtrados = ui.aplicar_multiselecao(filtrados, "responsavel", responsaveis)

    colunas = st.columns(4)
    ui.cartao(
        colunas[0], "Honorários contratuais", formatar_moeda(filtrados["honorario_previsto"].sum())
    )
    ui.cartao(
        colunas[1],
        "Honorários sucumbenciais",
        formatar_moeda(filtrados["honorario_sucumbencial"].sum()),
    )
    ui.cartao(colunas[2], "Total previsto", formatar_moeda(filtrados["honorario_total"].sum()))
    ui.cartao(
        colunas[3], "Valor ajuizado consolidado", formatar_moeda(filtrados["valor_ajuizado"].sum())
    )

    base = filtrados.dropna(subset=["data_ajuizamento"]).copy()
    if not base.empty:
        base["competencia"] = base["data_ajuizamento"].dt.to_period("M").astype(str)

        st.markdown("#### Honorários previstos por competência")
        serie = base.groupby("competencia")[
            ["honorario_previsto", "honorario_sucumbencial"]
        ].sum().reset_index()
        figura = px.bar(
            serie,
            x="competencia",
            y=["honorario_previsto", "honorario_sucumbencial"],
            barmode="stack",
        )
        figura.update_layout(height=360, xaxis_title="Competência", yaxis_title="R$")
        st.plotly_chart(figura, width="stretch")

    st.markdown("#### Por responsável")
    por_responsavel = (
        filtrados.groupby("responsavel")
        .agg(
            clientes=("cliente", "count"),
            honorario_total=("honorario_total", "sum"),
            valor_ajuizado=("valor_ajuizado", "sum"),
        )
        .reset_index()
        .sort_values("honorario_total", ascending=False)
    )
    ui.tabela(
        ui.formatar_moedas(por_responsavel, ["honorario_total", "valor_ajuizado"]),
        {
            "responsavel": "Responsável",
            "clientes": "Clientes",
            "honorario_total": "Honorários previstos",
            "valor_ajuizado": "Valor ajuizado",
        },
        "Sem dados no filtro.",
    )

    st.markdown("#### Por serviço")
    por_servico = (
        filtrados.groupby("servico")
        .agg(
            clientes=("cliente", "count"),
            honorario_total=("honorario_total", "sum"),
        )
        .reset_index()
        .sort_values("honorario_total", ascending=False)
    )
    ui.tabela(
        ui.formatar_moedas(por_servico, ["honorario_total"]),
        {
            "servico": "Serviço",
            "clientes": "Clientes",
            "honorario_total": "Honorários previstos",
        },
        "Sem dados no filtro.",
    )



# ==================================================================
# PRODUCAO
# ==================================================================
"""
Producao.

Cruza o estado atual (planilha principal) com o historico (planilha de
logs). E aqui que a arquitetura de duas fontes se paga: quantidade de
prazos por responsavel vem da fonte operacional, e volume de eventos
por editor vem do log.
"""




EVENTOS_RELEVANTES = (
    "LANCAMENTO_PRAZO",
    "PROTOCOLO",
    "PROTOCOLO_CLIENTE",
    "ENVIADO_REVISAO",
    "REVISAO_CONCLUIDA",
    "CONFERENCIA_BITRIX",
    "CADASTRO_CLIENTE",
)


def render_producao(prazos: pd.DataFrame, logs: pd.DataFrame) -> None:
    st.subheader("Produção")

    prazos = auth.aplicar_recorte(prazos)
    painel_producao(prazos, logs)


@st.fragment
def painel_producao(prazos: pd.DataFrame, logs: pd.DataFrame) -> None:
    regras = auth.regras_atuais()

    if not prazos.empty:
        st.markdown("#### Carga atual por responsável")
        abertos = prazos[~prazos["encerrado"]]
        resumo = (
            abertos.groupby(["responsavel", "situacao"])
            .size()
            .reset_index(name="Quantidade")
        )
        if resumo.empty:
            st.info("Nenhum prazo em aberto no recorte.")
        else:
            figura = px.bar(
                resumo,
                x="Quantidade",
                y="responsavel",
                color="situacao",
                orientation="h",
                color_discrete_map=ui.CORES_SITUACAO,
            )
            figura.update_layout(
                height=420,
                yaxis={"categoryorder": "total ascending", "title": "Responsável"},
                legend_title="Situação",
            )
            st.plotly_chart(figura, width="stretch")

    if logs.empty:
        st.info("Nenhum registro no histórico de alterações.")
        return

    st.markdown("#### Eventos registrados no período")
    periodo = ui.filtro_periodo(logs, "data_hora", "Período", "prod_periodo")
    eventos = periodo[periodo["tipo_evento"].isin(EVENTOS_RELEVANTES)]

    if eventos.empty:
        st.info("Nenhum evento relevante no período selecionado.")
        return

    contagem = (
        eventos.groupby(["chave_editor", "tipo_evento"]).size().reset_index(name="Qtd")
    )
    if not regras["ver_editor"]:
        contagem["chave_editor"] = contagem["chave_editor"].astype(str).str.split("@").str[0]

    figura = px.bar(
        contagem,
        x="Qtd",
        y="chave_editor",
        color="tipo_evento",
        orientation="h",
    )
    figura.update_layout(
        height=420,
        yaxis={"categoryorder": "total ascending", "title": "Editor"},
        legend_title="Evento",
    )
    st.plotly_chart(figura, width="stretch")

    tabela_resumo = (
        eventos.pivot_table(
            index="chave_editor",
            columns="tipo_evento",
            values="id",
            aggfunc="count",
            fill_value=0,
        )
        .reset_index()
        .rename(columns={"chave_editor": "Editor"})
    )
    st.dataframe(tabela_resumo, width="stretch", hide_index=True)



# ==================================================================
# HISTORICO
# ==================================================================
"""
Historico e auditoria.

Le a planilha de logs. Substitui a necessidade de alguem abrir uma aba
com dezenas de milhares de linhas de LOG_ALTERACOES dentro do Sheets.
"""




COLUNAS_HISTORICO = {
    "data_hora": "Data e hora",
    "aba_origem": "Aba",
    "linha": "Linha",
    "cliente_autor": "Cliente / Autor",
    "cabecalho": "Campo",
    "valor_anterior": "De",
    "valor_novo": "Para",
    "tipo_evento": "Evento",
    "chave_editor": "Editor",
}


def render_historico(logs: pd.DataFrame, base_ids: pd.DataFrame) -> None:
    st.subheader("Histórico e auditoria")

    if logs.empty:
        st.info(
            "Nenhum registro encontrado. Verifique se a planilha de logs foi "
            "informada nos Secrets e se contém a aba LOG_ALTERACOES."
        )
        return

    painel_historico(logs, base_ids)


@st.fragment
def painel_historico(logs: pd.DataFrame, base_ids: pd.DataFrame) -> None:
    regras = auth.regras_atuais()
    dados = logs.copy()

    if regras["somente_proprios"]:
        alvo = str(auth.usuario_logado().get("responsavel", "")).strip().upper()
        dados = dados[
            dados["responsavel_tecnico"].astype(str).str.upper() == alvo
        ] if alvo else dados.iloc[0:0]

    with st.expander("Filtros", expanded=True):
        colunas = st.columns(4)
        with colunas[0]:
            busca = st.text_input("Cliente ou autor", key="hist_busca")
        with colunas[1]:
            abas = ui.multiselecao("Aba", ui.opcoes(dados, "aba_origem"), "hist_aba")
        with colunas[2]:
            eventos = ui.multiselecao(
                "Evento", ui.opcoes(dados, "tipo_evento"), "hist_evento"
            )
        with colunas[3]:
            editores = ui.multiselecao(
                "Editor", ui.opcoes(dados, "chave_editor"), "hist_editor"
            )
        dados = ui.filtro_periodo(dados, "data_hora", "Período", "hist_periodo")

    if busca:
        dados = dados[
            dados["cliente_autor"].astype(str).str.contains(busca, case=False, na=False)
        ]
    dados = ui.aplicar_multiselecao(dados, "aba_origem", abas)
    dados = ui.aplicar_multiselecao(dados, "tipo_evento", eventos)
    dados = ui.aplicar_multiselecao(dados, "chave_editor", editores)

    st.caption(f"{len(dados)} registro(s) no filtro atual.")

    visao = dados.copy()
    visao["data_hora"] = pd.to_datetime(visao["data_hora"], errors="coerce").dt.strftime(
        "%d/%m/%Y %H:%M:%S"
    )
    if not regras["ver_editor"]:
        visao["chave_editor"] = (
            visao["chave_editor"].astype(str).str.split("@").str[0]
        )

    ui.tabela(visao.head(1000), COLUNAS_HISTORICO, "Nenhum registro no filtro.")

    if len(dados) > 1000:
        st.caption("Exibindo os 1.000 registros mais recentes. Refine o filtro ou exporte o CSV.")

    st.download_button(
        "Exportar CSV do recorte",
        dados.to_csv(index=False, sep=";").encode("utf-8-sig"),
        file_name="historico.csv",
        mime="text/csv",
    )

    if not base_ids.empty:
        with st.expander("BASE IDS"):
            st.dataframe(base_ids, width="stretch", hide_index=True)



# ==================================================================
# APLICAÇÃO
# ==================================================================
st.set_page_config(
    page_title="Gestão DB",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded",
)

# Cada pagina declara de quais fontes precisa. Evita carregar o log em
# telas que nao o utilizam.
FONTES_POR_PAGINA = {
    "Visão geral": ("prazos", "clientes"),
    "Prazos": ("prazos",),
    "Clientes": ("clientes",),
    "Financeiro": ("clientes",),
    "Produção": ("prazos", "logs"),
    "Histórico e auditoria": ("logs", "ids"),
}

CARREGADORES = {
    "prazos": modelo.carregar_prazos,
    "clientes": modelo.carregar_clientes,
    "logs": modelo.carregar_logs,
    "ids": modelo.carregar_base_ids,
}


def limpar_tudo() -> None:
    conexao.limpar_cache()
    for carregador in CARREGADORES.values():
        carregador.clear()
    modelo.estado_do_espelho.clear()


def barra_lateral(usuario: dict) -> str:
    with st.sidebar:
        st.markdown(f"### {usuario['nome']}")
        if not auth.modo_senha_unica():
            st.caption(f"Perfil: {usuario['perfil']}")
        st.divider()

        pagina = st.radio(
            "Painel",
            auth.regras_atuais()["paginas"],
            label_visibility="collapsed",
            key="pagina_atual",
        )

        st.divider()
        _estado_do_espelho()
        st.caption(f"Última leitura do painel: {conexao.rotulo_ultima_leitura()}")
        if st.button("Atualizar agora", width="stretch"):
            limpar_tudo()
            st.rerun()
        st.caption(
            f"Cache de {conexao.TTL_CACHE}s. O painel não escreve nas planilhas."
        )

        if st.button("Sair", width="stretch"):
            st.session_state.clear()
            st.rerun()

    return pagina


def _estado_do_espelho() -> None:
    """Idade do espelho, com aviso quando o dado ficar velho."""
    try:
        estado = modelo.estado_do_espelho()
    except Exception:  # noqa: BLE001 - aba de controle e opcional
        return

    momento = estado.get("ATUALIZADO_EM")
    if not momento:
        st.caption("Espelho: sem registro de atualização.")
        return

    idade = modelo.minutos_desde_atualizacao(estado)
    intervalo = float(estado.get("INTERVALO_MINUTOS") or 10)
    tolerancia = max(intervalo * 3, 30)

    if idade is not None and idade > tolerancia:
        st.warning(
            f"Espelho atualizado em {momento}, há cerca de {int(idade)} min. "
            "Verifique o gatilho de atualização na planilha auxiliar."
        )
    else:
        st.caption(f"Espelho atualizado em: {momento}")

    problemas = [
        f"{chave.replace('ESPELHO_', '')}: {valor}"
        for chave, valor in estado.items()
        if chave.startswith("ESPELHO_") and not valor.startswith("OK")
    ]
    if problemas:
        st.error("Espelho com problema — " + " | ".join(problemas))


def carregar(pagina: str) -> dict:
    dados = {}
    try:
        for fonte in FONTES_POR_PAGINA[pagina]:
            dados[fonte] = CARREGADORES[fonte]()
        conexao.marcar_leitura()
    except RuntimeError as erro:
        st.error(str(erro))
        st.stop()
    except KeyError as erro:
        st.error(
            "Falta configuração nos Secrets do aplicativo: "
            f"{erro}. Consulte o arquivo secrets.toml.example."
        )
        st.stop()
    return dados


def main() -> None:
    usuario = auth.usuario_logado()
    if not usuario:
        auth.tela_de_login()
        return

    pagina = barra_lateral(usuario)
    dados = carregar(pagina)

    st.title("Gestão DB")

    if pagina == "Visão geral":
        render_visao_geral(dados["prazos"], dados["clientes"])
    elif pagina == "Prazos":
        render_prazos(dados["prazos"])
    elif pagina == "Clientes":
        render_clientes(dados["clientes"])
    elif pagina == "Financeiro":
        render_financeiro(dados["clientes"])
    elif pagina == "Produção":
        render_producao(dados["prazos"], dados["logs"])
    elif pagina == "Histórico e auditoria":
        render_historico(dados["logs"], dados["ids"])


if __name__ == "__main__":
    main()
