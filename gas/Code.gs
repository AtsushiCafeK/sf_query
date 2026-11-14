/**
 * スプレッドシートのメニューと、メニューから呼ばれる処理。
 */

function onOpen() {
  SpreadsheetApp.getUi()
    .createMenu('Salesforce')
    .addItem('Salesforceに接続', 'connectSalesforce')
    .addItem('SOQLを実行', 'runQueryToSheet')
    .addSeparator()
    .addItem('コールバックURLを表示', 'showRedirectUri')
    .addItem('認証を解除', 'logout')
    .addToUi();
}

/** 認可用のリンクをダイアログで表示する。 */
function connectSalesforce() {
  var ui = SpreadsheetApp.getUi();
  var service = getSalesforceService();
  if (service.hasAccess()) {
    ui.alert('すでに接続済みです。');
    return;
  }
  var url = service.getAuthorizationUrl().replace(/&/g, '&amp;');
  var html = HtmlService.createHtmlOutput(
    '<p style="font-family:sans-serif"><a href="' + url +
    '" target="_blank">ここをクリックしてSalesforceにログイン</a></p>'
  ).setWidth(360).setHeight(90);
  ui.showModalDialog(html, 'Salesforceに接続');
}

/** 設定シートのSOQLを実行して、結果シートに書き出す。 */
function runQueryToSheet() {
  var ui = SpreadsheetApp.getUi();
  try {
    var cfg = getConfig();
    var result = runSoql(getValidatedSoql());
    writeResults(buildTable(result.records), cfg.outputSheet);
    var msg = result.records.length + '件を「' + cfg.outputSheet + '」に出力しました。';
    if (result.truncated) {
      msg += '\n' + result.reason;
    }
    ui.alert(msg);
  } catch (e) {
    ui.alert('エラー', e.message, ui.ButtonSet.OK);
  }
}

/** 接続アプリ（外部クライアントアプリ）に登録するコールバックURLを表示する。 */
function showRedirectUri() {
  var ui = SpreadsheetApp.getUi();
  ui.alert('コールバックURL', getRedirectUri(), ui.ButtonSet.OK);
}
