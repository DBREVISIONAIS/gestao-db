# Manual de implantação — Gestão DB no Streamlit

**Problema que se está resolvendo:** a planilha principal está batendo no limite de 10.000.000 de células porque acumula três funções ao mesmo tempo — base operacional, log de alterações e dashboards.

**Solução:** log e dashboards saem da planilha principal. O painel passa a ler uma planilha auxiliar, que é sua, e a planilha principal não recebe nenhum compartilhamento novo.

```
PLANILHA PRINCIPAL                    PLANILHA AUXILIAR
CONTROLE DE CLIENTES        ──────►   BI_CLIENTES      (IMPORTRANGE)
CONTROLE DE PRAZOS          ──────►   BI_PRAZOS        (IMPORTRANGE)
                            ──────►   LOG_ALTERACOES   (Apps Script)
        │                             CONTROLE_BI      (carimbo de hora)
        │                                     │
   não é compartilhada                        │ conta de serviço
   com ninguém novo                           │ somente leitura
                                              ▼
                                          STREAMLIT
                                      protegido por senha
```

A credencial do painel só enxerga a auxiliar. Mesmo que ela vazasse, não daria acesso à planilha principal.

---

## Fase 1 — Planilha auxiliar

Criar no Drive uma planilha chamada `DB - BI E LOGS`. Não precisa criar aba: o script cria todas.

Em Extensões > Apps Script dessa planilha, colar o arquivo `apps_script/AUXILIAR_ESPELHO.gs`, preencher `ID_PLANILHA_PRINCIPAL` no topo e salvar. Recarregar a planilha: aparece o menu `Espelho BI`.

Menu `Espelho BI` > **Criar/atualizar abas de espelho**. Isso cria `BI_PRAZOS` e `BI_CLIENTES` com a fórmula IMPORTRANGE já montada.

Abrir cada uma das duas abas. Vai aparecer o aviso pedindo para conectar as planilhas: clicar em **Permitir acesso**. É pedido uma única vez, e é o que autoriza a auxiliar a puxar dados da principal.

Menu `Espelho BI` > **Instalar atualização automática**.

Esse gatilho não é opcional. Segundo a documentação do Google, o IMPORTRANGE só verifica atualizações a cada hora enquanto o documento está aberto, e o painel lendo por API não conta como abrir o documento. Sem o gatilho, o espelho ficaria horas defasado. O script força o recálculo a cada dez minutos e grava o horário na aba `CONTROLE_BI`, que o painel lê para mostrar a idade do dado e avisar quando passar do esperado.

Os intervalos das fórmulas são delimitados de propósito, `A1:L10000` e `A1:BZ10000`. Nunca usar coluna inteira: isso traria milhões de células vazias e recriaria na auxiliar o mesmo problema que estamos resolvendo na principal. Se a base passar de dez mil linhas, aumentar o número em `CONFIG_BI.ESPELHOS`.

---

## Fase 2 — Conta de serviço

No Google Cloud Console: criar um projeto, ativar **Google Sheets API** e **Google Drive API**, criar uma **Conta de serviço**, e nela gerar uma chave do tipo **JSON**.

Copiar o `client_email` de dentro do JSON, com formato `algo@projeto.iam.gserviceaccount.com`, e compartilhar **apenas a planilha auxiliar** com esse endereço, como **Visualizador**. Desmarcar o envio de notificação, porque não existe caixa postal.

A planilha principal não entra nessa etapa. Ela continua exatamente com as permissões que tem hoje.

O JSON é credencial. Não anexar em conversa, não subir para repositório, não deixar em pasta compartilhada.

---

## Fase 3 — Publicar o painel

Subir a pasta `streamlit_db` para um repositório privado e conectar em `share.streamlit.io`, ou publicar onde já roda a ferramenta de cálculo do núcleo bancário.

Em Settings > Secrets, colar o conteúdo de `.streamlit/secrets.toml.example` preenchido com o ID da auxiliar, os campos do JSON e a senha. Para gerar a senha:

```
python gerar_hash.py
```

A senha em si nunca é gravada, só o hash SHA-256.

No Streamlit Community Cloud, publicar o aplicativo como **privado**. Assim o Streamlit exige login por conta Google antes mesmo da tela de senha, e o endereço deixa de ser porta de entrada.

Conferir contra a planilha antes de seguir: total de prazos em aberto, total de clientes ativos e soma de honorários do mês. Os números têm que bater com o `DASH PRAZOS` e o `DASH CLIENTES` atuais. Se não baterem, quase sempre é um cabeçalho que mudou de nome e não está entre os aliases de `db/modelo.py`.

---

## Fase 4 — Mover o log

No Apps Script da **planilha principal**, criar um arquivo novo, colar `apps_script/PATCH_LOGS_EXTERNOS.gs` e arrastá-lo para ser o **último arquivo da lista**. A sobrescrita de `garantirAbaLog_` precisa acontecer depois da declaração original.

Preencher `LOG_SPREADSHEET_ID` com o ID da auxiliar. Salvar e recarregar: aparece o menu `Gestão DB — Migração`.

Menu > **Testar gravação na planilha auxiliar**. Autorizar quando o Google pedir, porque o escopo mudou. Confirmar que apareceu uma linha `TESTE-...` na aba `LOG_ALTERACOES` da auxiliar.

Menu > **Migrar log para planilha externa**. Copia em blocos de 5.000 linhas. Executar de novo até concluir. Depois, menu > **Conferir migração do log**, e só avançar quando confirmar que o destino tem todo o histórico.

Menu `Gestão DB` > **Reinstalar gatilhos**. Fazer uma edição real de teste e confirmar que o registro apareceu na auxiliar.

O log não passa por IMPORTRANGE: o Apps Script grava direto na auxiliar, no mesmo instante da edição. Não há defasagem aqui.

---

## Fase 5 — Limpar a planilha principal

Depois de pelo menos uma semana rodando em paralelo:

Excluir a aba `LOG_ALTERACOES` da principal. Excluir `DASH PRAZOS`, `DASH CLIENTES`, `BASE PRAZOS`, `BASE CLIENTES` e `BASE_DASHBOARD`. Remover o gatilho por tempo de 15 minutos que chama `atualizarDashboard` — o gatilho `onEdit` de `aoEditarControle` continua, porque é ele que alimenta o log.

Por fim, em cada aba operacional que sobrou, excluir as linhas e colunas não utilizadas: selecionar da primeira linha vazia até o fim, botão direito, Excluir linhas, e o mesmo com as colunas à direita da última em uso.

Excluir a aba é o que libera células. Limpar conteúdo ou ocultar não reduz a contagem.

---

## O que fica em cada lugar

`CONTROLE DE PRAZOS` e `CONTROLE DE CLIENTES` ficam onde estão, intocadas. O próprio script já foi evoluído na V12.1 para nunca escrever nelas.

`LOG_ALTERACOES` migra para a auxiliar. `BASE IDS` fica na principal; se você quiser vê-la no painel, crie na auxiliar uma aba `BI_BASE_IDS` com um IMPORTRANGE dela, e o painel passa a exibi-la automaticamente.

`DASH PRAZOS`, `DASH CLIENTES` e as bases de apoio deixam de existir. Elas só existiam porque o Sheets precisa de células para calcular. O Streamlit calcula em memória.

---

## Manutenção

**Defasagem.** O espelho atualiza a cada dez minutos e o painet relê a cada dois. Na prática o dado tem até doze minutos de idade. Para acompanhamento gerencial isso é suficiente. Para conferir se um prazo específico foi protocolado agora, a fonte é a planilha, não o painel. O painel mostra o horário da última atualização justamente para que essa diferença fique visível.

Se precisar de menos defasagem, baixar `INTERVALO_MINUTOS` para 5 ou 1 no script da auxiliar. Cada execução consome cota de Apps Script, então não vale descer sem necessidade.

**Coluna nova.** Se surgir coluna que deva aparecer no painel, conferir se ela está dentro do intervalo do IMPORTRANGE e incluir o alias em `db/modelo.py`. Os aliases comparam texto normalizado, sem acento e sem pontuação.

**Volume do log.** A auxiliar também tem limite de 10.000.000 de células. Com 23 colunas, dá cerca de 430.000 linhas de log. Quando chegar perto, criar `DB - BI E LOGS 2027` e repontar o `LOG_SPREADSHEET_ID`.

**Usuário novo.** Enquanto for senha única, não há usuário a cadastrar. Se a equipe passar a usar, descomentar os blocos `[usuarios]` e `[perfis]` nos Secrets: a existência de qualquer usuário desliga o modo de senha única automaticamente.

---

## Pontos de atenção

**A defasagem existe e é o preço desta arquitetura.** Foi escolhida em troca de não compartilhar a planilha principal. O aviso automático no painel é o que impede que dado velho passe por dado atual.

**Gatilho instalável é requisito para o log.** `SpreadsheetApp.openById` não funciona em gatilho simples. O projeto já instala `aoEditarControle` via `ScriptApp.newTrigger`, então está atendido. Se alguém criar uma função chamada literalmente `onEdit`, ela falha em silêncio.

**Autorização do IMPORTRANGE.** Se as abas de espelho mostrarem `#REF!`, é porque ninguém clicou em Permitir acesso. O painel detecta isso e exibe o erro em vermelho na barra lateral.

**LGPD.** A auxiliar guarda nome de cliente, andamento processual e e-mail de quem editou. Segue a mesma disciplina de acesso do resto do escritório: compartilhamento restrito, conta de serviço somente leitura e credencial fora de repositório público.
