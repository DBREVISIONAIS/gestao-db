"""Componentes de filtro e indicadores reutilizados pelas paginas."""

from __future__ import annotations

import pandas as pd
import streamlit as st

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
