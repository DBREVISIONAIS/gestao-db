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
from paginas import clientes, financeiro, historico, producao, prazos, visao_geral

st.set_page_config(
    page_title="Gestão DB | Dutra Bitencourt",
    page_icon="⚖️",
    layout="wide",
    initial_sidebar_state="expanded",
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
    Aplica a identidade do escritorio por CSS.

    O tema normalmente iria em .streamlit/config.toml, mas na versao de
    arquivo unico nao existe essa pasta. Injetar o estilo aqui mantem o
    visual sem depender de arquivo extra no repositorio.
    """
    st.markdown(
        f"""
        <style>
        html, body, [class*="css"], .stMarkdown, .stText {{
            font-family: Arial, Helvetica, sans-serif;
        }}
        .stApp {{ background-color: #FFFFFF; }}
        h1, h2, h3, h4 {{
            color: {PALETA["AZUL_ESCURO"]};
            font-family: Arial, Helvetica, sans-serif;
            font-weight: 700;
        }}
        section[data-testid="stSidebar"] {{
            background-color: {PALETA["AZUL_ESCURO"]};
        }}
        section[data-testid="stSidebar"] * {{ color: #FFFFFF !important; }}
        section[data-testid="stSidebar"] .stButton button {{
            background-color: {PALETA["AZUL_CLARO"]};
            color: #FFFFFF !important;
            border: none;
            font-weight: 600;
        }}
        div[data-testid="stMetric"] {{
            background-color: {PALETA["CINZA_FUNDO"]};
            border-left: 5px solid {PALETA["AZUL_CLARO"]};
            border-radius: 6px;
            padding: 12px 16px;
        }}
        div[data-testid="stMetricValue"] {{
            color: {PALETA["AZUL_ESCURO"]};
            font-weight: 700;
        }}
        div[data-testid="stMetricLabel"] {{
            color: {PALETA["CINZA_TEXTO"]};
            text-transform: uppercase;
            font-size: 0.75rem;
            letter-spacing: 0.04em;
        }}
        .stButton button {{
            border-radius: 6px;
            font-weight: 600;
        }}
        .marca-db {{
            border-bottom: 3px solid {PALETA["AMARELO"]};
            padding-bottom: 10px;
            margin-bottom: 18px;
        }}
        .marca-db .titulo {{
            color: {PALETA["AZUL_ESCURO"]};
            font-size: 1.9rem;
            font-weight: 700;
            letter-spacing: -0.01em;
        }}
        .marca-db .subtitulo {{
            color: {PALETA["CINZA_TEXTO"]};
            font-size: 0.85rem;
            text-transform: uppercase;
            letter-spacing: 0.12em;
        }}
        </style>
        """,
        unsafe_allow_html=True,
    )


def cabecalho(subtitulo: str) -> None:
    st.markdown(
        f"""
        <div class="marca-db">
          <div class="titulo">Dutra Bitencourt</div>
          <div class="subtitulo">{subtitulo}</div>
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
        cabecalho("Painel de gestão · acesso restrito")
        auth.tela_de_login()
        return

    pagina = barra_lateral(usuario)
    dados = carregar(pagina)

    cabecalho(pagina)

    if pagina == "Visão geral":
        visao_geral.render(dados["prazos"], dados["clientes"])
    elif pagina == "Prazos":
        prazos.render(dados["prazos"])
    elif pagina == "Clientes":
        clientes.render(dados["clientes"])
    elif pagina == "Financeiro":
        financeiro.render(dados["clientes"])
    elif pagina == "Produção":
        producao.render(dados["prazos"], dados["logs"])
    elif pagina == "Histórico e auditoria":
        historico.render(dados["logs"], dados["ids"])


if __name__ == "__main__":
    main()
