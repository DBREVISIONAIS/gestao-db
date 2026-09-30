"""
Financeiro.

Todos os valores exibidos aqui sao previsoes lancadas no CONTROLE DE
CLIENTES, e nao valores efetivamente recebidos. O rotulo deixa isso
explicito para nao gerar leitura equivocada de faturamento.

O realizado usa a Data do Ajuizamento, que e o marco de protocolo
adotado pelo escritorio. Cliente com valor mas sem ajuizamento entra no
pipeline, nunca no realizado.
"""

from __future__ import annotations

from datetime import date

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from db import auth, modelo
from db.normalizacao import formatar_moeda, normalizar_texto
from paginas import componentes as ui


def render(clientes: pd.DataFrame) -> None:
    st.subheader("Financeiro")
    st.caption(
        "Valores previstos, conforme lançamento no controle de clientes. "
        "Não representam valores recebidos."
    )

    if not auth.regras_atuais()["ver_financeiro"]:
        st.warning("Seu perfil não tem acesso aos dados financeiros.")
        return

    clientes = auth.aplicar_recorte(clientes)
    if clientes.empty:
        st.info("Nenhum cliente disponível.")
        return

    metas(clientes)
    st.divider()
    painel(clientes)
    st.divider()
    metas_e_projecoes(clientes)


# ------------------------------------------------------------- metas


@st.fragment
def metas(clientes: pd.DataFrame) -> None:
    st.markdown("### Meta")

    anos = sorted(
        {int(a) for a in clientes["ano_ajuizamento"].dropna().unique()}, reverse=True
    )
    if not anos:
        st.info("Nenhum ajuizamento com data preenchida.")
        return

    controles = st.columns([1, 1.4, 1.4])
    with controles[0]:
        ano = st.selectbox("Ano", anos, index=0, key="meta_ano")
    cadastradas = _metas_cadastradas()
    meta_padrao = cadastradas.get(ano) or _projecao_meta(clientes, ano, cadastradas)
    with controles[1]:
        # Chave por ano: ao trocar o ano, o campo assume a meta daquele ano
        # em vez de manter o valor digitado para outro.
        meta_anual = st.number_input(
            "Meta anual (R$)",
            min_value=0.0,
            value=float(meta_padrao),
            step=100_000.0,
            format="%.2f",
            key=f"meta_anual_{ano}",
            help="Vem da aba METAS da planilha auxiliar. Sem meta cadastrada para "
            "o ano, usa a projeção (realizado do ano anterior + crescimento). "
            "Alterar aqui vale só para esta sessão.",
        )
    with controles[2]:
        meta_mensal = st.number_input(
            "Meta mensal de referência (R$)",
            min_value=0.0,
            value=float(meta_anual / 12) if meta_anual else 400_000.0,
            step=50_000.0,
            format="%.2f",
            key=f"meta_mensal_{ano}_{int(meta_anual)}",
            help="Serve só de linha de comparação. O que fecha o ano é a meta anual.",
        )
    if ano not in cadastradas:
        st.caption(
            f"Não há meta cadastrada para {ano}; o valor acima é a projeção. "
            "Para fixar, preencha a aba METAS da planilha auxiliar."
        )

    # Realizado: só o que está protocolado de fato (ver modelo.py). Quem tem
    # data de ajuizamento mas status PROTOCOLAR ou outro pendente fica fora
    # e aparece no aviso logo abaixo, para corrigir na planilha.
    ajuizados = clientes[
        clientes["protocolado"] & (clientes["ano_ajuizamento"] == ano)
    ].copy()
    pendentes = clientes[
        clientes["ajuizado"] & ~clientes["protocolado"] & ~clientes["encerrado"]
        & (clientes["ano_ajuizamento"] == ano)
    ]
    realizado = float(ajuizados["honorario_total"].sum())
    falta = max(meta_anual - realizado, 0.0)
    percentual = (realizado / meta_anual * 100) if meta_anual else 0.0

    # Pipeline: cliente com valor previsto que ainda não foi ajuizado.
    # É o que está parado fora do protocolo e pode virar realizado.
    #
    # O recorte por status importa: sem ele o pipeline pega desde caso
    # em checklist antigo até pré-descarte, o que superestima a chance
    # de bater a meta.
    disponivel = clientes[
        (~clientes["protocolado"])
        & (clientes["honorario_total"] > 0)
        & (~clientes["encerrado"])
    ].copy()

    etapas = sorted(disponivel["status"].dropna().unique())
    padrao = [
        e for e in etapas
        if any(t in e for t in ("MINUTA", "ESTRAT", "REVIS", "PROTOCOLAR"))
    ] or etapas

    cenario = st.multiselect(
        "Cenário do pipeline: quais etapas contam como próximas do protocolo",
        etapas,
        default=padrao,
        key="meta_cenario",
    )
    pipeline = disponivel[disponivel["status"].isin(cenario)] if cenario else disponivel
    valor_pipeline = float(pipeline["honorario_total"].sum())

    hoje = date.today()
    meses_restantes = (12 - hoje.month + 1) if hoje.year == ano else 0

    colunas = st.columns(4)
    ui.cartao(colunas[0], "Realizado no ano", formatar_moeda(realizado),
              f"{len(ajuizados)} protocolado(s) em {ano}. Conta só status "
              "Protocolado (ou Concluído) com data de ajuizamento.")
    ui.cartao(colunas[1], "Falta para a meta", formatar_moeda(falta),
              f"{percentual:.1f}% da meta atingido.".replace(".", ","))
    ui.cartao(
        colunas[2], "Parado fora do protocolo", formatar_moeda(valor_pipeline),
        f"{len(pipeline)} cliente(s) no cenário selecionado, de "
        f"{len(disponivel)} com valor e sem ajuizamento.",
    )
    ui.cartao(
        colunas[3],
        "Necessário por mês",
        formatar_moeda(falta / meses_restantes) if meses_restantes else "—",
        f"{meses_restantes} mês(es) restante(s) no ano." if meses_restantes
        else "Ano encerrado.",
    )

    if not pendentes.empty:
        with st.expander(
            f"{len(pendentes)} cliente(s) com data de ajuizamento em {ano} mas sem "
            f"status Protocolado, fora do realizado "
            f"({formatar_moeda(float(pendentes['honorario_total'].sum()))})"
        ):
            st.caption(
                "Não contam na meta. Se já foram protocolados, corrija o status na "
                "planilha; se não foram, a data de ajuizamento está adiantada."
            )
            ui.tabela_compacta(
                pendentes.sort_values("honorario_total", ascending=False).assign(
                    data_txt=lambda d: pd.to_datetime(d["data_ajuizamento"])
                    .dt.strftime("%d/%m/%Y")
                ),
                {
                    "cliente": "Cliente",
                    "status": "Status",
                    "responsavel": "Responsável",
                    "data_txt": "Data do ajuizamento",
                    "honorario_total": "Honorários previstos (R$)",
                    "link_bitrix": "Bitrix",
                    "linha_origem": "Linha",
                },
                moedas=["honorario_total"],
            )

    if falta <= 0:
        st.success(
            f"Meta de {formatar_moeda(meta_anual)} atingida. "
            f"Excedente de {formatar_moeda(realizado - meta_anual)}."
        )
    elif valor_pipeline >= falta:
        st.info(
            f"O pipeline atual cobre a meta: há {formatar_moeda(valor_pipeline)} "
            f"parado fora do protocolo para uma diferença de {formatar_moeda(falta)}."
        )
    else:
        st.warning(
            f"O pipeline atual não cobre a meta. Faltam {formatar_moeda(falta)} e há "
            f"{formatar_moeda(valor_pipeline)} disponível para protocolo, "
            f"uma diferença de {formatar_moeda(falta - valor_pipeline)} que "
            "depende de contratos novos."
        )

    _composicao_por_nucleo(ajuizados, pipeline, meta_anual, falta)
    _evolucao(ajuizados, meta_anual, meta_mensal, ano)
    _simulador(pipeline, falta)


# Cor fixa por núcleo, para o mesmo serviço ter a mesma cor em qualquer
# gráfico. Núcleo novo que apareça na planilha recebe cor da sequência.
CORES_NUCLEO = {
    "ISENÇÃO IR": "#1A3762",
    "BANCÁRIO": "#4DA2DA",
    "CONTRIBUIÇÃO PREV": "#F7BD2E",
    "PREVIDENCIÁRIO": "#2E7D32",
    "DIVERSOS": "#C1B7AD",
    "FALTA PARA A META": "#E3E9F0",
}


def _composicao_por_nucleo(ajuizados, pipeline, meta_anual, falta) -> None:
    """
    Quanto cada núcleo (serviço) já entregou da meta do ano.

    A pizza tem a meta inteira como 100%: cada fatia é o realizado de
    um núcleo e a fatia cinza é o que falta. A tabela ao lado cruza
    isso com o pipeline do cenário escolhido acima, para mostrar de
    qual núcleo pode vir o que falta.
    """
    st.markdown("#### Participação de cada núcleo na meta")

    if ajuizados.empty and pipeline.empty:
        st.info("Sem ajuizamentos nem pipeline no ano selecionado.")
        return

    quantidade = ajuizados.groupby("servico").size().rename("ajuizamentos")
    realizado = ajuizados.groupby("servico")["honorario_total"].sum().rename("realizado")
    parado = pipeline.groupby("servico")["honorario_total"].sum().rename("pipeline")
    nucleos = pd.concat([quantidade, realizado, parado], axis=1).fillna(0)
    nucleos.index.name = "servico"
    nucleos = nucleos.reset_index().sort_values("realizado", ascending=False)

    total_realizado = float(nucleos["realizado"].sum())
    nucleos["pct_realizado"] = (
        nucleos["realizado"] / total_realizado * 100 if total_realizado else 0.0
    )
    nucleos["pct_meta"] = nucleos["realizado"] / meta_anual * 100 if meta_anual else 0.0
    nucleos["potencial"] = nucleos["realizado"] + nucleos["pipeline"]

    fatias = nucleos[nucleos["realizado"] > 0][["servico", "realizado"]].copy()
    if falta > 0:
        fatias = pd.concat(
            [fatias, pd.DataFrame([{"servico": "FALTA PARA A META", "realizado": falta}])],
            ignore_index=True,
        )

    esquerda, direita = st.columns([1, 1.25])
    with esquerda:
        if fatias.empty:
            st.info("Sem valor realizado no ano.")
        else:
            figura = px.pie(
                fatias,
                names="servico",
                values="realizado",
                color="servico",
                color_discrete_map=CORES_NUCLEO,
                hole=0.35,
            )
            figura.update_traces(
                textinfo="percent",
                sort=False,
                hovertemplate="%{label}<br>R$ %{value:,.2f}<br>%{percent}<extra></extra>",
            )
            figura.update_layout(
                height=360,
                margin=dict(t=10, b=10, l=10, r=10),
                legend=dict(orientation="h", y=-0.08),
                separators=",.",
            )
            st.plotly_chart(figura, width="stretch")
            st.caption("Base da pizza: meta anual. A fatia cinza é o que falta.")

    with direita:
        ui.tabela_compacta(
            nucleos,
            {
                "servico": "Núcleo",
                "ajuizamentos": "Ajuiz.",
                "realizado": "Realizado (R$)",
                "pct_realizado": "% do realizado",
                "pct_meta": "% da meta",
                "pipeline": "Pipeline (R$)",
                "potencial": "Realizado + pipeline (R$)",
            },
            moedas=["realizado", "pipeline", "potencial"],
            inteiros=["ajuizamentos"],
            percentuais=["pct_realizado", "pct_meta"],
            somar=["ajuizamentos", "realizado", "pipeline", "potencial",
                   "pct_realizado", "pct_meta"],
        )
        st.caption(
            "Pipeline: clientes com valor e sem ajuizamento, nas etapas do "
            "cenário selecionado acima."
        )


def _pct(valor) -> str:
    try:
        return f"{float(valor):.1f}%".replace(".", ",")
    except (TypeError, ValueError):
        return "—"


# Meta de 2026 já definida pelo escritório. Vale enquanto a aba METAS não
# trouxer um valor para 2026; a aba sempre prevalece.
METAS_PADRAO = {2026: 4_800_000.0}


def _metas_da_planilha() -> dict:
    try:
        return dict(modelo.carregar_metas())
    except Exception:  # noqa: BLE001
        return {}


def _metas_cadastradas() -> dict:
    return {**METAS_PADRAO, **_metas_da_planilha()}


def _origem_meta(ano: int) -> str:
    if ano in _metas_da_planilha():
        return "Aba METAS"
    if ano in METAS_PADRAO:
        return "Padrão do painel"
    return "—"


def _realizado_por_ano(clientes: pd.DataFrame) -> pd.Series:
    base = clientes[clientes["protocolado"]]
    return base.groupby("ano_ajuizamento")["honorario_total"].sum()


def _projecao_meta(clientes, ano, cadastradas, crescimento: float = 0.30) -> float:
    """
    Meta de um ano sem meta cadastrada: realizado do ano anterior mais o
    crescimento. Se o ano anterior ainda está em curso, usa o ritmo dele
    (realizado dividido pelos meses decorridos, vezes 12).
    """
    realizado = _realizado_por_ano(clientes)
    anterior = ano - 1
    base = _fechamento_estimado(realizado.get(anterior, 0.0), anterior)
    if not base:
        base = cadastradas.get(anterior, 0.0)
    return round(base * (1 + crescimento), 2) if base else 4_800_000.0


def _fechamento_estimado(realizado_ano: float, ano: int) -> float:
    hoje = date.today()
    if ano == hoje.year and hoje.month > 0:
        # Mês corrente conta pela fração de dias já passados.
        decorridos = (hoje.month - 1) + hoje.day / 30.0
        return realizado_ano / decorridos * 12 if decorridos else 0.0
    return float(realizado_ano)


def _evolucao(ajuizados, meta_anual, meta_mensal, ano) -> None:
    st.markdown("#### Evolução acumulada no ano")

    if ajuizados.empty:
        st.info("Sem ajuizamentos no ano selecionado.")
        return

    mensal = (
        ajuizados.groupby("competencia_ajuizamento")["honorario_total"]
        .sum()
        .reset_index()
        .sort_values("competencia_ajuizamento")
    )
    mensal["acumulado"] = mensal["honorario_total"].cumsum()
    mensal["meta_acumulada"] = [
        meta_mensal * (i + 1) for i in range(len(mensal))
    ]

    figura = go.Figure()
    figura.add_bar(
        x=mensal["competencia_ajuizamento"],
        y=mensal["honorario_total"],
        name="No mês",
        marker_color="#4DA2DA",
    )
    figura.add_scatter(
        x=mensal["competencia_ajuizamento"],
        y=mensal["acumulado"],
        name="Acumulado",
        mode="lines+markers",
        line=dict(color="#1A3762", width=3),
    )
    figura.add_scatter(
        x=mensal["competencia_ajuizamento"],
        y=mensal["meta_acumulada"],
        name="Meta acumulada",
        mode="lines",
        line=dict(color="#C62828", width=2, dash="dash"),
    )
    figura.add_hline(
        y=meta_anual,
        line=dict(color="#F7BD2E", width=2, dash="dot"),
        annotation_text="Meta anual",
        annotation_position="top left",
    )
    figura.update_layout(
        height=400, xaxis_title="Competência", yaxis_title="R$",
        legend=dict(orientation="h", y=1.12),
    )
    st.plotly_chart(figura, width="stretch")

    # Tabela sempre aberta, com a leitura de cada mês: se o acumulado já
    # alcançou a meta acumulada e quanto da meta do ano ele representa.
    mensal["meta_mes_ok"] = mensal["honorario_total"] >= meta_mensal
    mensal["pct_mes"] = mensal["honorario_total"] / meta_mensal * 100 if meta_mensal else pd.NA
    mensal["diferenca"] = mensal["acumulado"] - mensal["meta_acumulada"]
    mensal["pct_anual"] = mensal["acumulado"] / meta_anual * 100 if meta_anual else pd.NA
    mensal["situacao"] = [
        "✓ Acima da meta" if d >= 0 else "✗ Abaixo da meta" for d in mensal["diferenca"]
    ]
    mensal["diferenca_txt"] = mensal["diferenca"].map(
        lambda v: ("+" if v >= 0 else "−") + ui.moeda_cheia(abs(v)) if v else "0,00"
    )

    ultimo = mensal.iloc[-1]
    if ultimo["diferenca"] >= 0:
        st.success(
            f"Até {ultimo['competencia_ajuizamento']}, o acumulado está "
            f"{ui.moeda_cheia(ultimo['diferenca'], True)} acima da meta acumulada. "
            f"{_pct(ultimo['pct_anual'])} da meta anual já atingidos."
        )
    else:
        st.warning(
            f"Até {ultimo['competencia_ajuizamento']}, o acumulado está "
            f"{ui.moeda_cheia(abs(ultimo['diferenca']), True)} abaixo da meta acumulada. "
            f"{_pct(ultimo['pct_anual'])} da meta anual atingidos."
        )

    meses_batidos = int(mensal["meta_mes_ok"].sum())
    ui.tabela_compacta(
        mensal,
        {
            "competencia_ajuizamento": "Mês",
            "honorario_total": "No mês (R$)",
            "pct_mes": "% da meta do mês",
            "acumulado": "Acumulado (R$)",
            "meta_acumulada": "Meta acumulada (R$)",
            "diferenca_txt": "Diferença (R$)",
            "pct_anual": "% da meta anual",
            "situacao": "Situação",
        },
        moedas=["honorario_total", "acumulado", "meta_acumulada"],
        percentuais=["pct_mes", "pct_anual"],
        alinhar_direita=["diferenca_txt"],
        somar=["honorario_total"],
        valores_total={
            "acumulado": ultimo["acumulado"],
            "meta_acumulada": ultimo["meta_acumulada"],
            "diferenca_txt": ultimo["diferenca_txt"],
            "pct_anual": ultimo["pct_anual"],
            "situacao": (
                ("✓ Em dia" if ultimo["diferenca"] >= 0 else "✗ Abaixo")
                + f" · {meses_batidos}/{len(mensal)} meses na meta"
            ),
        },
    )
    st.caption(
        "% da meta do mês: o valor do mês contra a meta mensal de referência. "
        "Diferença: acumulado menos meta acumulada. % da meta anual: quanto do "
        "objetivo do ano o acumulado já representa."
    )


def _entrada_no_status(pipeline: pd.DataFrame) -> tuple[pd.Series, object]:
    """
    Quando cada cliente entrou no status em que está hoje, pelo log.

    Procura, no log do controle de clientes, a última mudança da coluna
    STATUS para o valor atual daquela linha. A linha é identificada pelo
    nome do cliente registrado no log e, na falta dele, pelo número da
    linha. Sem registro, o status foi definido antes do início do log:
    devolve vazio e a data do primeiro registro do log, para a tela dizer
    "desde antes de".
    """
    vazio = pd.Series(pd.NaT, index=pipeline.index, dtype="datetime64[ns]")
    try:
        logs = modelo.carregar_logs()
    except Exception:  # noqa: BLE001 - sem log, o simulador segue sem a coluna
        return vazio, None
    if logs.empty:
        return vazio, None

    inicio_log = logs["data_hora"].min()
    aba = logs["aba_origem"].astype(str).map(normalizar_texto)
    campo = logs["cabecalho"].astype(str).map(normalizar_texto)
    mudancas = logs[aba.str.contains("CLIENTE", na=False) & campo.eq("STATUS")].copy()
    if mudancas.empty:
        return vazio, inicio_log

    mudancas["status_n"] = mudancas["valor_novo"].astype(str).map(normalizar_texto)
    mudancas["nome_n"] = mudancas["cliente_autor"].astype(str).map(normalizar_texto)
    mudancas["linha_n"] = pd.to_numeric(mudancas["linha"], errors="coerce")
    por_nome = mudancas[mudancas["nome_n"] != ""].groupby(["nome_n", "status_n"])["data_hora"].max()
    por_linha = mudancas.groupby(["linha_n", "status_n"])["data_hora"].max()

    datas = []
    for _, linha in pipeline.iterrows():
        status = normalizar_texto(linha["status"])
        chave_nome = (normalizar_texto(linha["cliente"]), status)
        chave_linha = (pd.to_numeric(linha.get("linha_origem"), errors="coerce"), status)
        if chave_nome in por_nome.index:
            datas.append(por_nome[chave_nome])
        elif chave_linha in por_linha.index:
            datas.append(por_linha[chave_linha])
        else:
            datas.append(pd.NaT)
    return pd.Series(pd.to_datetime(datas), index=pipeline.index), inicio_log


def _simulador(pipeline: pd.DataFrame, falta: float) -> None:
    st.markdown("#### Simulador de protocolo")

    if pipeline.empty:
        st.info("Nenhum cliente com valor previsto aguardando protocolo.")
        return

    filtros = st.columns([1.2, 2])
    with filtros[0]:
        status = st.multiselect(
            "Filtrar por status",
            sorted(pipeline["status"].dropna().unique()),
            default=[],
            key="sim_status",
        )
    with filtros[1]:
        busca = st.text_input(
            "Filtrar por cliente", placeholder="parte do nome", key="sim_busca"
        )

    pipeline = ui.aplicar_multiselecao(pipeline, "status", status)
    pipeline = ui.busca_texto(pipeline, ["cliente"], busca)

    if pipeline.empty:
        st.info("Nenhum cliente no filtro atual.")
        return

    ordenado = pipeline.sort_values("honorario_total", ascending=False).copy()
    # Dias corridos desde a assinatura do contrato até hoje: quanto tempo
    # o cliente já espera pelo protocolo. Sem data de contrato, fica vazio.
    hoje = pd.Timestamp.today().normalize()
    ordenado["dias_parado"] = (
        hoje - pd.to_datetime(ordenado["data_contrato"], errors="coerce")
    ).dt.days.astype("Int64")
    ordenado["data_contrato_txt"] = pd.to_datetime(
        ordenado["data_contrato"], errors="coerce"
    ).dt.strftime("%d/%m/%Y").fillna("—")

    entrada, inicio_log = _entrada_no_status(ordenado)
    ordenado["dias_status"] = (hoje - entrada.dt.normalize()).dt.days.astype("Int64")
    antes_do_log = (
        f"antes de {pd.Timestamp(inicio_log).strftime('%d/%m/%Y')}"
        if inicio_log is not None and pd.notna(inicio_log) else "sem registro"
    )
    ordenado["status_desde"] = entrada.dt.strftime("%d/%m/%Y").fillna(antes_do_log)
    ordenado["dias_status_txt"] = ordenado["dias_status"].map(
        lambda v: "—" if pd.isna(v) else str(int(v))
    )
    ordenado["rotulo"] = (
        ordenado["cliente"].astype(str)
        + " — "
        + ordenado["honorario_total"].apply(formatar_moeda)
        + " — "
        + ordenado["status"].astype(str)
    )

    # Sugestão gulosa: os maiores primeiro, até cobrir a diferença.
    # Não é a combinação ótima, é a que exige menos protocolos.
    sugeridos: list[str] = []
    if falta > 0:
        acumulado = 0.0
        for _, linha in ordenado.iterrows():
            if acumulado >= falta:
                break
            sugeridos.append(linha["rotulo"])
            acumulado += float(linha["honorario_total"])

    if sugeridos:
        st.caption(
            f"Sugestão automática: protocolando {len(sugeridos)} cliente(s) de maior "
            "valor, a diferença é coberta. Ajuste a seleção como quiser."
        )

    # A seleção é refeita com a sugestão sempre que o filtro muda. Antes,
    # o Streamlit guardava a seleção do filtro anterior; com o filtro novo
    # aqueles clientes deixavam de existir nas opções e a seleção ficava
    # vazia, zerando o valor.
    opcoes = ordenado["rotulo"].tolist()
    assinatura = (tuple(sorted(status)), busca.strip().upper(), len(opcoes), round(falta, 2))
    if st.session_state.get("sim_assinatura") != assinatura:
        st.session_state["sim_assinatura"] = assinatura
        st.session_state["meta_simulacao"] = sugeridos
    else:
        # Mantém só o que ainda existe nas opções.
        st.session_state["meta_simulacao"] = [
            r for r in st.session_state.get("meta_simulacao", []) if r in opcoes
        ]

    escolhidos = st.multiselect(
        "Clientes a protocolar",
        opcoes,
        key="meta_simulacao",
        placeholder="Escolha os clientes",
    )

    selecionados = ordenado[ordenado["rotulo"].isin(escolhidos)]
    soma = float(selecionados["honorario_total"].sum())
    restante = falta - soma

    colunas = st.columns(3)
    ui.cartao(colunas[0], "Selecionados", len(selecionados))
    ui.cartao(colunas[1], "Valor da seleção", formatar_moeda(soma))
    ui.cartao(
        colunas[2],
        "Diferença após protocolo",
        formatar_moeda(max(restante, 0.0)),
        "Quanto ainda faltaria para a meta anual.",
    )

    if soma and restante <= 0:
        st.success(
            f"Protocolando esses {len(selecionados)} cliente(s), a meta é batida "
            f"com folga de {formatar_moeda(-restante)}."
        )
    elif soma:
        st.warning(
            f"Ainda faltariam {formatar_moeda(restante)} depois de protocolar "
            "os clientes selecionados."
        )

    tabela = selecionados if not selecionados.empty else ordenado.head(30)
    ui.tabela_compacta(
        tabela,
        {
            "cliente": "Cliente",
            "servico": "Serviço",
            "status": "Status",
            "responsavel": "Responsável",
            "data_contrato_txt": "Contrato",
            "dias_parado": "Dias desde o contrato",
            "status_desde": "No status atual desde",
            "dias_status_txt": "Dias no status atual",
            "honorario_total": "Honorários previstos (R$)",
            "link_bitrix": "Bitrix",
            "linha_origem": "Linha",
        },
        moedas=["honorario_total"],
        inteiros=["dias_parado"],
        alinhar_direita=["dias_status_txt"],
        somar=["honorario_total"],
        valores_total={
            "dias_parado": tabela["dias_parado"].mean(),
            "dias_status_txt": (
                "—" if tabela["dias_status"].isna().all()
                else str(int(round(tabela["dias_status"].mean())))
            ),
        },
        vazio="Sem clientes aguardando protocolo.",
    )
    st.caption(
        "Dias desde o contrato: dias corridos entre a data do contrato e hoje. "
        "No status atual desde: a última vez que o STATUS da linha foi mudado para "
        "o valor de hoje (MINUTA, por exemplo), segundo o log de alterações. "
        "\"Antes de\" indica que a mudança é anterior ao início do log. Na linha "
        "de total, as médias de dias entre os clientes listados."
    )


# --------------------------------------------------------- painel geral


@st.fragment
def painel(clientes: pd.DataFrame) -> None:
    st.markdown("### Carteira")

    filtrados = ui.filtro_periodo(
        clientes, "data_ajuizamento", "Ajuizamento entre", "fin_periodo"
    )
    responsaveis = ui.multiselecao(
        "Responsável", ui.opcoes(filtrados, "responsavel"), "fin_resp"
    )
    filtrados = ui.aplicar_multiselecao(filtrados, "responsavel", responsaveis)

    # Duas bases distintas, e confundi-las produz número errado.
    #
    # AJUIZADOS: quem tem Data do Ajuizamento dentro do período. É o
    # realizado, e é o que bate com o DASH CLIENTES.
    #
    # FILTRADOS: a carteira inteira do recorte, incluindo quem ainda
    # não foi protocolado. O filtro de data preserva de propósito quem
    # não tem data, senão minuta e checklist sumiriam da tela.
    ajuizados = filtrados[filtrados["protocolado"]]
    realizado = ajuizados["honorario_total"].sum()
    carteira = filtrados["honorario_total"].sum()
    media = realizado / len(ajuizados) if len(ajuizados) else 0

    colunas = st.columns(4)
    ui.cartao(colunas[0], "Ajuizamentos no período", len(ajuizados))
    ui.cartao(
        colunas[1], "Honorários dos ajuizamentos", formatar_moeda(realizado),
        "Soma apenas dos clientes com Data do Ajuizamento no período.",
    )
    ui.cartao(
        colunas[2], "Média por ajuizamento", formatar_moeda(media),
        "Honorários dos ajuizamentos dividido pela quantidade de ajuizamentos.",
    )
    ui.cartao(
        colunas[3], "Previsto na carteira", formatar_moeda(carteira),
        f"Todos os {len(filtrados)} clientes do recorte, protocolados ou não. "
        f"A diferença de {formatar_moeda(carteira - realizado)} está em etapas "
        "anteriores ao protocolo.",
    )

    colunas = st.columns(3)
    ui.cartao(
        colunas[0], "Contratuais dos ajuizamentos",
        formatar_moeda(ajuizados["honorario_previsto"].sum()),
    )
    ui.cartao(
        colunas[1], "Sucumbenciais dos ajuizamentos",
        formatar_moeda(ajuizados["honorario_sucumbencial"].sum()),
    )
    dias = filtrados["dias_contrato_ajuizamento"].dropna()
    dias = dias[dias >= 0]
    ui.cartao(
        colunas[2], "Média contrato até ajuizamento",
        f"{dias.mean():.1f} dias".replace(".", ",") if len(dias) else "—",
        f"Calculada sobre {len(dias)} caso(s) com as duas datas.",
    )

    # Matrizes por mês: só protocolados de fato, com o mesmo critério da meta.
    base = filtrados[filtrados["protocolado"]].copy()

    esquerda, direita = st.columns(2)
    with esquerda:
        st.markdown("#### Por responsável")
        carteira_resp = (
            filtrados.groupby("responsavel")
            .agg(
                clientes=("cliente", "count"),
                carteira=("honorario_total", "sum"),
            )
            .reset_index()
        )
        realizado_resp = (
            ajuizados.groupby("responsavel")
            .agg(
                ajuizamentos=("cliente", "count"),
                honorario_total=("honorario_total", "sum"),
            )
            .reset_index()
        )
        por_responsavel = carteira_resp.merge(
            realizado_resp, on="responsavel", how="left"
        ).fillna({"ajuizamentos": 0, "honorario_total": 0})
        por_responsavel["media"] = por_responsavel["honorario_total"] / (
            por_responsavel["ajuizamentos"].replace(0, pd.NA)
        )
        por_responsavel = por_responsavel.sort_values(
            "honorario_total", ascending=False
        )
        ui.tabela_compacta(
            por_responsavel,
            {
                "responsavel": "Responsável",
                "clientes": "Clientes",
                "carteira": "Carteira (R$)",
                "ajuizamentos": "Ajuizados",
                "honorario_total": "Ajuizado (R$)",
                "media": "Média (R$)",
            },
            moedas=["carteira", "honorario_total", "media"],
            inteiros=["clientes", "ajuizamentos"],
            somar=["clientes", "carteira", "ajuizamentos", "honorario_total"],
            medias={"media": ("honorario_total", "ajuizamentos")},
        )

    with direita:
        st.markdown("#### Por status da carteira")
        st.caption("Previsto de toda a carteira, independentemente do protocolo.")
        por_status = (
            filtrados.groupby("status")
            .agg(
                clientes=("cliente", "count"),
                honorario_total=("honorario_total", "sum"),
            )
            .reset_index()
            .sort_values("honorario_total", ascending=False)
        )
        ui.tabela_compacta(
            por_status,
            {
                "status": "Status",
                "clientes": "Quantidade",
                "honorario_total": "Previsto (R$)",
            },
            moedas=["honorario_total"],
            inteiros=["clientes"],
        )

    if base.empty:
        return

    st.markdown("#### Honorários por mês e por serviço")
    st.caption("Valores em R$, sem abreviação.")
    ui.matriz_com_total(
        base, "competencia_ajuizamento", "servico", "honorario_total", "Mês"
    )

    st.markdown("#### Ajuizamentos por mês e por responsável")
    ui.matriz_com_total(
        base,
        "competencia_ajuizamento",
        "responsavel",
        "protocolado",
        "Mês",
        formato="inteiro",
    )



# ------------------------------------------------- metas e projeções


@st.fragment
def metas_e_projecoes(clientes: pd.DataFrame) -> None:
    """
    Metas de todos os anos lado a lado com o realizado, e a projeção dos
    próximos anos com crescimento sobre o fechamento do ano atual.
    """
    st.markdown("### Metas e projeções")
    st.caption(
        "Realizado: só protocolados de fato. Meta cadastrada: aba METAS da "
        "planilha auxiliar (colunas ANO e META ANUAL). Os anos futuros sem meta "
        "cadastrada recebem a projeção."
    )

    cadastradas = _metas_cadastradas()
    realizado = _realizado_por_ano(clientes)
    hoje = date.today()

    controles = st.columns([1, 1, 2])
    with controles[0]:
        crescimento = st.number_input(
            "Crescimento ao ano (%)", min_value=0.0, max_value=300.0, value=30.0,
            step=5.0, key="proj_crescimento",
        ) / 100
    with controles[1]:
        horizonte = st.number_input(
            "Anos à frente", min_value=1, max_value=5, value=3, step=1,
            key="proj_horizonte",
        )
    with controles[2]:
        base_escolhida = st.radio(
            "Base da projeção do ano atual",
            ["Fechamento estimado pelo ritmo", "Realizado até hoje"],
            horizontal=True, key="proj_base",
            help="O ano atual ainda não terminou. Pelo ritmo, o realizado até hoje "
            "é projetado para 12 meses; a outra opção usa só o que já foi realizado.",
        )

    anos_passados = sorted(
        {int(a) for a in realizado.index if pd.notna(a)} | set(cadastradas)
    )
    anos_passados = [a for a in anos_passados if a <= hoje.year]
    linhas = []
    for ano in anos_passados:
        feito = float(realizado.get(ano, 0.0))
        meta = cadastradas.get(ano)
        estimado = _fechamento_estimado(feito, ano) if ano == hoje.year else feito
        linhas.append({
            "ano": str(ano) + (" (em curso)" if ano == hoje.year else ""),
            "meta": meta if meta else pd.NA,
            "origem": _origem_meta(ano),
            "realizado": feito,
            "pct": feito / meta * 100 if meta else pd.NA,
            "estimado": estimado,
            "pct_estimado": estimado / meta * 100 if meta else pd.NA,
            "situacao": (
                "" if not meta else
                ("✓ Batida" if feito >= meta else
                 ("✗ Não batida" if ano < hoje.year else
                  ("◐ No ritmo de bater" if estimado >= meta else "◐ Abaixo do ritmo")))
            ),
        })

    atual = float(realizado.get(hoje.year, 0.0))
    base = _fechamento_estimado(atual, hoje.year) if base_escolhida.startswith("Fech") else atual
    projetado = base
    for passo in range(1, int(horizonte) + 1):
        ano = hoje.year + passo
        projetado = projetado * (1 + crescimento)
        meta = cadastradas.get(ano)
        linhas.append({
            "ano": str(ano),
            "meta": meta if meta else projetado,
            "origem": _origem_meta(ano) if meta else f"Projeção (+{crescimento * 100:.0f}% a.a.)",
            "realizado": pd.NA,
            "pct": pd.NA,
            "estimado": pd.NA,
            "pct_estimado": pd.NA,
            "situacao": "",
        })
        if meta:
            projetado = meta  # o ano seguinte parte da meta cadastrada

    tabela = pd.DataFrame(linhas)
    ui.tabela_compacta(
        tabela,
        {
            "ano": "Ano",
            "meta": "Meta (R$)",
            "origem": "Origem da meta",
            "realizado": "Realizado (R$)",
            "pct": "% atingido",
            "estimado": "Fechamento estimado (R$)",
            "pct_estimado": "% estimado",
            "situacao": "Situação",
        },
        moedas=["meta", "realizado", "estimado"],
        percentuais=["pct", "pct_estimado"],
        total=False,
    )
    st.caption(
        f"Fechamento estimado do ano em curso: realizado até hoje "
        f"({ui.moeda_cheia(atual, True)}) dividido pelos meses decorridos e "
        f"multiplicado por 12 ({ui.moeda_cheia(_fechamento_estimado(atual, hoje.year), True)}). "
        "Projeção dos anos seguintes: base escolhida acima mais o crescimento ao ano, "
        "acumulado. Um ano com meta cadastrada passa a ser a base do seguinte."
    )

    grafico = tabela.copy()
    grafico["ano_n"] = grafico["ano"].str.slice(0, 4)
    figura = go.Figure()
    figura.add_bar(x=grafico["ano_n"], y=pd.to_numeric(grafico["meta"], errors="coerce"),
                   name="Meta", marker_color="#C1B7AD")
    figura.add_bar(x=grafico["ano_n"], y=pd.to_numeric(grafico["realizado"], errors="coerce"),
                   name="Realizado", marker_color="#1A3762")
    figura.add_scatter(x=grafico["ano_n"],
                       y=pd.to_numeric(grafico["estimado"], errors="coerce"),
                       name="Fechamento estimado", mode="markers",
                       marker=dict(color="#F7BD2E", size=12, symbol="diamond"))
    figura.update_layout(height=340, barmode="group", yaxis_title="R$", xaxis_title="",
                         legend=dict(orientation="h", y=1.12), separators=",.")
    st.plotly_chart(figura, width="stretch", key="proj_grafico")
