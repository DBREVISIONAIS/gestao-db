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

from __future__ import annotations

import hashlib
import hmac

import pandas as pd
import streamlit as st

PAGINAS_DISPONIVEIS = (
    "Visão geral",
    "Prazos",
    "Clientes",
    "Financeiro",
    "Resultados",
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
