/* ============================================================
 * PATCH — LOG_ALTERACOES EM PLANILHA EXTERNA
 *
 * Objetivo: tirar da planilha principal a aba que cresce sem limite,
 * sem alterar nenhuma linha do codigo ja existente.
 *
 * Como instalar:
 *   1. Crie um NOVO arquivo .gs no mesmo projeto do Apps Script.
 *   2. Cole este conteudo inteiro.
 *   3. Garanta que este arquivo fique como o ULTIMO da lista de
 *      arquivos do projeto (arraste no menu lateral, se preciso).
 *      A sobrescrita de garantirAbaLog_ precisa ocorrer depois da
 *      declaracao original.
 *   4. Preencha LOG_SPREADSHEET_ID abaixo.
 *   5. Menu Gestao DB -> Migrar log para planilha externa.
 *   6. Menu Gestao DB -> Reinstalar gatilhos.
 *
 * Requisito tecnico: o gatilho de edicao PRECISA ser instalavel.
 * O projeto ja instala 'aoEditarControle' via ScriptApp.newTrigger,
 * entao esta condicao esta atendida. Um gatilho simples chamado
 * literalmente onEdit nao consegue usar SpreadsheetApp.openById e
 * falharia em silencio.
 *
 * O que este patch NAO move: a aba BASE IDS. Ela e pequena, tem
 * tamanho proporcional a quantidade de registros e esta acoplada ao
 * escopo fechado da versao V12. Mover exigiria reescrever aquele
 * bloco, com risco desproporcional ao ganho de celulas.
 * ============================================================
 */

/** ID da planilha que vai receber os logs. Somente o ID, sem URL. */
const LOG_SPREADSHEET_ID = 'COLE_AQUI_O_ID_DA_PLANILHA_AUXILIAR';

/** Cache da planilha externa dentro da mesma execucao. */
var DB_LOG_EXTERNO_CACHE_ = null;

function DB_LOG_EXTERNO_PLANILHA_() {
  if (DB_LOG_EXTERNO_CACHE_) return DB_LOG_EXTERNO_CACHE_;

  if (!LOG_SPREADSHEET_ID || LOG_SPREADSHEET_ID.indexOf('COLE_AQUI') === 0) {
    throw new Error(
      'LOG_SPREADSHEET_ID nao foi preenchido no arquivo PATCH_LOGS_EXTERNOS.'
    );
  }

  DB_LOG_EXTERNO_CACHE_ = SpreadsheetApp.openById(LOG_SPREADSHEET_ID);
  return DB_LOG_EXTERNO_CACHE_;
}

/* ------------------------------------------------------------
 * Sobrescrita de garantirAbaLog_.
 *
 * Todas as funcoes do sistema pedem a aba de log por esta funcao:
 * aoEditarControle, atualizarDashboard, lerPrazos_, lerClientes_ e
 * escreverAtividadesRecentes_. Trocando so este ponto, a gravacao e a
 * leitura passam a ocorrer na planilha externa e nada mais precisa ser
 * alterado. O parametro ss e ignorado de proposito.
 * ------------------------------------------------------------
 */
var DB_LOG_EXTERNO_GARANTIR_ANTERIOR_ = garantirAbaLog_;

garantirAbaLog_ = function(ss) {
  const destino = DB_LOG_EXTERNO_PLANILHA_();
  return DB_LOG_EXTERNO_GARANTIR_ANTERIOR_(destino);
};

/* ------------------------------------------------------------
 * Migracao do historico existente.
 *
 * Copia em blocos para nao estourar o tempo de execucao. Pode ser
 * chamada mais de uma vez: continua de onde parou, comparando a
 * quantidade de linhas ja transferidas.
 * ------------------------------------------------------------
 */
function migrarLogParaPlanilhaExterna() {
  const origem = SpreadsheetApp
    .getActiveSpreadsheet()
    .getSheetByName(CONFIG_DB.ABA_LOG);

  if (!origem) {
    SpreadsheetApp.getUi().alert(
      'Nao existe a aba ' + CONFIG_DB.ABA_LOG + ' nesta planilha.'
    );
    return;
  }

  const destino = DB_LOG_EXTERNO_GARANTIR_ANTERIOR_(DB_LOG_EXTERNO_PLANILHA_());

  const totalOrigem = Math.max(origem.getLastRow() - 1, 0);
  const totalDestino = Math.max(destino.getLastRow() - 1, 0);

  if (totalDestino >= totalOrigem) {
    SpreadsheetApp.getUi().alert(
      'Nada a migrar. Origem: ' + totalOrigem +
      ' linha(s). Destino: ' + totalDestino + ' linha(s).'
    );
    return;
  }

  const primeira = totalDestino + 2;
  const restante = totalOrigem - totalDestino;
  const bloco = Math.min(restante, 5000);

  const valores = origem
    .getRange(primeira, 1, bloco, CABECALHOS_LOG.length)
    .getValues();

  destino
    .getRange(destino.getLastRow() + 1, 1, bloco, CABECALHOS_LOG.length)
    .setValues(valores);

  SpreadsheetApp.flush();

  const migradas = totalDestino + bloco;

  SpreadsheetApp.getUi().alert(
    'Migradas ' + migradas + ' de ' + totalOrigem + ' linha(s). ' +
    (migradas < totalOrigem
      ? 'Execute novamente para continuar.'
      : 'Migracao concluida. Confira o destino antes de excluir a aba de origem.')
  );
}

/**
 * Conferencia antes de excluir a aba antiga.
 * Compara contagem e a ultima linha das duas planilhas.
 */
function conferirMigracaoDoLog() {
  const origem = SpreadsheetApp
    .getActiveSpreadsheet()
    .getSheetByName(CONFIG_DB.ABA_LOG);
  const destino = DB_LOG_EXTERNO_GARANTIR_ANTERIOR_(DB_LOG_EXTERNO_PLANILHA_());

  const totalOrigem = origem ? Math.max(origem.getLastRow() - 1, 0) : 0;
  const totalDestino = Math.max(destino.getLastRow() - 1, 0);

  let ultimaOrigem = '';
  let ultimaDestino = '';

  if (totalOrigem > 0) {
    ultimaOrigem = origem
      .getRange(origem.getLastRow(), 1, 1, 2)
      .getDisplayValues()[0]
      .join(' | ');
  }
  if (totalDestino > 0) {
    ultimaDestino = destino
      .getRange(destino.getLastRow(), 1, 1, 2)
      .getDisplayValues()[0]
      .join(' | ');
  }

  SpreadsheetApp.getUi().alert(
    'CONFERENCIA DO LOG\n\n' +
    'Origem (planilha principal): ' + totalOrigem + ' linha(s)\n' +
    'Ultima: ' + ultimaOrigem + '\n\n' +
    'Destino (planilha auxiliar): ' + totalDestino + ' linha(s)\n' +
    'Ultima: ' + ultimaDestino + '\n\n' +
    (totalDestino >= totalOrigem
      ? 'OK. O destino contem todo o historico.'
      : 'ATENCAO. Ainda faltam linhas. Nao exclua a aba de origem.')
  );
}

/**
 * Teste de gravacao. Registra uma linha de verificacao na planilha
 * externa. Serve para confirmar que a conta que roda o gatilho tem
 * permissao de edicao no destino.
 */
function testarGravacaoDoLogExterno() {
  const sheet = garantirAbaLog_(SpreadsheetApp.getActiveSpreadsheet());
  const agora = new Date();

  const linha = new Array(CABECALHOS_LOG.length).fill('');
  linha[0] = 'TESTE-' + agora.getTime();
  linha[1] = agora;
  linha[4] = 'TESTE DE INSTALACAO';
  linha[10] = 'TESTE';
  linha[22] = 'Linha gerada por testarGravacaoDoLogExterno. Pode ser excluida.';

  sheet.appendRow(linha);
  SpreadsheetApp.flush();

  SpreadsheetApp.getUi().alert(
    'Gravacao realizada na planilha auxiliar. Verifique a ultima linha da aba ' +
    CONFIG_DB.ABA_LOG + '.'
  );
}

/**
 * Menu proprio de migracao.
 *
 * Nao substitui o menu Gestao DB existente: envolve o onOpen atual e
 * acrescenta um segundo menu. Como este arquivo e o ultimo do projeto,
 * a variavel onOpen ja aponta para a versao mais recente quando esta
 * linha e executada.
 */
function menuMigracaoLog() {
  SpreadsheetApp.getUi()
    .createMenu('Gestão DB — Migração')
    .addItem('Testar gravação na planilha auxiliar', 'testarGravacaoDoLogExterno')
    .addItem('Migrar log para planilha externa', 'migrarLogParaPlanilhaExterna')
    .addItem('Conferir migração do log', 'conferirMigracaoDoLog')
    .addToUi();
}

var DB_LOG_EXTERNO_ONOPEN_ANTERIOR_ = onOpen;

onOpen = function() {
  try {
    DB_LOG_EXTERNO_ONOPEN_ANTERIOR_();
  } catch (erro) {
    // O menu de migracao precisa aparecer mesmo que o menu principal
    // falhe, porque e por ele que se diagnostica o problema.
  }
  menuMigracaoLog();
};
