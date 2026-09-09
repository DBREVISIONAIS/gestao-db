# Gestão DB — painel Streamlit

Painel de consulta do controle de clientes e prazos. Somente leitura: não altera nada nas planilhas.

Para o passo a passo de instalação, ver `MANUAL_IMPLANTACAO.md`.

## Estrutura

```
streamlit_db/
├── app.py                          entrada, login e navegação
├── requirements.txt
├── gerar_hash.py                   gera o hash de senha para os Secrets
├── .streamlit/
│   ├── config.toml                 tema institucional
│   └── secrets.toml.example        modelo de configuração
├── db/
│   ├── conexao.py                  acesso ao Sheets, escopo readonly, cache
│   ├── modelo.py                   abas → DataFrames
│   ├── normalizacao.py             texto, números e datas no padrão do .gs
│   └── auth.py                     login por senha e perfis opcionais
├── paginas/
│   ├── visao_geral.py
│   ├── prazos.py                   substitui DASH PRAZOS
│   ├── clientes.py                 substitui DASH CLIENTES
│   ├── financeiro.py
│   ├── producao.py                 cruza estado atual com histórico
│   ├── historico.py                substitui a consulta ao LOG_ALTERACOES
│   └── componentes.py              filtros e indicadores reutilizados
└── apps_script/
    ├── AUXILIAR_ESPELHO.gs         vai no script da planilha AUXILIAR
    └── PATCH_LOGS_EXTERNOS.gs      vai no script da planilha PRINCIPAL
```

## Acesso

Padrão: senha única definida em `SENHA_ACESSO`, guardada como hash SHA-256. Preenchendo os blocos `[usuarios]` e `[perfis]` nos Secrets, o painel passa automaticamente para acesso por usuário.

## Desempenho

Cada página carrega só as fontes que usa, o corpo das telas roda em `st.fragment` para que o filtro não reexecute o app inteiro, e a leitura do log é limitada por `MAX_LINHAS_LOG`.

## Fontes de dados

Tudo vem da planilha auxiliar. A planilha principal não é compartilhada com a conta de serviço e não é lida pelo painel.

| Aba na auxiliar | Origem                                    | Atualização        |
| --------------- | ----------------------------------------- | ------------------ |
| BI_PRAZOS       | IMPORTRANGE de CONTROLE DE PRAZOS         | a cada 10 min      |
| BI_CLIENTES     | IMPORTRANGE de CONTROLE DE CLIENTES       | a cada 10 min      |
| LOG_ALTERACOES  | gravado pelo Apps Script da principal     | imediata           |
| CONTROLE_BI     | carimbo de hora do espelho                | a cada 10 min      |
| BI_BASE_IDS     | IMPORTRANGE de BASE IDS (opcional)        | a cada 10 min      |

## Compatibilidade com o Apps Script

`db/normalizacao.py` reproduz de propósito o comportamento de `normalizarTexto_`, `converterNumero_`, `converterPercentual_` e `classificarTipoPrazo_`. `db/modelo.py` reproduz `obterContextoLinha_` na versão V12.3, inclusive o alias `FATAL` para o prazo fatal e a regra das duas colunas `RESPONSÁVEL` do controle de prazos, em que a primeira ocorrência é o responsável técnico e a última é a controladoria.

Se o `.gs` mudar alguma dessas regras, o `.py` correspondente precisa mudar junto, senão o painel e o dashboard passam a divergir.

## Execução local

```
pip install -r requirements.txt
cp .streamlit/secrets.toml.example .streamlit/secrets.toml
# preencher os IDs, a conta de serviço e os usuários
streamlit run app.py
```

`.streamlit/secrets.toml` e o JSON da conta de serviço estão no `.gitignore`. Não versionar.
