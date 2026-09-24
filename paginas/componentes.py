"""Componentes de filtro e indicadores reutilizados pelas paginas."""

from __future__ import annotations

from datetime import date
from html import escape

import pandas as pd
import streamlit as st

# Recorte padrão dos filtros de data. Foi quando o cadastro passou a
# ser confiável. Os registros anteriores continuam na base e podem ser
# alcançados ampliando o filtro; só não entram por padrão.
INICIO_PADRAO = date(2026, 1, 1)

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
    dados: pd.DataFrame, coluna: str, rotulo: str, chave: str,
    sem_corte: bool = False,
) -> pd.DataFrame:
    """
    Filtro por intervalo de datas. Registros sem data sao preservados.

    Por padrão começa em INICIO_PADRAO. Com sem_corte=True começa na
    data mais antiga da base, para telas que são ficha do cliente e não
    relatório do ano.
    """
    if dados.empty or coluna not in dados.columns:
        return dados

    serie = pd.to_datetime(dados[coluna], errors="coerce")
    validas = serie.dropna()
    if validas.empty:
        return dados

    minimo = validas.min().date()
    fim_padrao = validas.max().date()
    inicio_padrao = (
        INICIO_PADRAO
        if not sem_corte and minimo <= INICIO_PADRAO <= fim_padrao
        else minimo
    )

    intervalo = st.date_input(
        rotulo,
        value=(inicio_padrao, fim_padrao),
        min_value=minimo,
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

    # Link do Bitrix vira botão clicável que abre o negócio em outra aba.
    configuracao = {}
    if "link_bitrix" in presentes:
        rotulo = presentes["link_bitrix"]
        visao[rotulo] = visao[rotulo].map(url_bitrix)
        configuracao[rotulo] = st.column_config.LinkColumn(
            rotulo, display_text="Abrir ↗", width="small"
        )
    st.dataframe(
        visao, width="stretch", hide_index=True, column_config=configuracao or None
    )


def url_bitrix(valor) -> str | None:
    """Link utilizável, ou None. Link sem protocolo ganha https://."""
    texto = str(valor or "").strip()
    if not texto or texto.upper() in ("NAN", "NONE"):
        return None
    if not texto.lower().startswith(("http://", "https://")):
        texto = "https://" + texto
    return texto


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
            def _formatar(valor):
                # Valor ausente vira travessão. "R$ <NA>" aparecia na
                # linha de quem tem carteira mas nenhum ajuizamento.
                if pd.isna(valor):
                    return "—"
                return (
                    f"R$ {valor:,.2f}".replace(",", "@")
                    .replace(".", ",")
                    .replace("@", ".")
                )

            saida[coluna] = saida[coluna].apply(_formatar)
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


def moeda_cheia(valor, simbolo: bool = False) -> str:
    """
    Valor por extenso no padrão brasileiro: 1.234.567,89.

    Sem o símbolo por padrão, porque nas tabelas-resumo o cabeçalho já
    avisa que o valor é em reais e o "R$" em cada célula só alarga a
    coluna. Zero e ausente viram travessão.
    """
    try:
        numero = float(valor)
    except (TypeError, ValueError):
        return "—"
    if pd.isna(numero) or numero == 0:
        return "—"
    texto = f"{numero:,.2f}".replace(",", "@").replace(".", ",").replace("@", ".")
    return f"R$ {texto}" if simbolo else texto


def _inteiro(valor) -> str:
    try:
        numero = float(valor)
    except (TypeError, ValueError):
        return "—"
    if pd.isna(numero) or numero == 0:
        return "—"
    return f"{int(round(numero)):,}".replace(",", ".")


def _percentual(valor) -> str:
    try:
        numero = float(valor)
    except (TypeError, ValueError):
        return "—"
    if pd.isna(numero):
        return "—"
    return f"{numero:.1f}%".replace(".", ",")


def adicionar_total(
    dados: pd.DataFrame,
    coluna_rotulo: str,
    somar: list[str],
    medias: dict | None = None,
    rotulo: str = "TOTAL",
) -> pd.DataFrame:
    """
    Acrescenta a linha de total ao final.

    Soma apenas as colunas indicadas. Coluna de média não pode ser
    somada: o total dela é recalculado como numerador sobre
    denominador, já totalizados (medias = {coluna: (num, den)}).
    Colunas acumuladas ficam em branco na linha de total.
    """
    if dados.empty:
        return dados
    linha = {coluna: pd.NA for coluna in dados.columns}
    linha[coluna_rotulo] = rotulo
    for coluna in somar:
        if coluna in dados.columns:
            linha[coluna] = pd.to_numeric(dados[coluna], errors="coerce").sum()
    for coluna, regra in (medias or {}).items():
        if coluna in dados.columns:
            numerador, denominador = regra[0], regra[1]
            fator = regra[2] if len(regra) > 2 else 1
            den = linha.get(denominador)
            num = linha.get(numerador)
            linha[coluna] = (num / den * fator) if den and not pd.isna(den) else pd.NA
    return pd.concat([dados, pd.DataFrame([linha])], ignore_index=True)


def tabela_compacta(
    dados: pd.DataFrame,
    colunas: dict,
    moedas: list[str] | tuple = (),
    inteiros: list[str] | tuple = (),
    percentuais: list[str] | tuple = (),
    total: bool = True,
    somar: list[str] | None = None,
    medias: dict | None = None,
    vazio: str = "Sem dados no filtro.",
    valores_total: dict | None = None,
    alinhar_direita: list[str] | tuple = (),
) -> None:
    """
    Tabela-resumo enxuta, em HTML.

    O st.dataframe estica a tabela na largura toda e abrevia pouco; para
    resumo de poucas linhas isso espalha a informação. Aqui a tabela
    ocupa só a largura do conteúdo, os números ficam alinhados à direita
    com valor cheio e a última linha traz o total da coluna.

    Por padrão soma moedas e inteiros. Percentuais só entram no total
    se forem indicados em `somar` (participação soma 100%, variação não).
    """
    if dados is None or dados.empty:
        st.info(vazio)
        return

    presentes = {k: v for k, v in colunas.items() if k in dados.columns}
    base = dados[list(presentes)].copy()
    primeira = list(presentes)[0]

    if total:
        if somar is None:
            somar = [c for c in list(moedas) + list(inteiros) if c in base.columns]
        base = adicionar_total(base, primeira, somar, medias)
        # Valor pronto para a linha de total, quando não é soma nem razão
        # (por exemplo, a média simples de uma coluna de dias).
        for coluna, valor in (valores_total or {}).items():
            if coluna in base.columns:
                base.loc[base.index[-1], coluna] = valor

    numericas = set(moedas) | set(inteiros) | set(percentuais) | set(alinhar_direita)
    # Link do Bitrix vira um link clicável que abre em outra aba. É a única
    # célula montada em HTML; o resto é escapado.
    links = {}
    if "link_bitrix" in base.columns:
        for posicao, valor in enumerate(base["link_bitrix"]):
            url = url_bitrix(valor) if isinstance(valor, str) else None
            links[posicao] = (
                f'<a href="{escape(url, quote=True)}" target="_blank" '
                f'rel="noopener">Abrir ↗</a>' if url else ""
            )
    for coluna in base.columns:
        if coluna in moedas:
            base[coluna] = base[coluna].apply(moeda_cheia)
        elif coluna in inteiros:
            base[coluna] = base[coluna].apply(_inteiro)
        elif coluna in percentuais:
            base[coluna] = base[coluna].apply(_percentual)
        else:
            base[coluna] = base[coluna].apply(
                lambda v: "" if (v is None or (not isinstance(v, str) and pd.isna(v)))
                else str(v)
            )

    def classe(coluna: str) -> str:
        nomes = []
        if coluna in numericas:
            nomes.append("num")
        if str(presentes.get(coluna, coluna)).upper() == "TOTAL":
            nomes.append("col-total")
        return " ".join(nomes)

    cabecalho = "".join(
        f'<th class="{classe(c)}">{escape(str(presentes[c]))}</th>' for c in base.columns
    )
    linhas = []
    ultima = len(base) - 1
    for posicao, (_, registro) in enumerate(base.iterrows()):
        celulas = "".join(
            f'<td class="{classe(c)}">'
            + (links.get(posicao, "") if c == "link_bitrix" else escape(str(registro[c])))
            + "</td>"
            for c in base.columns
        )
        atributo = ' class="total"' if total and posicao == ultima else ""
        linhas.append(f"<tr{atributo}>{celulas}</tr>")

    html = (
        '<div class="tab-db-wrap"><table class="tab-db">'
        f"<thead><tr>{cabecalho}</tr></thead>"
        f"<tbody>{''.join(linhas)}</tbody></table></div>"
    )
    st.markdown(html, unsafe_allow_html=True)


def matriz_com_total(
    dados: pd.DataFrame,
    indice: str,
    coluna: str,
    valor: str,
    rotulo_indice: str,
    formato: str = "moeda",
    aggfunc: str = "sum",
) -> None:
    """
    Tabela cruzada com total na última coluna e na última linha.

    Colunas inteiramente zeradas saem, porque só alargam a tabela.
    Os valores vão cheios (sem abreviar em mil ou mi).
    """
    if dados.empty:
        st.info("Sem dados no filtro.")
        return

    matriz = dados.pivot_table(
        index=indice, columns=coluna, values=valor, aggfunc=aggfunc, fill_value=0
    )
    matriz = matriz.loc[:, (matriz != 0).any(axis=0)]
    if matriz.empty:
        st.info("Sem dados no filtro.")
        return

    matriz["TOTAL"] = matriz.sum(axis=1)
    matriz = matriz.reset_index()
    matriz.columns = [str(c) for c in matriz.columns]
    valores = [c for c in matriz.columns if c != indice]
    rotulos = {indice: rotulo_indice, **{c: c for c in valores}}

    if formato == "moeda":
        tabela_compacta(matriz, rotulos, moedas=valores)
    else:
        tabela_compacta(matriz, rotulos, inteiros=valores)


def remover_colunas_vazias(dados: pd.DataFrame, colunas: list[str]) -> list[str]:
    """
    Devolve só as colunas que têm algum valor diferente de zero.

    Coluna inteiramente zerada não informa nada e empurra o resto da
    tabela para fora da tela.
    """
    uteis = []
    for coluna in colunas:
        if coluna not in dados.columns:
            continue
        serie = pd.to_numeric(dados[coluna], errors="coerce").fillna(0)
        if (serie != 0).any():
            uteis.append(coluna)
    return uteis
