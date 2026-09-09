"""
Normalizacao de texto, numeros e datas.

Estas funcoes espelham deliberadamente o comportamento do Apps Script
(normalizarTexto_, converterNumero_, converterPercentual_) para que o
Streamlit produza exatamente os mesmos agrupamentos que o dashboard
atual do Google Sheets. Nao alterar sem alterar o .gs correspondente.
"""

from __future__ import annotations

import re
import unicodedata
from datetime import date, datetime

import pandas as pd

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
