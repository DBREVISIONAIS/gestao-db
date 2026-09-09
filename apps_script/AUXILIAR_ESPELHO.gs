/* ============================================================
 * PLANILHA AUXILIAR — ESPELHO E ATUALIZAÇÃO
 *
 * Este arquivo vai no Apps Script da PLANILHA AUXILIAR, nao no da
 * planilha principal. Sao dois projetos diferentes.
 *
 * O que ele faz:
 *   1. Cria as abas de espelho com a formula IMPORTRANGE ja pronta,
 *      sempre com intervalo delimitado, nunca coluna inteira.
 *   2. Forca a atualizacao do espelho em intervalo fixo. Isso e
 *      necessario porque o IMPORTRANGE so verifica atualizacao a cada
 *      hora enquanto o documento esta aberto, e o painel lendo por API
 *      nao conta como abrir o documento. Sem este gatilho, o espelho
 *      pode ficar horas defasado sem ninguem perceber.
 *   3. Registra na aba CONTROLE_BI o horario da ultima atualizacao e o
 *      estado de cada espelho, para o painel exibir e avisar quando o
 *      dado estiver velho.
 *
 * Instalacao:
 *   Extensoes > Apps Script na planilha auxiliar, colar este arquivo,
 *   preencher ID_PLANILHA_PRINCIPAL, salvar e recarregar a planilha.
 *   Depois: menu Espelho BI > Criar/atualizar abas de espelho, clicar
 *   em Permitir acesso quando o Google pedir, e por fim
 *   menu Espelho BI > Instalar atualizacao automatica.
 * ============================================================
 */

const CONFIG_BI = {
  TIMEZONE: 'America/Sao_Paulo',

  /** ID da planilha de onde vem os dados. Somente o ID, sem URL. */
  ID_PLANILHA_PRINCIPAL: 'COLE_AQUI_O_ID_DA_PLANILHA_PRINCIPAL',

  /** Minutos entre uma atualizacao e outra. Valores aceitos: 1, 5, 10, 15, 30. */
  INTERVALO_MINUTOS: 10,

  ABA_CONTROLE: 'CONTROLE_BI',

  /**
   * Intervalos delimitados de proposito. Coluna inteira (A:Z) traz
   * milhoes de celulas vazias e recria, na auxiliar, o mesmo problema
   * de limite que estamos resolvendo na principal.
   */
  ESPELHOS: [
    { aba: 'BI_PRAZOS',   origem: 'CONTROLE DE PRAZOS',   intervalo: 'A1:L10000' },
    { aba: 'BI_CLIENTES', origem: 'CONTROLE DE CLIENTES', intervalo: 'A1:BZ10000' }
  ]
};

function onOpen() {
  SpreadsheetApp.getUi()
    .createMenu('Espelho BI')
    .addItem('Criar/atualizar abas de espelho', 'montarEspelhos')
    .addItem('Atualizar agora', 'atualizarEspelhos')
    .addSeparator()
    .addItem('Instalar atualização automática', 'instalarGatilhoEspelho')
    .addItem('Remover atualização automática', 'removerGatilhoEspelho')
    .addSeparator()
    .addItem('Diagnosticar planilha de origem', 'diagnosticarOrigem')
    .addItem('Conferir estado dos espelhos', 'conferirEspelhos')
    .addToUi();
}

function BI_VALIDAR_ID_() {
  const id = CONFIG_BI.ID_PLANILHA_PRINCIPAL;
  if (!id || id.indexOf('COLE_AQUI') === 0) {
    throw new Error('Preencha ID_PLANILHA_PRINCIPAL no topo deste arquivo.');
  }
  return id;
}

function BI_FORMULA_(espelho) {
  const url = 'https://docs.google.com/spreadsheets/d/' + BI_VALIDAR_ID_();
  const intervalo = "'" + espelho.origem + "'!" + espelho.intervalo;
  return '=IMPORTRANGE("' + url + '";"' + intervalo + '")';
}

function BI_GARANTIR_ABA_(ss, nome) {
  let sheet = ss.getSheetByName(nome);
  if (!sheet) sheet = ss.insertSheet(nome);
  return sheet;
}

/**
 * Diagnostico da origem. Rodar SEMPRE antes de montar os espelhos.
 *
 * O #REF! do IMPORTRANGE tem tres causas possiveis e elas se parecem
 * na tela. Esta funcao separa as tres:
 *   1. ID errado          -> a planilha nem abre
 *   2. Nome de aba errado -> a planilha abre mas a aba nao existe
 *   3. Falta autorizacao  -> tudo certo, so falta clicar em Permitir
 *                            acesso, o que so pode ser feito na tela
 *
 * O script roda sob a sua autorizacao, entao ele consegue abrir a
 * origem por ID e listar as abas mesmo antes de o IMPORTRANGE estar
 * autorizado. E o jeito de saber se o problema e configuracao ou
 * apenas o clique que falta.
 */
function diagnosticarOrigem() {
  const ui = SpreadsheetApp.getUi();
  const id = String(CONFIG_BI.ID_PLANILHA_PRINCIPAL || '').trim();

  if (!id || id.indexOf('COLE_AQUI') === 0) {
    ui.alert('Preencha ID_PLANILHA_PRINCIPAL no topo deste arquivo.');
    return;
  }

  if (id.indexOf('http') === 0 || id.indexOf('/') !== -1) {
    ui.alert(
      'ID inválido.\n\nVocê colou a URL inteira. Use apenas o trecho ' +
      'entre /d/ e /edit.\n\nValor atual:\n' + id
    );
    return;
  }

  let origem;
  try {
    origem = SpreadsheetApp.openById(id);
  } catch (erro) {
    ui.alert(
      'NÃO FOI POSSÍVEL ABRIR A PLANILHA DE ORIGEM\n\n' +
      'ID configurado:\n' + id + '\n\n' +
      'Isso significa que o ID está errado ou que a sua conta não tem ' +
      'acesso a essa planilha. Confira o ID na URL da planilha ' +
      'principal, entre /d/ e /edit.\n\nDetalhe técnico: ' + erro.message
    );
    return;
  }

  const abas = origem.getSheets().map(function(sheet) {
    return sheet.getName();
  });

  let mensagem = 'ORIGEM ENCONTRADA\n\n';
  mensagem += 'Planilha: ' + origem.getName() + '\n';
  mensagem += 'ID: ' + id + '\n\n';
  mensagem += 'ABAS EXISTENTES (' + abas.length + '):\n';
  mensagem += abas.join('\n') + '\n\n';
  mensagem += 'ABAS QUE ESTE SCRIPT PROCURA:\n';

  let faltando = 0;
  CONFIG_BI.ESPELHOS.forEach(function(espelho) {
    const sheet = origem.getSheetByName(espelho.origem);
    if (sheet) {
      mensagem += '[OK] ' + espelho.origem + ' — ' +
        sheet.getLastRow() + ' linha(s), ' +
        sheet.getLastColumn() + ' coluna(s)\n';
    } else {
      faltando++;
      mensagem += '[FALTA] ' + espelho.origem +
        ' — não existe com esse nome exato\n';
    }
  });

  if (faltando) {
    mensagem += '\nCorrija o campo "origem" em CONFIG_BI.ESPELHOS usando ' +
      'o nome exato da aba, como aparece na lista acima.';
  } else {
    mensagem += '\nConfiguração correta. Se ainda aparecer #REF! nas abas ' +
      'de espelho, falta apenas autorizar: abra a aba, clique na célula ' +
      'A1 e use o botão PERMITIR ACESSO. Esse clique não pode ser feito ' +
      'por script.';
  }

  ui.alert(mensagem);
}

/** Cria as abas de espelho e grava a formula em A1 de cada uma. */
function montarEspelhos() {
  const ss = SpreadsheetApp.getActiveSpreadsheet();

  CONFIG_BI.ESPELHOS.forEach(function(espelho) {
    const sheet = BI_GARANTIR_ABA_(ss, espelho.aba);
    sheet.clearContents();
    sheet.getRange('A1').setFormula(BI_FORMULA_(espelho));
  });

  BI_GARANTIR_ABA_(ss, CONFIG_BI.ABA_CONTROLE);
  SpreadsheetApp.flush();

  SpreadsheetApp.getUi().alert(
    'Abas de espelho criadas.\n\n' +
    'Se aparecer #REF! nas abas, siga esta ordem:\n\n' +
    '1. Clique na célula A1 da aba. Se surgir o botão PERMITIR ACESSO, ' +
    'clique nele. É pedido uma única vez por par de planilhas e não ' +
    'pode ser feito por script.\n\n' +
    '2. Se não surgir esse botão, o problema é de configuração. Use ' +
    'Espelho BI > Diagnosticar planilha de origem.\n\n' +
    'Depois, use Espelho BI > Instalar atualização automática.'
  );
}

/**
 * Forca o recalculo do IMPORTRANGE.
 *
 * O Google so reavalia a formula quando ela muda ou quando o documento
 * e aberto. Limpar a celula e reescrever a mesma formula e o caminho
 * suportado para provocar a reavaliacao por script.
 */
function atualizarEspelhos() {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  const estados = [];

  CONFIG_BI.ESPELHOS.forEach(function(espelho) {
    const sheet = BI_GARANTIR_ABA_(ss, espelho.aba);
    const celula = sheet.getRange('A1');

    celula.clearContent();
    SpreadsheetApp.flush();
    celula.setFormula(BI_FORMULA_(espelho));
    SpreadsheetApp.flush();

    const valor = String(celula.getDisplayValue() || '').trim();
    let situacao = 'OK';

    if (valor.indexOf('#REF') === 0) {
      // #REF pode ser falta de autorizacao, ID errado ou nome de aba
      // errado. O menu Diagnosticar planilha de origem separa os casos.
      situacao = '#REF! — falta autorizar (clique em A1 e em Permitir ' +
        'acesso) ou o ID/nome da aba está errado. Rode o diagnóstico.';
    } else if (valor.indexOf('#ERROR') === 0 || valor.indexOf('#N/A') === 0) {
      situacao = 'ERRO NA FÓRMULA: ' + valor;
    } else if (!valor) {
      situacao = 'VAZIO. Confira o nome da aba de origem e o intervalo.';
    } else {
      situacao = 'OK — ' + Math.max(sheet.getLastRow() - 1, 0) + ' linha(s)';
    }

    estados.push([espelho.aba, espelho.origem + '!' + espelho.intervalo, situacao]);
  });

  BI_REGISTRAR_CONTROLE_(ss, estados);
}

/**
 * Grava o horario da ultima atualizacao e o estado de cada espelho.
 * O painel le esta aba para mostrar a idade real do dado.
 */
function BI_REGISTRAR_CONTROLE_(ss, estados) {
  const sheet = BI_GARANTIR_ABA_(ss, CONFIG_BI.ABA_CONTROLE);
  sheet.clearContents();

  const agora = new Date();
  const linhas = [
    ['CHAVE', 'VALOR', 'DETALHE'],
    [
      'ATUALIZADO_EM',
      Utilities.formatDate(agora, CONFIG_BI.TIMEZONE, 'dd/MM/yyyy HH:mm:ss'),
      'Última atualização forçada do espelho'
    ],
    ['INTERVALO_MINUTOS', CONFIG_BI.INTERVALO_MINUTOS, 'Frequência do gatilho']
  ];

  estados.forEach(function(estado) {
    linhas.push(['ESPELHO_' + estado[0], estado[2], estado[1]]);
  });

  sheet.getRange(1, 1, linhas.length, 3).setValues(linhas);
  sheet.getRange(1, 1, 1, 3).setFontWeight('bold');
  SpreadsheetApp.flush();
}

function instalarGatilhoEspelho() {
  removerGatilhoEspelho();

  ScriptApp.newTrigger('atualizarEspelhos')
    .timeBased()
    .everyMinutes(CONFIG_BI.INTERVALO_MINUTOS)
    .create();

  SpreadsheetApp.getUi().alert(
    'Atualização automática instalada a cada ' +
    CONFIG_BI.INTERVALO_MINUTOS + ' minuto(s).'
  );
}

function removerGatilhoEspelho() {
  ScriptApp.getProjectTriggers().forEach(function(gatilho) {
    if (gatilho.getHandlerFunction() === 'atualizarEspelhos') {
      ScriptApp.deleteTrigger(gatilho);
    }
  });
}

function conferirEspelhos() {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  let mensagem = 'ESTADO DOS ESPELHOS\n\n';

  CONFIG_BI.ESPELHOS.forEach(function(espelho) {
    const sheet = ss.getSheetByName(espelho.aba);
    if (!sheet) {
      mensagem += espelho.aba + ': aba não existe.\n';
      return;
    }
    const valor = String(sheet.getRange('A1').getDisplayValue() || '').trim();
    mensagem += espelho.aba + ': ' +
      Math.max(sheet.getLastRow() - 1, 0) + ' linha(s), ' +
      Math.max(sheet.getLastColumn(), 0) + ' coluna(s)' +
      (valor.indexOf('#') === 0 ? ' — ' + valor : '') + '\n';
  });

  const gatilhos = ScriptApp.getProjectTriggers().filter(function(g) {
    return g.getHandlerFunction() === 'atualizarEspelhos';
  });

  mensagem += '\nGatilho de atualização: ' +
    (gatilhos.length ? 'instalado' : 'NÃO instalado');

  SpreadsheetApp.getUi().alert(mensagem);
}
