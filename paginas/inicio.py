"""
Início: porta de entrada para os sistemas do escritório.

Aparece em dois lugares: na tela de login, abaixo do formulário, e como
primeira página depois de entrar. Os sistemas externos abrem em outra
aba; cada um tem a própria senha, então o link não dá acesso sozinho.

Os endereços podem ser trocados nos Secrets sem mexer no código:

    [sistemas]
    bancario = "https://manual-bancario.streamlit.app/"
    isencao = "https://manual-isencao-db.streamlit.app/"
"""

from __future__ import annotations

import streamlit as st

PAGINA_INTERNA = "Visão geral"


def _endereco(chave: str, padrao: str) -> str:
    try:
        return str(st.secrets.get("sistemas", {}).get(chave) or padrao)
    except Exception:  # noqa: BLE001 - secrets indisponível
        return padrao


def sistemas() -> list[dict]:
    return [
        {
            "chave": "gestao",
            "icone": ":material/monitoring:",
            "titulo": "Gestão e controle de prazos",
            "texto": "Painel de gestão: prazos, clientes, financeiro, resultados "
                     "e controladoria.",
            "url": None,  # este próprio aplicativo
        },
        {
            "chave": "bancario",
            "icone": ":material/account_balance:",
            "titulo": "Núcleo Bancário",
            "texto": "Visão geral do núcleo bancário, manual de procedimentos e "
                     "painel dos processos.",
            "url": _endereco("bancario", "https://manual-bancario.streamlit.app/"),
        },
        {
            "chave": "isencao",
            "icone": ":material/menu_book:",
            "titulo": "Manual da Isenção",
            "texto": "Manual do núcleo de isenção de imposto de renda por doença grave.",
            "url": _endereco("isencao", "https://manual-isencao-db.streamlit.app/"),
        },
    ]


def _ir_para_gestao() -> None:
    # Callback: roda antes do próximo ciclo, quando ainda é permitido
    # trocar o valor da barra de páginas.
    st.session_state["pagina_atual"] = PAGINA_INTERNA


def cartoes(logado: bool) -> None:
    """
    Um cartão por sistema. Logado, o cartão da gestão leva para a Visão
    geral; na tela de login, ele só indica que o acesso é pelo formulário.
    """
    colunas = st.columns(3)
    for coluna, sistema in zip(colunas, sistemas()):
        with coluna, st.container(border=True, height="stretch"):
            st.markdown(f"**{sistema['titulo']}**")
            st.caption(sistema["texto"])
            if sistema["url"]:
                st.link_button(
                    "Abrir", sistema["url"], icon=":material/open_in_new:",
                    width="stretch",
                )
            elif logado:
                st.button(
                    "Abrir", icon=sistema["icone"], width="stretch",
                    key=f"inicio_{sistema['chave']}", on_click=_ir_para_gestao,
                )
            else:
                st.button(
                    "Entre com sua senha acima", icon=":material/lock:",
                    width="stretch", disabled=True, key=f"login_{sistema['chave']}",
                )


def render(usuario: dict) -> None:
    primeiro_nome = str(usuario.get("nome", "")).split(" ")[0]
    st.subheader(f"Olá, {primeiro_nome}" if primeiro_nome else "Início")
    st.caption(
        "Escolha o sistema. Os sistemas externos abrem em outra aba e pedem a "
        "senha própria de cada um."
    )
    cartoes(logado=True)
