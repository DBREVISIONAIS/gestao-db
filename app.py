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
