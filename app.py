"""
Gestão DB — painel de consulta.

Arquitetura:
    PLANILHA PRINCIPAL  -> onde a equipe trabalha. Nao e compartilhada
                           com a conta de servico e nao e lida daqui.
    PLANILHA AUXILIAR   -> BI_PRAZOS e BI_CLIENTES por IMPORTRANGE,
                           mais LOG_ALTERACOES gravado pelo Apps Script.
    STREAMLIT           -> le somente a auxiliar, calcula em memoria.

Como o espelho tem defasagem, o painel mostra na barra lateral quando
foi a ultima atualizacao do IMPORTRANGE e avisa quando o dado passa do
tempo esperado. Sem esse aviso, dado velho passaria por dado atual.

Sobre desempenho: cada pagina carrega apenas o que ela usa. O log, que
e a fonte pesada, so e lido nas telas de Producao e Historico. Os
filtros rodam dentro de fragmentos (st.fragment), de modo que mexer num
filtro nao reexecuta o app inteiro nem refaz leitura de planilha.
"""

from __future__ import annotations

import os
import sys

import streamlit as st

# Garante que os pacotes locais db/ e paginas/ sejam encontrados mesmo
# quando o app e executado a partir de outro diretorio de trabalho.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from db import auth, conexao, modelo  # noqa: E402
from paginas import (
    clientes,
    financeiro,
    historico,
    logs,
    prazos,
    producao,
    resultados,
    visao_geral,
)

st.set_page_config(
    page_title="Gestão DB | Dutra Bitencourt",
    page_icon="⚖️",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# Paleta institucional, a mesma usada nos dashboards do Google Sheets,
# para que o painel e a planilha nao pareçam sistemas diferentes.
PALETA = {
    "AZUL_ESCURO": "#1A3762",
    "AZUL_CLARO": "#4DA2DA",
    "AMARELO": "#F7BD2E",
    "BEGE": "#C1B7AD",
    "CINZA_FUNDO": "#F3F6F9",
    "CINZA_TEXTO": "#4A5568",
    "VERMELHO": "#C62828",
    "VERDE": "#2E7D32",
}


def aplicar_identidade_visual() -> None:
    """
    Identidade visual do escritório, aplicada por CSS.

    O tema normalmente iria em .streamlit/config.toml, mas a versão de
    arquivo único não tem essa pasta. Injetar aqui mantém o visual sem
    depender de arquivo extra no repositório.

    A referência é o papel timbrado: fundo branco, faixa azul-marinho
    no topo, filete amarelo e tipografia sóbria. Nada de tema escuro.
    """
    st.markdown(
        f"""
        <style>
        html, body, [class*="css"], .stMarkdown, .stText,
        button, input, select, textarea {{
            font-family: Arial, Helvetica, sans-serif;
        }}
        .stApp {{ background-color: #FFFFFF; }}
        section[data-testid="stSidebar"] {{ display: none; }}
        .block-container {{ padding-top: 1.2rem; max-width: 1500px; }}

        h1, h2, h3, h4 {{
            color: {PALETA["AZUL_ESCURO"]};
            font-weight: 700;
            letter-spacing: -0.01em;
        }}
        h3 {{ font-size: 1.25rem; margin-top: 1.4rem; }}
        h4 {{
            font-size: 1rem;
            text-transform: uppercase;
            letter-spacing: 0.06em;
            color: {PALETA["CINZA_TEXTO"]};
            border-bottom: 1px solid #E3E9F0;
            padding-bottom: 6px;
            margin-top: 1.6rem;
        }}

        /* Faixa institucional, no espírito do papel timbrado */
        .timbre {{
            background: {PALETA["AZUL_ESCURO"]};
            border-radius: 4px;
            padding: 16px 22px 14px 22px;
            display: flex;
            align-items: center;
            justify-content: space-between;
            border-bottom: 4px solid {PALETA["AMARELO"]};
            margin-bottom: 18px;
        }}
        .timbre .marca {{
            display: flex;
            align-items: center;
            gap: 14px;
        }}
        .timbre .monograma {{
            border: 2px solid #FFFFFF;
            border-radius: 6px;
            color: #FFFFFF;
            font-weight: 700;
            font-size: 1.05rem;
            letter-spacing: 0.02em;
            padding: 4px 9px;
        }}
        .timbre .nome {{
            color: #FFFFFF;
            font-size: 1.35rem;
            font-weight: 700;
            line-height: 1.1;
        }}
        .timbre .nome span {{
            display: block;
            font-size: 0.7rem;
            font-weight: 400;
            letter-spacing: 0.18em;
            text-transform: uppercase;
            color: #C9D6E6;
            margin-top: 3px;
        }}
        .timbre .contexto {{
            color: #FFFFFF;
            font-size: 0.82rem;
            text-align: right;
            letter-spacing: 0.1em;
            text-transform: uppercase;
        }}

        /* Indicadores */
        div[data-testid="stMetric"] {{
            background-color: #FFFFFF;
            border: 1px solid #E3E9F0;
            border-left: 4px solid {PALETA["AZUL_CLARO"]};
            border-radius: 6px;
            padding: 12px 14px;
            overflow: visible;
        }}
        div[data-testid="stMetricValue"] {{
            color: {PALETA["AZUL_ESCURO"]};
            font-weight: 700;
            font-size: 1.5rem;
            white-space: normal !important;
            overflow: visible !important;
            text-overflow: clip !important;
            overflow-wrap: anywhere;
            line-height: 1.2;
        }}
        div[data-testid="stMetricValue"] > div {{
            white-space: normal !important;
            overflow: visible !important;
            text-overflow: clip !important;
        }}
        div[data-testid="stMetricLabel"] {{
            color: {PALETA["CINZA_TEXTO"]};
            text-transform: uppercase;
            font-size: 0.72rem;
            letter-spacing: 0.05em;
        }}

        /* Navegação e botões */
        div[data-testid="stSegmentedControl"] button {{ font-weight: 600; }}
        .stButton button {{
            border-radius: 6px;
            font-weight: 600;
            border: 1px solid {PALETA["AZUL_ESCURO"]};
            color: {PALETA["AZUL_ESCURO"]};
            background: #FFFFFF;
        }}
        .stButton button:hover {{
            background: {PALETA["AZUL_ESCURO"]};
            color: #FFFFFF;
        }}

        .barra-status {{
            color: {PALETA["CINZA_TEXTO"]};
            font-size: 0.78rem;
            padding: 4px 0 2px 0;
            border-top: 1px solid #EEF2F7;
        }}
        div[data-testid="stExpander"] {{
            border: 1px solid #E3E9F0;
            border-radius: 6px;
        }}
        </style>
        """,
        unsafe_allow_html=True,
    )


def cabecalho(subtitulo: str) -> None:
    st.markdown(
        f"""
        <div class="timbre">
          <div class="marca">
            <div class="monograma">DB</div>
            <div class="nome">Dutra Bitencourt<span>Advocacia</span></div>
          </div>
          <div class="contexto">{subtitulo}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


aplicar_identidade_visual()

# Cada pagina declara de quais fontes precisa. Evita carregar o log em
# telas que nao o utilizam.
FONTES_POR_PAGINA = {
    "Visão geral": ("prazos", "clientes"),
    "Prazos": ("prazos",),
    "Clientes": ("clientes",),
    "Financeiro": ("clientes",),
    "Resultados": ("prazos",),
    "Produção": ("prazos", "logs"),
    "Logs e ciclos": ("logs", "clientes"),
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


def barra_superior(usuario: dict) -> str:
    """
    Navegacao no topo, em vez de barra lateral.

    Com a lateral ocupando espaco, os cartoes de indicador ficavam
    estreitos e o Streamlit truncava os valores com reticencias. No
    topo, o conteudo usa a largura inteira da tela.
    """
    paginas = auth.regras_atuais()["paginas"]

    navegacao, atualizar, sair = st.columns([8, 1.3, 1])

    with navegacao:
        pagina = st.segmented_control(
            "Painel",
            paginas,
            default=st.session_state.get("pagina_atual") or paginas[0],
            key="pagina_atual",
            label_visibility="collapsed",
        )
    with atualizar:
        if st.button("Atualizar", width="stretch"):
            limpar_tudo()
            st.rerun()
    with sair:
        if st.button("Sair", width="stretch"):
            st.session_state.clear()
            st.rerun()

    return pagina or paginas[0]


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
        st.markdown(
            f'<div class="barra-status">Espelho atualizado em {momento}'
            f' · última leitura do painel {conexao.rotulo_ultima_leitura()}'
            f' · cache de {conexao.TTL_CACHE}s · o painel não escreve nas '
            f'planilhas</div>',
            unsafe_allow_html=True,
        )

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
        cabecalho("Painel de gestão · acesso restrito")
        auth.tela_de_login()
        return

    # O cabecalho vem antes do carregamento de proposito: se a leitura
    # falhar e a execucao parar, a tela de erro ainda aparece dentro da
    # identidade visual, e nao numa pagina em branco.
    cabecalho(f"Painel de gestão · {usuario['nome']}")

    pagina = barra_superior(usuario)
    _estado_do_espelho()

    dados = carregar(pagina)

    if pagina == "Visão geral":
        visao_geral.render(dados["prazos"], dados["clientes"])
    elif pagina == "Prazos":
        prazos.render(dados["prazos"])
    elif pagina == "Clientes":
        clientes.render(dados["clientes"])
    elif pagina == "Financeiro":
        financeiro.render(dados["clientes"])
    elif pagina == "Resultados":
        resultados.render(dados["prazos"])
    elif pagina == "Produção":
        producao.render(dados["prazos"], dados["logs"])
    elif pagina == "Logs e ciclos":
        logs.render(dados["logs"], dados["clientes"])
    elif pagina == "Histórico e auditoria":
        historico.render(dados["logs"], dados["ids"])


if __name__ == "__main__":
    main()
