"""
Consulta ao Bitrix24, somente leitura.

Usa um webhook de entrada (Bitrix24 > Aplicações > Webhooks > Webhook
de entrada, escopo CRM). O endereço do webhook é credencial: ele vai
nos Secrets do Streamlit, bloco [bitrix], e nunca no repositório.

    [bitrix]
    webhook = "https://SEUDOMINIO.bitrix24.com.br/rest/ID_USUARIO/TOKEN/"

O painel só chama métodos de leitura (crm.deal.list, crm.category.list,
crm.status.list). Mesmo assim, o escopo CRM do webhook permite escrita:
quem tiver o endereço consegue alterar cards. Por isso o webhook deve
ser criado por um usuário com permissão de CRM apenas de leitura, e o
endereço nunca deve ser colado em conversa, planilha ou código.

Etapa: o card guarda STAGE_ID (código interno, como C3:PREPARATION).
O nome legível vem de crm.status.list, com ENTITY_ID DEAL_STAGE para o
funil geral e DEAL_STAGE_{id} para os demais funis.
"""

from __future__ import annotations

import re
import time

import pandas as pd
import requests
import streamlit as st

TIMEOUT = 20
LOTE = 50  # limite de registros por página da API

SEMANTICA = {"P": "EM ANDAMENTO", "S": "GANHO", "F": "PERDIDO"}


def configurado() -> bool:
    try:
        return bool(str(st.secrets["bitrix"]["webhook"]).strip())
    except Exception:  # noqa: BLE001
        return False


def _webhook() -> str:
    endereco = str(st.secrets["bitrix"]["webhook"]).strip()
    return endereco if endereco.endswith("/") else endereco + "/"


def _chamar(metodo: str, parametros: dict | None = None, tentativas: int = 4) -> dict:
    """
    Chamada REST com nova tentativa quando o Bitrix limita a taxa.

    O Bitrix aceita poucas requisições por segundo por portal e
    responde QUERY_LIMIT_EXCEEDED quando passa disso. A espera cresce a
    cada tentativa.
    """
    url = _webhook() + metodo + ".json"
    for tentativa in range(tentativas):
        try:
            resposta = requests.post(url, json=parametros or {}, timeout=TIMEOUT)
        except requests.RequestException as erro:
            if tentativa == tentativas - 1:
                raise RuntimeError(f"Bitrix fora do ar ou inacessível: {erro}") from erro
            time.sleep(1 + tentativa)
            continue

        try:
            dados = resposta.json()
        except ValueError:
            dados = {}

        codigo = dados.get("error")
        if codigo == "QUERY_LIMIT_EXCEEDED" or resposta.status_code == 503:
            time.sleep(1 + tentativa)
            continue
        if codigo or resposta.status_code >= 400:
            # A mensagem não inclui a URL, porque ela contém o token.
            raise RuntimeError(
                f"Bitrix recusou {metodo}: {codigo or resposta.status_code} "
                f"{dados.get('error_description', '')}".strip()
            )
        return dados
    raise RuntimeError(f"Bitrix limitou a taxa de chamadas em {metodo}. Tente em instantes.")


# ------------------------------------------------------------------ links


def id_do_negocio(link) -> str:
    """
    ID do negócio a partir do link do card.

    Aceita .../crm/deal/details/12345/ com ou sem barra final e com
    parâmetros. Link de lead, contato ou processo inteligente devolve
    vazio, porque a consulta aqui é só de negócios.
    """
    texto = str(link or "").strip().lower()
    achado = re.search(r"/crm/deal/details/(\d+)", texto)
    return achado.group(1) if achado else ""


# ------------------------------------------------------------- consultas


@st.cache_data(ttl=3600, show_spinner=False)
def etapas() -> pd.DataFrame:
    """Tabela de etapas de todos os funis de negócio: código -> nome."""
    funis = {0: "Funil geral"}
    try:
        resultado = _chamar("crm.category.list", {"entityTypeId": 2})
        for funil in resultado.get("result", {}).get("categories", []):
            funis[int(funil["id"])] = funil.get("name") or f"Funil {funil['id']}"
    except RuntimeError:
        pass  # sem permissão de ler funis, fica só o geral

    linhas = []
    for id_funil, nome_funil in funis.items():
        entidade = "DEAL_STAGE" if id_funil == 0 else f"DEAL_STAGE_{id_funil}"
        resultado = _chamar(
            "crm.status.list",
            {"filter": {"ENTITY_ID": entidade}, "order": {"SORT": "ASC"}},
        )
        for etapa in resultado.get("result", []):
            linhas.append(
                {
                    "stage_id": etapa["STATUS_ID"],
                    "etapa": etapa.get("NAME", etapa["STATUS_ID"]),
                    "ordem": int(etapa.get("SORT") or 0),
                    "id_funil": id_funil,
                    "funil": nome_funil,
                }
            )
    return pd.DataFrame(linhas)


@st.cache_data(ttl=600, show_spinner="Consultando os cards no Bitrix...")
def negocios(ids: tuple) -> pd.DataFrame:
    """
    Situação atual dos negócios informados, de 50 em 50.

    Recebe tupla para o cache funcionar. O resultado fica guardado por
    10 minutos; o botão Atualizar do painel limpa antes disso.
    """
    ids = [i for i in dict.fromkeys(ids) if i]
    registros = []
    for inicio in range(0, len(ids), LOTE):
        lote = ids[inicio:inicio + LOTE]
        resultado = _chamar(
            "crm.deal.list",
            {
                "filter": {"@ID": lote},
                "select": [
                    "ID", "TITLE", "STAGE_ID", "CATEGORY_ID", "STAGE_SEMANTIC_ID",
                    "CLOSED", "DATE_MODIFY", "MOVED_TIME", "ASSIGNED_BY_ID",
                ],
            },
        )
        registros.extend(resultado.get("result", []))

    if not registros:
        return pd.DataFrame(columns=["id_negocio"])

    dados = pd.DataFrame(registros).rename(
        columns={
            "ID": "id_negocio",
            "TITLE": "titulo_bitrix",
            "STAGE_ID": "stage_id",
            "CATEGORY_ID": "id_funil",
            "STAGE_SEMANTIC_ID": "semantica",
            "CLOSED": "fechado",
            "DATE_MODIFY": "modificado_bitrix",
            "MOVED_TIME": "movido_bitrix",
            "ASSIGNED_BY_ID": "responsavel_bitrix_id",
        }
    )
    for coluna in ("modificado_bitrix", "movido_bitrix"):
        if coluna in dados.columns:
            dados[coluna] = pd.to_datetime(
                dados[coluna], errors="coerce", utc=True
            ).dt.tz_convert("America/Sao_Paulo").dt.tz_localize(None)
        else:
            dados[coluna] = pd.NaT

    tabela = etapas()
    if not tabela.empty:
        dados = dados.merge(
            tabela[["stage_id", "etapa", "ordem", "funil"]], on="stage_id", how="left"
        )
    if "etapa" in dados.columns:
        dados["etapa"] = dados["etapa"].fillna(dados["stage_id"])
    else:
        dados["etapa"] = dados["stage_id"]
    for coluna in ("funil", "ordem"):
        if coluna not in dados.columns:
            dados[coluna] = pd.NA
    dados["situacao_bitrix"] = dados["semantica"].map(SEMANTICA).fillna("EM ANDAMENTO")
    return dados


def consultar(links: pd.Series) -> pd.DataFrame:
    """
    Recebe os links do controle de clientes e devolve, por link, a etapa
    atual do card. Link que não é de negócio ou que não foi encontrado
    (card apagado, sem permissão) aparece marcado, não some.
    """
    base = pd.DataFrame({"link_bitrix": links.astype(str)})
    base["id_negocio"] = base["link_bitrix"].map(id_do_negocio)
    ids = tuple(sorted(set(base["id_negocio"]) - {""}))
    encontrados = negocios(ids) if ids else pd.DataFrame(columns=["id_negocio"])

    encontrados = encontrados.copy()
    encontrados["id_negocio"] = encontrados["id_negocio"].astype(str)
    saida = base.merge(encontrados, on="id_negocio", how="left")
    for coluna in ("stage_id", "etapa", "funil", "ordem", "situacao_bitrix",
                   "modificado_bitrix", "movido_bitrix"):
        if coluna not in saida.columns:
            saida[coluna] = pd.NA
    saida["situacao_bitrix"] = saida["situacao_bitrix"].astype(object)
    saida.loc[saida["id_negocio"] == "", "situacao_bitrix"] = "LINK NÃO É DE NEGÓCIO"
    saida.loc[
        (saida["id_negocio"] != "") & saida["stage_id"].isna(), "situacao_bitrix"
    ] = "NÃO ENCONTRADO NO BITRIX"
    saida.loc[saida["link_bitrix"].str.strip().isin(["", "nan", "None"]),
              "situacao_bitrix"] = "SEM LINK"
    return saida


def limpar_cache() -> None:
    etapas.clear()
    negocios.clear()
