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


# ------------------------------------------- resultados processuais

_MOTIVOS = (
    ("PRESCRIÇÃO", r"PRESCRICAO"),
    ("DECADÊNCIA", r"DECADENCIA"),
    ("LAUDO OU PERÍCIA DESFAVORÁVEL", r"LAUDO.{0,40}DESFAVORAVEL|PERICIA.{0,40}DESFAVORAVEL"),
    ("LAUDO OU PERÍCIA FAVORÁVEL", r"LAUDO.{0,40}FAVORAVEL|PERICIA.{0,40}FAVORAVEL"),
    ("DOCUMENTAÇÃO OU PROVA INSUFICIENTE",
     r"DOCUMENTACAO INSUFICIENTE|DOCUMENTOS INSUFICIENTES|AUSENCIA DE PROVA|FALTA DE PROVA"),
    ("ILEGITIMIDADE", r"ILEGITIMIDADE"),
    ("FALTA DE INTERESSE PROCESSUAL", r"FALTA DE INTERESSE|AUSENCIA DE INTERESSE"),
    ("HONORÁRIOS PERICIAIS", r"HONORARIOS PERICIAIS"),
    ("SUCUMBÊNCIA", r"SUCUMBENCIA"),
)


def classificar_motivo_resultado(texto: str) -> str:
    """Espelha classificarMotivoResultado_ do Apps Script."""
    motivos = [rotulo for rotulo, padrao in _MOTIVOS if re.search(padrao, texto)]
    return " | ".join(motivos) if motivos else "NÃO REGISTRADO NO CONTROLE"


def extrair_resultados_processuais(conteudo, observacao) -> list[dict]:
    """
    Espelha extrairResultadosProcessuais_ do Apps Script.

    Um mesmo registro pode gerar mais de um resultado, por exemplo AJG
    deferida e liminar indeferida no mesmo andamento. A ordem dos testes
    importa: parcial antes de procedente, nao acolhidos antes de
    acolhidos. Alterar aqui exige alterar o .gs, senao painel e
    dashboard passam a divergir.
    """
    partes = [str(v).strip() for v in (conteudo, observacao) if valor_preenchido(v)]
    vistos, unicas = set(), []
    for parte in partes:
        chave = normalizar_texto(parte)
        if chave and chave not in vistos:
            vistos.add(chave)
            unicas.append(parte)

    registro = " | ".join(unicas)
    texto = normalizar_texto(registro)
    if not texto:
        return []

    motivo = classificar_motivo_resultado(texto)
    resultados: list[dict] = []

    def adicionar(categoria: str, resultado: str) -> None:
        if any(
            r["categoria"] == categoria and r["resultado"] == resultado
            for r in resultados
        ):
            return
        resultados.append(
            {
                "categoria": categoria,
                "resultado": resultado,
                "motivo": motivo,
                "texto": registro[:600],
            }
        )

    busca = lambda padrao: bool(re.search(padrao, texto))  # noqa: E731

    parcial = busca(r"PARCIALMENTE PROCEDENTE|PARCIAL PROCEDENTE|PROCEDENTE EM PARTE")
    improcedente = busca(r"SENTENCA.{0,100}IMPROCEDENTE|IMPROCEDENTE.{0,100}SENTENCA")
    extincao = busca(r"SENTENCA DE EXTINCAO|SENTENCA EXTINCAO|SENTENCA.{0,80}EXTINCAO")
    desconstituida = busca(r"SENTENCA.{0,80}DESCONSTITUIDA")
    procedente = busca(r"SENTENCA.{0,100}PROCEDENTE") and not improcedente and not parcial
    inicia_sentenca = busca(r"^SENTENCA\b")
    fase_posterior = busca(
        r"CUMPRIMENTO DE SENTENCA|TRANSITO EM JULGADO|CUSTAS.{0,30}SENTENCA|BAIXA.{0,30}SENTENCA"
    )

    if parcial:
        adicionar("SENTENÇA", "PARCIALMENTE PROCEDENTE")
    elif improcedente:
        adicionar("SENTENÇA", "IMPROCEDENTE")
    elif extincao:
        adicionar("SENTENÇA", "EXTINÇÃO")
    elif desconstituida:
        adicionar("SENTENÇA", "DESCONSTITUÍDA")
    elif procedente:
        adicionar("SENTENÇA", "PROCEDENTE")
    elif inicia_sentenca and not fase_posterior:
        adicionar("SENTENÇA", "SEM CLASSIFICAÇÃO")

    ajg_deferida = busca(
        r"\bAJG (DEFERIDA|CONCEDIDA)\b|JUSTICA GRATUITA (DEFERIDA|CONCEDIDA)\b"
        r"|GRATUIDADE DA JUSTICA (DEFERIDA|CONCEDIDA)\b|CONCEDIDA A GRATUIDADE DA JUSTICA"
    )
    ajg_indeferida = busca(
        r"\bAJG (INDEFERIDA|INDFEFERIDA|NEGADA)\b"
        r"|JUSTICA GRATUITA (INDEFERIDA|INDFEFERIDA|NEGADA)\b"
        r"|GRATUIDADE DA JUSTICA (INDEFERIDA|NAO CONCEDIDA|NEGADA)\b"
    )
    if ajg_indeferida:
        adicionar("JUSTIÇA GRATUITA", "INDEFERIDA")
    elif ajg_deferida:
        adicionar("JUSTIÇA GRATUITA", "DEFERIDA")

    if busca(r"\bLIMINAR\b"):
        if busca(
            r"LIMINAR.{0,80}(INDEFERIDA|NAO CONCEDIDA|NEGADA)"
            r"|(INDEFERIDA|NAO CONCEDIDA|NEGADA).{0,80}LIMINAR"
        ):
            adicionar("LIMINAR", "INDEFERIDA")
        elif busca(r"LIMINAR.{0,80}(DEFERIDA|CONCEDIDA)|(DEFERIDA|CONCEDIDA).{0,80}LIMINAR"):
            adicionar("LIMINAR", "DEFERIDA")
        elif busca(r"DECISAO LIMINAR|DESPACHO.{0,30}LIMINAR"):
            adicionar("LIMINAR", "SEM CLASSIFICAÇÃO")

    if busca(r"\bAGRAVO\b"):
        if busca(r"AGRAVO.{0,100}NAO PROVIDO|NAO PROVIDO.{0,100}AGRAVO"):
            adicionar("AGRAVO", "NÃO PROVIDO")
        elif busca(r"AGRAVO.{0,100}PROVIDO|PROVIDO.{0,100}AGRAVO"):
            adicionar("AGRAVO", "PROVIDO")
        elif busca(r"AGRAVO.{0,100}RECEBIDO.{0,100}SEM EFEITO SUSPENSIVO"):
            adicionar("AGRAVO", "RECEBIDO SEM EFEITO SUSPENSIVO")
        elif busca(r"AGRAVO.{0,100}RECEBIDO.{0,100}EFEITO SUSPENSIVO"):
            adicionar("AGRAVO", "RECEBIDO COM EFEITO SUSPENSIVO")
        elif busca(r"AGRAVO DE INSTRUMENTO|AGRAVO INTERNO|^AGRAVO\b") and not busca(
            r"CONTRARRAZ|DOCUMENTOS.{0,30}AGRAVO"
        ):
            adicionar("AGRAVO", "INTERPOSTO OU PENDENTE")

    if busca(r"\bEMBARGOS\b"):
        contrarrazoes = busca(r"CONTRARRAZ")
        sessao = busca(r"SESSAO.{0,40}EMBARGOS|AGUARDANDO.{0,40}EMBARGOS")
        emb_parcial = busca(
            r"EMBARGOS.{0,100}(ACOLHIDOS EM PARTE|PARCIALMENTE ACOLHIDOS|PARCIALMENTE PROVIDOS)"
            r"|PARCIALMENTE.{0,60}EMBARGOS"
        )
        nao_acolhidos = busca(
            r"EMBARGOS.{0,100}(NAO ACOLHIDOS|NAO COLHIDOS|NAO PROVIDOS)|NAO ACOLHIDOS.{0,80}EMBARGOS"
        )
        nao_conhecidos = busca(r"EMBARGOS.{0,100}NAO CONHECIDOS")
        acolhidos = (
            not nao_acolhidos
            and not emb_parcial
            and busca(r"EMBARGOS.{0,100}ACOLHIDOS|ACOLHIDOS.{0,80}EMBARGOS")
        )

        if emb_parcial:
            adicionar("EMBARGOS", "PARCIALMENTE ACOLHIDOS")
        elif nao_conhecidos:
            adicionar("EMBARGOS", "NÃO CONHECIDOS")
        elif nao_acolhidos:
            adicionar("EMBARGOS", "NÃO ACOLHIDOS")
        elif acolhidos:
            adicionar("EMBARGOS", "ACOLHIDOS")
        elif not contrarrazoes and not sessao and busca(r"^EMBARGOS\b"):
            adicionar("EMBARGOS", "INTERPOSTOS OU PENDENTES")

    return resultados
