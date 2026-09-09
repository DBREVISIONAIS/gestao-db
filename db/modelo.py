"""
Modelo de dados do painel.

Cada funcao le uma aba e devolve um DataFrame ja derivado. O mapeamento
de colunas reproduz obterContextoLinha_ do Apps Script na versao V12.3,
inclusive os aliases de compatibilidade (FATAL / PRAZO FATAL) e o
tratamento das duas colunas RESPONSAVEL do CONTROLE DE PRAZOS.
"""

from __future__ import annotations

from datetime import datetime

import numpy as np
import pandas as pd
import streamlit as st

from db import conexao
from db.normalizacao import (
    classificar_resultado_sentenca,
    extrair_resultados_processuais,
    classificar_tipo_prazo,
    converter_data,
    converter_numero,
    converter_percentual,
    diferenca_dias,
    inicio_do_dia,
    mapa_cabecalhos,
    normalizar_texto,
    primeiro_preenchido,
    valor_por_cabecalho,
    valor_por_cabecalho_parcial,
    valor_preenchido,
)

# Abas da planilha auxiliar. BI_PRAZOS e BI_CLIENTES sao espelhos
# criados por IMPORTRANGE e mantem os mesmos cabecalhos da origem, de
# modo que o mapeamento de colunas abaixo continua valendo.
ABA_PRAZOS = "BI_PRAZOS"
ABA_CLIENTES = "BI_CLIENTES"
ABA_LOG = "LOG_ALTERACOES"
ABA_BASE_IDS = "BI_BASE_IDS"
ABA_CONTROLE = "CONTROLE_BI"

STATUS_ENCERRADOS_PRAZOS = {"PROTOCOLADO", "CONCLUIDO", "OK"}
STATUS_ENCERRADOS_CLIENTES = {"PROTOCOLADO", "DESCARTADO", "CONCLUIDO"}


# --------------------------------------------------------------- prazos


def padronizar_status(status) -> str:
    """
    Reduz o status livre da planilha aos rotulos do dashboard.

    A ordem dos testes importa: PROTOCOLADO antes de PROTOCOLAR, senao
    "PARA PROTOCOLAR" seria lido como ja protocolado.
    """
    texto = normalizar_texto(status)
    if not texto:
        return "SEM STATUS"
    if "PROTOCOLADO" in texto or "CONCLUIDO" in texto:
        return "PROTOCOLADO"
    if "PROTOCOLAR" in texto:
        return "PARA PROTOCOLAR"
    if "REVIS" in texto:
        return "PARA REVISAR"
    if "AGUARD" in texto:
        return "AGUARDANDO"
    if "PENDENTE" in texto:
        return "PENDENTE"
    return texto


def _situacao_prazo(data_controle, encerrado: bool, hoje) -> tuple[str, int | None]:
    """
    Faixas iguais as do DASH PRAZOS, calculadas sobre a data de
    controle, que e o Prazo Fatal e, quando ele nao for valido, a
    Data Final.
    """
    dias = diferenca_dias(hoje, data_controle)
    if dias is None:
        return "SEM DATA DE CONTROLE", None
    if encerrado:
        return "ENCERRADO", dias
    if dias < 0:
        return "VENCIDO", dias
    if dias == 0:
        return "VENCE HOJE", dias
    if dias <= 7:
        return "PRÓXIMOS 7 DIAS", dias
    if dias <= 15:
        return "DE 8 A 15 DIAS", dias
    return "APÓS 15 DIAS", dias


@st.cache_data(ttl=conexao.TTL_CACHE, show_spinner="Lendo o controle de prazos...")
def carregar_prazos() -> pd.DataFrame:
    matriz = conexao.ler_aba(conexao.id_planilha_auxiliar(), ABA_PRAZOS)
    if len(matriz) < 2:
        return pd.DataFrame()

    mapa = mapa_cabecalhos(matriz[0])
    hoje = inicio_do_dia(datetime.now())
    registros = []

    for indice, linha in enumerate(matriz[1:], start=2):
        autor = valor_por_cabecalho(linha, mapa, ["AUTOR", "CLIENTE"])
        data_evento = valor_por_cabecalho(linha, mapa, ["DATA EVENTO"])
        data_final = valor_por_cabecalho(linha, mapa, ["DATA FINAL"])
        # V12.3: a estrutura atual usa FATAL. Os demais sao compatibilidade.
        prazo_fatal = valor_por_cabecalho(
            linha, mapa, ["FATAL", "PRAZO FATAL", "DATA FATAL"]
        )
        observacao = valor_por_cabecalho(linha, mapa, ["OBSERVACAO"])
        conteudo = primeiro_preenchido(
            [valor_por_cabecalho(linha, mapa, ["CONTEUDO DO PRAZO"]), observacao]
        )
        status = valor_por_cabecalho(linha, mapa, ["STATUS"])

        if not any(
            valor_preenchido(v)
            for v in (autor, data_evento, data_final, prazo_fatal, conteudo, status)
        ):
            continue

        # Primeira ocorrencia de RESPONSAVEL e o tecnico, a ultima e a
        # controladoria. Mesma regra do obterContextoLinha_.
        responsavel = valor_por_cabecalho(linha, mapa, ["RESPONSAVEL"], ocorrencia=0)
        indices_resp = mapa.get(normalizar_texto("RESPONSAVEL"), [])
        responsavel_controladoria = valor_por_cabecalho(
            linha, mapa, ["RESPONSAVEL CONTROLADORIA", "RESPONSAVEL DA CONTROLADORIA"]
        )
        if not valor_preenchido(responsavel_controladoria) and len(indices_resp) > 1:
            posicao = indices_resp[-1]
            responsavel_controladoria = linha[posicao] if posicao < len(linha) else ""

        encerrado = normalizar_texto(status) in STATUS_ENCERRADOS_PRAZOS

        # Data de controle: Prazo Fatal e, na ausencia dele, Data Final.
        # Mesma regra do DASH PRAZOS. Sem esse fallback, milhares de
        # registros ficariam fora da contagem operacional.
        fatal = converter_data(prazo_fatal)
        final = converter_data(data_final)

        # O campo FATAL nem sempre traz data. Ele também recebe texto,
        # e AGUARDA é uma situação declarada de propósito, diferente de
        # campo em branco. Separar as duas coisas importa: AGUARDA é
        # decisão registrada, campo vazio é pendência de preenchimento.
        texto_fatal = normalizar_texto(prazo_fatal)
        if pd.notna(fatal):
            situacao_fatal = "COM FATAL"
        elif "AGUARDA" in texto_fatal:
            situacao_fatal = "AGUARDA"
        elif not texto_fatal:
            situacao_fatal = "FATAL VAZIO"
        else:
            situacao_fatal = "FATAL INVÁLIDO"

        if pd.notna(fatal):
            data_controle, fonte_data = fatal, "PRAZO FATAL"
        elif pd.notna(final):
            data_controle, fonte_data = final, "DATA FINAL"
        else:
            data_controle, fonte_data = pd.NaT, "SEM DATA"

        situacao, dias_para_fatal = _situacao_prazo(data_controle, encerrado, hoje)
        resultados = extrair_resultados_processuais(conteudo, observacao)
        data_referencia = converter_data(
            primeiro_preenchido([data_evento, prazo_fatal, data_final])
        )

        registros.append(
            {
                "linha_origem": indice,
                "autor": str(autor).strip(),
                "link_bitrix": valor_por_cabecalho(linha, mapa, ["LINK BITRIX"]),
                "data_evento": converter_data(data_evento),
                "prazo_texto": valor_por_cabecalho(linha, mapa, ["PRAZO"]),
                "data_final": converter_data(data_final),
                "prazo_fatal": converter_data(prazo_fatal),
                "conteudo": str(conteudo).strip(),
                "observacao": str(observacao).strip(),
                "responsavel": str(responsavel).strip() or "SEM RESPONSÁVEL",
                "delegado": str(valor_por_cabecalho(linha, mapa, ["DELEGADO"])).strip(),
                "responsavel_controladoria": str(responsavel_controladoria).strip(),
                "status": str(status).strip().upper() or "SEM STATUS",
                "verificacao_controladoria": valor_por_cabecalho(
                    linha, mapa, ["VERIFICACAO CONTROLADORIA"]
                ),
                "status_padronizado": padronizar_status(status),
                "tipo_prazo": classificar_tipo_prazo(conteudo),
                "fatal_texto": str(prazo_fatal).strip(),
                "situacao_fatal": situacao_fatal,
                "data_controle": data_controle,
                "fonte_data": fonte_data,
                "situacao": situacao,
                "dias_para_fatal": dias_para_fatal,
                "dias_evento_fatal": diferenca_dias(data_evento, prazo_fatal),
                "encerrado": encerrado,
                "resultados": resultados,
                "resumo_resultados": " | ".join(
                    f"{r['categoria']}: {r['resultado']}" for r in resultados
                ),
                "motivo_resultado": " | ".join(
                    sorted(
                        {
                            r["motivo"]
                            for r in resultados
                            if r["motivo"] != "NÃO REGISTRADO NO CONTROLE"
                        }
                    )
                ),
                "data_referencia": data_referencia,
            }
        )

    dados = pd.DataFrame(registros)
    if dados.empty:
        return dados

    dados["ano"] = dados["data_referencia"].dt.year
    dados["mes"] = dados["data_referencia"].dt.month
    dados["ano_fatal"] = dados["prazo_fatal"].dt.year
    dados["mes_fatal"] = dados["prazo_fatal"].dt.month
    dados["competencia_controle"] = (
        dados["data_controle"].dt.to_period("M").astype(str).replace("NaT", "")
    )
    return dados


def resultados_processuais(prazos: pd.DataFrame) -> pd.DataFrame:
    """
    Achata a lista de resultados de cada prazo em uma linha por
    resultado. Espelha achatarResultadosProcessuais_ do Apps Script.
    """
    if prazos.empty or "resultados" not in prazos.columns:
        return pd.DataFrame()

    linhas = []
    for _, prazo in prazos.iterrows():
        for resultado in prazo["resultados"] or []:
            linhas.append(
                {
                    "data_evento": prazo["data_evento"],
                    "autor": prazo["autor"],
                    "responsavel": prazo["responsavel"],
                    "categoria": resultado["categoria"],
                    "resultado": resultado["resultado"],
                    "motivo": resultado["motivo"],
                    "conteudo": resultado["texto"],
                    "status": prazo["status"],
                    "linha_origem": prazo["linha_origem"],
                }
            )

    dados = pd.DataFrame(linhas)
    if dados.empty:
        return dados
    dados["competencia"] = (
        dados["data_evento"].dt.to_period("M").astype(str).replace("NaT", "")
    )
    return dados.sort_values("data_evento", ascending=False, na_position="last")


# -------------------------------------------------------------- clientes


@st.cache_data(ttl=conexao.TTL_CACHE, show_spinner="Lendo o controle de clientes...")
def carregar_clientes() -> pd.DataFrame:
    matriz = conexao.ler_aba(conexao.id_planilha_auxiliar(), ABA_CLIENTES)
    if len(matriz) < 2:
        return pd.DataFrame()

    mapa = mapa_cabecalhos(matriz[0])
    registros = []

    for indice, linha in enumerate(matriz[1:], start=2):
        cliente = valor_por_cabecalho(linha, mapa, ["CLIENTE", "AUTOR"])
        status = valor_por_cabecalho(linha, mapa, ["STATUS"])
        servico = valor_por_cabecalho(linha, mapa, ["SERVICO"])
        data_contrato = valor_por_cabecalho(linha, mapa, ["DATA CONTRATO"])
        data_ajuizamento = valor_por_cabecalho(linha, mapa, ["DATA AJUIZAMENTO"])

        if not any(
            valor_preenchido(v)
            for v in (cliente, status, servico, data_contrato, data_ajuizamento)
        ):
            continue

        honorario_previsto = converter_numero(
            valor_por_cabecalho(linha, mapa, ["HONORARIO PREVISTO"])
        )
        honorario_sucumbencial = converter_numero(
            valor_por_cabecalho(linha, mapa, ["HONORARIO SUCUMBENCIAL PREVISTO"])
        )
        valor_ajuizado = converter_numero(
            valor_por_cabecalho(linha, mapa, ["VALOR AJUIZADO CONSOLIDADO"])
        )
        calculo_real = primeiro_preenchido(
            [
                valor_por_cabecalho_parcial(linha, mapa, "RT CONFIRMADA CONTABILISTA"),
                valor_por_cabecalho_parcial(linha, mapa, "CALCULO REAL"),
            ]
        )
        sentenca = valor_por_cabecalho(linha, mapa, ["SENTENCA PROCEDENTE"])
        data_referencia = converter_data(
            primeiro_preenchido([data_ajuizamento, data_contrato])
        )

        registros.append(
            {
                "linha_origem": indice,
                "cliente": str(cliente).strip(),
                "status": str(status).strip().upper() or "SEM STATUS",
                "servico": str(servico).strip().upper() or "NÃO INFORMADO",
                "responsavel": str(
                    valor_por_cabecalho(linha, mapa, ["RESPONSAVEL"])
                ).strip()
                or "SEM RESPONSÁVEL",
                "link_bitrix": valor_por_cabecalho(linha, mapa, ["LINK BITRIX"]),
                "data_contrato": converter_data(data_contrato),
                "data_ajuizamento": converter_data(data_ajuizamento),
                "calculo_real": calculo_real,
                "percentual_honorarios": converter_percentual(
                    valor_por_cabecalho(
                        linha, mapa, ["% HONORARIOS", "PERCENTUAL HONORARIOS"]
                    )
                ),
                "numero_parcelas": converter_numero(
                    valor_por_cabecalho(
                        linha, mapa, ["N DE PARCELAS", "NUMERO DE PARCELAS"]
                    )
                ),
                "valor_parcela_irrf": converter_numero(
                    valor_por_cabecalho(
                        linha, mapa, ["VALOR PARCELA IRRF", "VALOR PARCELA"]
                    )
                ),
                "honorario_previsto": honorario_previsto,
                "honorario_sucumbencial": honorario_sucumbencial,
                "honorario_total": honorario_previsto + honorario_sucumbencial,
                "valor_ajuizado": valor_ajuizado,
                "tribunal": valor_por_cabecalho(linha, mapa, ["TRIBUNAL AJUIZAMENTO"]),
                "liminar": valor_por_cabecalho_parcial(linha, mapa, "LIMINAR CONCEDIDA"),
                "data_primeiro_faturamento": converter_data(
                    valor_por_cabecalho(
                        linha, mapa, ["DATA 1 FATURAMENTO", "DATA PRIMEIRO FATURAMENTO"]
                    )
                ),
                "sentenca_procedente": sentenca,
                "resultado_sentenca": classificar_resultado_sentenca(sentenca),
                "data_sentenca": converter_data(
                    valor_por_cabecalho(linha, mapa, ["DATA SENTENCA"])
                ),
                "data_transito": converter_data(
                    valor_por_cabecalho_parcial(linha, mapa, "DATA TJ PROC CONHECIMENTO")
                ),
                "data_faturamento_restituicao": converter_data(
                    valor_por_cabecalho_parcial(
                        linha, mapa, "DATA FATURAMENTO RESTITUICAO"
                    )
                ),
                "data_conclusao_execucao": converter_data(
                    valor_por_cabecalho_parcial(linha, mapa, "DATA CONCLUSAO EXECUCAO")
                ),
                "diagnostico": valor_por_cabecalho(linha, mapa, ["DIAGNOSTICO"]),
                "comentario": valor_por_cabecalho(linha, mapa, ["COMENTARIO"]),
                "dias_contrato_ajuizamento": diferenca_dias(
                    data_contrato, data_ajuizamento
                ),
                "encerrado": normalizar_texto(status) in STATUS_ENCERRADOS_CLIENTES,
                "ajuizado": pd.notna(converter_data(data_ajuizamento)),
                "possui_calculo": valor_preenchido(calculo_real) or valor_ajuizado != 0,
                "data_referencia": data_referencia,
            }
        )

    dados = pd.DataFrame(registros)
    if dados.empty:
        return dados

    dados["ano"] = dados["data_referencia"].dt.year
    dados["mes"] = dados["data_referencia"].dt.month
    dados["ano_ajuizamento"] = dados["data_ajuizamento"].dt.year
    dados["mes_ajuizamento"] = dados["data_ajuizamento"].dt.month
    dados["competencia_ajuizamento"] = (
        dados["data_ajuizamento"].dt.to_period("M").astype(str).replace("NaT", "")
    )
    return dados


# ------------------------------------------------------------------ log

COLUNAS_LOG = {
    "ID": "id",
    "DATA_HORA": "data_hora",
    "DATA": "data",
    "HORA": "hora",
    "ABA_ORIGEM": "aba_origem",
    "LINHA": "linha",
    "COLUNA": "coluna",
    "CABECALHO": "cabecalho",
    "VALOR_ANTERIOR": "valor_anterior",
    "VALOR_NOVO": "valor_novo",
    "TIPO_EVENTO": "tipo_evento",
    "CLIENTE_AUTOR": "cliente_autor",
    "LINK_BITRIX": "link_bitrix",
    "RESPONSAVEL_TECNICO": "responsavel_tecnico",
    "RESPONSAVEL_CONTROLADORIA": "responsavel_controladoria",
    "STATUS_ATUAL": "status_atual",
    "DATA_EVENTO": "data_evento",
    "DATA_FINAL": "data_final",
    "PRAZO_FATAL": "prazo_fatal",
    "CONTEUDO_PRAZO_SERVICO": "conteudo",
    "EDITOR_EMAIL": "editor_email",
    "CHAVE_EDITOR": "chave_editor",
    "OBSERVACAO": "observacao",
}

# Colunas acrescentadas pela V11 do Apps Script. Podem nao existir em
# instalacoes antigas, entao a ausencia nao e erro.
COLUNAS_LOG_OPCIONAIS = {
    "ID_CLIENTE": "id_cliente",
    "ID_PRAZO": "id_prazo",
    "VINCULO": "vinculo",
}


EMAIL_INDISPONIVEL = "E-MAIL NÃO DISPONIBILIZADO PELO GOOGLE"


def _nome_do_editor(email: str, chave: str) -> str:
    """
    Nome de exibição do editor.

    Prioriza o e-mail, porque CHAVE_EDITOR vem de
    Session.getTemporaryActiveUserKey(), que é anônima e rotaciona: a
    mesma pessoa vira várias chaves ao longo do tempo, o que inutiliza
    qualquer contagem por editor.

    O bloco [editores] dos Secrets permite mapear e-mail para nome
    próprio. Sem ele, usa a parte antes do @, que já costuma bastar.
    """
    email = str(email or "").strip()

    if email and "@" in email and email != EMAIL_INDISPONIVEL:
        try:
            mapa = dict(st.secrets.get("editores", {}))
        except Exception:  # noqa: BLE001
            mapa = {}

        for chave_secreta, nome in mapa.items():
            if chave_secreta.strip().lower() == email.lower():
                return str(nome)

        local = email.split("@")[0]
        return local.replace(".", " ").replace("_", " ").title()

    return "NÃO IDENTIFICADO"


@st.cache_data(ttl=conexao.TTL_CACHE, show_spinner="Lendo o histórico de alterações...")
def carregar_logs() -> pd.DataFrame:
    # A aba pode ainda nao existir, quando o patch do Apps Script que
    # redireciona o log nao foi instalado. Nesse caso o painel segue
    # funcionando sem historico, em vez de travar a tela inteira.
    try:
        matriz = conexao.ler_aba_recente(
            conexao.id_planilha_logs(), ABA_LOG, conexao.limite_log()
        )
    except RuntimeError:
        return pd.DataFrame()

    if len(matriz) < 2:
        return pd.DataFrame()

    mapa = mapa_cabecalhos(matriz[0])
    registros = []

    for linha in matriz[1:]:
        registro = {}
        for cabecalho, campo in COLUNAS_LOG.items():
            registro[campo] = valor_por_cabecalho(linha, mapa, [cabecalho])
        for cabecalho, campo in COLUNAS_LOG_OPCIONAIS.items():
            registro[campo] = valor_por_cabecalho(
                linha, mapa, [cabecalho, cabecalho.replace("_", " ")]
            )
        if not valor_preenchido(registro.get("data_hora")) and not valor_preenchido(
            registro.get("aba_origem")
        ):
            continue
        registros.append(registro)

    dados = pd.DataFrame(registros)
    if dados.empty:
        return dados

    dados["data_hora"] = dados["data_hora"].apply(converter_data)
    dados["linha"] = pd.to_numeric(dados["linha"], errors="coerce")
    dados = dados.sort_values("data_hora", ascending=False, na_position="last")
    # Identidade do editor: e-mail sempre que existir. A chave anônima
    # fica guardada só para conferência pontual de uma linha.
    dados["editor"] = [
        _nome_do_editor(email, chave)
        for email, chave in zip(dados["editor_email"], dados["chave_editor"])
    ]
    # Chave de agrupamento do histórico: o ID permanente quando existe,
    # senão o nome. O ID é mais seguro porque sobrevive a mudança de
    # nome e a reordenação de linhas.
    dados["chave_registro"] = [
        str(ident).strip() if str(ident).strip() else normalizar_texto(nome)
        for ident, nome in zip(dados["id_cliente"], dados["cliente_autor"])
    ]
    dados["hora"] = dados["data_hora"].dt.strftime("%H") + "h"
    dados["dia_semana"] = dados["data_hora"].dt.dayofweek
    dados["chave_origem"] = (
        dados["aba_origem"].astype(str).str.strip()
        + "|"
        + dados["linha"].fillna(0).astype(int).astype(str)
    )
    return dados.reset_index(drop=True)


@st.cache_data(ttl=conexao.TTL_CACHE, show_spinner=False)
def carregar_base_ids() -> pd.DataFrame:
    """
    Le a BASE IDS espelhada, se ela existir.

    E opcional: a BASE IDS continua vivendo na planilha principal e so
    aparece no painel se voce criar um espelho BI_BASE_IDS na auxiliar.
    A ausencia nao e tratada como erro.
    """
    try:
        matriz = conexao.ler_aba(conexao.id_planilha_auxiliar(), ABA_BASE_IDS)
    except RuntimeError:
        return pd.DataFrame()

    if len(matriz) < 2:
        return pd.DataFrame()

    colunas = [normalizar_texto(c).replace(" ", "_").lower() for c in matriz[0]]
    dados = pd.DataFrame(matriz[1:], columns=colunas)
    return dados[dados.iloc[:, 0].astype(str).str.strip() != ""].reset_index(drop=True)


# ------------------------------------------------- estado do espelho


@st.cache_data(ttl=60, show_spinner=False)
def estado_do_espelho() -> dict:
    """
    Le a aba CONTROLE_BI, escrita pelo Apps Script da auxiliar a cada
    atualizacao forcada do IMPORTRANGE.

    Serve para o painel mostrar a idade real do dado. Sem isso, o
    espelho poderia estar defasado e o usuario nao teria como saber.
    """
    try:
        matriz = conexao.ler_aba(conexao.id_planilha_auxiliar(), ABA_CONTROLE)
    except RuntimeError:
        return {}

    estado = {}
    for linha in matriz[1:]:
        if len(linha) >= 2 and str(linha[0]).strip():
            estado[str(linha[0]).strip()] = str(linha[1]).strip()
    return estado


def minutos_desde_atualizacao(estado: dict) -> float | None:
    """Idade do espelho em minutos, ou None se nao houver registro."""
    momento = converter_data(estado.get("ATUALIZADO_EM"))
    if pd.isna(momento):
        return None
    return (datetime.now() - momento).total_seconds() / 60.0


# ------------------------------------------------- análise do histórico

DIAS_SEMANA = (
    "Segunda", "Terça", "Quarta", "Quinta", "Sexta", "Sábado", "Domingo",
)


def _dias_uteis(inicio, fim) -> float | None:
    """Dias úteis entre duas datas, sem considerar feriados."""
    if pd.isna(inicio) or pd.isna(fim):
        return None
    return float(
        np.busday_count(
            pd.Timestamp(inicio).date(), pd.Timestamp(fim).date()
        )
    )


def transicoes_status(logs: pd.DataFrame) -> pd.DataFrame:
    """
    Extrai as mudanças efetivas de status a partir do log.

    Considera apenas linhas cujo cabeçalho editado é STATUS e em que o
    valor mudou de fato. Alteração de outros campos não é transição.
    """
    if logs.empty:
        return pd.DataFrame()

    base = logs[
        logs["cabecalho"].astype(str).str.upper().str.strip().eq("STATUS")
    ].copy()
    if base.empty:
        return base

    base["de"] = (
        base["valor_anterior"].astype(str).str.strip().str.upper().replace("", "SEM STATUS")
    )
    base["para"] = base["valor_novo"].astype(str).str.strip().str.upper()
    base = base[(base["para"] != "") & (base["de"] != base["para"])]
    return base.sort_values("data_hora")


def permanencia_por_etapa(transicoes: pd.DataFrame) -> pd.DataFrame:
    """
    Tempo de permanência em cada etapa, por registro.

    A entrada na etapa é a transição que leva a ela; a saída é a
    transição seguinte do mesmo registro. Ciclos sem saída são os
    casos que ainda estão na etapa e ficam de fora da média de ciclos
    completos, exatamente como no DASH LOGS.
    """
    if transicoes.empty:
        return pd.DataFrame()

    linhas = []
    for chave, grupo in transicoes.groupby("chave_registro"):
        grupo = grupo.sort_values("data_hora")
        registros = grupo.to_dict("records")
        for indice, atual in enumerate(registros):
            seguinte = registros[indice + 1] if indice + 1 < len(registros) else None
            entrada = atual["data_hora"]
            saida = seguinte["data_hora"] if seguinte else pd.NaT
            corridos = (
                (saida - entrada).total_seconds() / 86400 if pd.notna(saida) else None
            )
            linhas.append(
                {
                    "chave_registro": chave,
                    "cliente": atual["cliente_autor"],
                    "etapa": atual["para"],
                    "entrada": entrada,
                    "saida": saida,
                    "dias_corridos": round(corridos, 1) if corridos is not None else None,
                    "dias_uteis": _dias_uteis(entrada, saida),
                    "editor_entrada": atual["editor"],
                    "concluido": seguinte is not None,
                }
            )

    return pd.DataFrame(linhas)


def ciclos_do_cliente(
    clientes: pd.DataFrame, transicoes: pd.DataFrame
) -> pd.DataFrame:
    """
    Tempo entre contrato, minuta e protocolo, por cliente.

    O marco de minuta é a primeira transição para MINUTA. O de
    protocolo é a Data do Ajuizamento quando existir e, na falta dela,
    a primeira transição para PROTOCOLADO. Casos sem marco entram como
    pendência de informação, e não como atraso.
    """
    if clientes.empty:
        return pd.DataFrame()

    marcos: dict[str, dict] = {}
    if not transicoes.empty:
        for chave, grupo in transicoes.groupby("chave_registro"):
            grupo = grupo.sort_values("data_hora")
            minuta = grupo[grupo["para"].str.contains("MINUTA", na=False)]
            protocolo = grupo[grupo["para"].str.contains("PROTOCOLADO", na=False)]
            pronta = grupo[
                grupo["para"].str.contains("PROTOCOLAR|REVIS", na=False, regex=True)
            ]
            marcos[chave] = {
                "minuta": minuta["data_hora"].min() if not minuta.empty else pd.NaT,
                "pronta": pronta["data_hora"].min() if not pronta.empty else pd.NaT,
                "protocolo": (
                    protocolo["data_hora"].min() if not protocolo.empty else pd.NaT
                ),
            }

    linhas = []
    for _, cliente in clientes.iterrows():
        chave = normalizar_texto(cliente["cliente"])
        marco = marcos.get(chave, {})
        contrato = cliente["data_contrato"]
        protocolo = (
            cliente["data_ajuizamento"]
            if pd.notna(cliente["data_ajuizamento"])
            else marco.get("protocolo", pd.NaT)
        )
        minuta = marco.get("minuta", pd.NaT)
        pronta = marco.get("pronta", pd.NaT)

        def corridos(inicio, fim):
            if pd.isna(inicio) or pd.isna(fim):
                return None
            return round((pd.Timestamp(fim) - pd.Timestamp(inicio)).days, 1)

        linhas.append(
            {
                "cliente": cliente["cliente"],
                "servico": cliente["servico"],
                "responsavel": cliente["responsavel"],
                "status": cliente["status"],
                "contrato": contrato,
                "minuta": minuta,
                "pronta": pronta,
                "protocolo": protocolo,
                "contrato_minuta": corridos(contrato, minuta),
                "contrato_protocolo": corridos(contrato, protocolo),
                "pronta_protocolo": corridos(pronta, protocolo),
                "contrato_minuta_uteis": _dias_uteis(contrato, minuta),
                "contrato_protocolo_uteis": _dias_uteis(contrato, protocolo),
            }
        )

    return pd.DataFrame(linhas)
