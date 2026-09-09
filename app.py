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
# Azul extraído do próprio logotipo, para a faixa e a arte casarem sem
# emenda visível.
AZUL_LOGO = "#00315F"

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


# Logotipo do escritório embutido em base64. Fica no próprio arquivo
# para não depender de hospedagem externa nem de pasta no repositório.
LOGO_BASE64 = (
    "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"
)


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

        /* O cabeçalho padrão do Streamlit é uma faixa branca fixa que
           cobria o topo da faixa institucional. Fica transparente e sem
           altura própria; os botões do canto continuam clicáveis. */
        header[data-testid="stHeader"] {{
            background: transparent;
            height: 0;
        }}
        header[data-testid="stHeader"] * {{ color: {AZUL_LOGO}; }}
        div[data-testid="stDecoration"] {{ display: none; }}

        .block-container {{
            padding-top: 3.2rem;
            padding-bottom: 3rem;
            max-width: 1500px;
        }}

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
            background: {AZUL_LOGO};
            border-radius: 6px;
            padding: 14px 24px;
            display: flex;
            align-items: center;
            justify-content: space-between;
            border-bottom: 3px solid {PALETA["AMARELO"]};
            margin: 0 0 22px 0;
        }}
        .timbre img {{
            height: 46px;
            display: block;
        }}
        .timbre .contexto {{
            color: #C9D6E6;
            font-size: 0.78rem;
            text-align: right;
            letter-spacing: 0.14em;
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
          <img src="data:image/png;base64,{LOGO_BASE64}" alt="Dutra Bitencourt" />
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
