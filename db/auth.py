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
    "Início",
    "Visão geral",
    "Prazos",
    "Clientes",
    "Clientes e processos",
    "Financeiro",
    "Resultados",
    "Produção",
    "Controladoria",
    "Logs e ciclos",
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
    # Início aparece para todo perfil: é só a porta de entrada, sem dados.
    return {
        "paginas": [p for p in PAGINAS_DISPONIVEIS if p in paginas or p == "Início"],
        "somente_proprios": bool(configuracao.get("somente_proprios", False)),
        "ver_financeiro": bool(configuracao.get("ver_financeiro", True)),
        "ver_editor": bool(configuracao.get("ver_editor", True)),
    }


def usuario_logado() -> dict | None:
    return st.session_state.get("usuario")


def regras_atuais() -> dict:
    usuario = usuario_logado()
    regras = regras_do_perfil(usuario["perfil"]) if usuario else dict(PERFIL_TOTAL)
    # Valores de honorários só aparecem com a área de gestão liberada,
    # inclusive nas páginas abertas (Visão geral, Clientes e processos).
    regras["ver_financeiro"] = regras["ver_financeiro"] and gestao_liberada()
    return regras


# ------------------------------------------------------ área de gestão
#
# Segunda senha, pedida só para as páginas de gestão. Quem entra com a
# senha geral vê prazos, controladoria e o Início; as páginas de gestão
# aparecem na barra com cadeado e pedem a SENHA_GESTAO na primeira vez.
# Liberada, a área fica aberta até a pessoa clicar em Sair.
#
# Secrets:
#   SENHA_GESTAO = "hash SHA-256 (gerar_hash.py)"
#   (as páginas livres ficam em PAGINAS_LIVRES, logo abaixo; o resto é gestão)
#
# Sem SENHA_GESTAO configurada, as páginas de gestão ficam fechadas para
# todos: é mais seguro falhar fechado do que abrir por esquecimento.
# No modo por usuário, o perfil com gestao = true entra sem a segunda senha.

# Únicas páginas abertas só com a senha de acesso. Todas as demais pedem
# a senha de gestão, inclusive páginas que forem criadas no futuro: a
# regra é por exclusão, para nenhuma página nova nascer desprotegida.
PAGINAS_LIVRES = ("Início", "Visão geral", "Prazos")
TENTATIVAS_MAXIMAS = 5
BLOQUEIO_MINUTOS = 5


def _segredo(nome: str) -> str:
    """
    Lê um valor dos Secrets. Procura primeiro no nível principal e, se não
    achar, dentro dos blocos [..]. No TOML, uma linha escrita abaixo de um
    cabeçalho como [sistemas] passa a pertencer àquele bloco, e esse é o
    erro mais comum ao colar uma senha nova no fim dos Secrets.
    """
    try:
        valor = st.secrets.get(nome)
        if valor:
            return str(valor).strip()
        for chave in st.secrets.keys():
            bloco = st.secrets.get(chave)
            if hasattr(bloco, "get") and bloco.get(nome):
                return str(bloco.get(nome)).strip()
    except Exception:  # noqa: BLE001
        pass
    return ""


def paginas_gestao() -> tuple:
    return tuple(p for p in PAGINAS_DISPONIVEIS if p not in PAGINAS_LIVRES)


def _perfil_com_gestao() -> bool:
    usuario = usuario_logado()
    if not usuario or modo_senha_unica():
        return False
    configuracao = dict(_perfis().get(usuario["perfil"], {}) or {})
    return bool(configuracao.get("gestao", False))


def gestao_liberada() -> bool:
    return bool(st.session_state.get("gestao_liberada")) or _perfil_com_gestao()


def pagina_protegida(pagina: str) -> bool:
    return pagina in paginas_gestao() and not gestao_liberada()


def tela_senha_gestao(pagina: str) -> None:
    """Formulário da senha de gestão, com limite de tentativas por sessão."""
    import time

    st.subheader(pagina)
    guardada = _segredo("SENHA_GESTAO")
    if not guardada:
        st.warning(
            "A senha de gestão não foi encontrada nos Secrets. Confira se a linha "
            "SENHA_GESTAO está escrita exatamente assim, com as aspas, e salve de novo."
        )
        return

    bloqueado_ate = st.session_state.get("gestao_bloqueio_ate", 0)
    if time.time() < bloqueado_ate:
        restante = int((bloqueado_ate - time.time()) // 60) + 1
        st.error(f"Muitas tentativas erradas. Tente de novo em {restante} min.")
        return

    _, centro, _ = st.columns([1, 1.2, 1])
    with centro:
        st.info("Área de gestão. Digite a senha de gestão para continuar.",
                icon=":material/lock:")
        with st.form("senha_gestao"):
            senha = st.text_input("Senha de gestão", type="password")
            entrar = st.form_submit_button("Liberar", width="stretch")
        if not entrar:
            return
        if _confere(senha, guardada):
            st.session_state["gestao_liberada"] = True
            st.session_state.pop("gestao_tentativas", None)
            st.rerun()
        tentativas = st.session_state.get("gestao_tentativas", 0) + 1
        st.session_state["gestao_tentativas"] = tentativas
        if tentativas >= TENTATIVAS_MAXIMAS:
            st.session_state["gestao_bloqueio_ate"] = time.time() + BLOQUEIO_MINUTOS * 60
            st.session_state["gestao_tentativas"] = 0
            st.error(f"Senha incorreta. Acesso bloqueado por {BLOQUEIO_MINUTOS} min.")
        else:
            st.error(f"Senha incorreta. Tentativa {tentativas} de {TENTATIVAS_MAXIMAS}.")


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
        st.session_state["pagina_atual"] = "Início"
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
