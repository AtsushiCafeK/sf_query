/**
 * スプレッドシート上の操作（メニュー・実行の入口）。
 *
 * 使い方:
 *   設定シートの「SOQL」にクエリを書き、メニューの
 *   「Salesforce」→「SOQLを実行」を選ぶと、結果が出力先シートに並ぶ。
 */

/**
 * スプレッドシートを開いたときにメニューを作る。
 */
function onOpen() {
  SpreadsheetApp.getUi()
    .createMenu('Salesforce')
    .addItem('SOQLを実行', 'runQuery')
    .addSeparator()
    .addItem('Salesforceに接続', 'showAuthorizationDialog')
    .addItem('接続状態を確認', 'showConnectionStatus')
    .addItem('認証を解除', 'disconnect')
    .addSeparator()
    .addItem('コールバックURLを表示', 'showRedirectUri')
    .addItem('設定シートを作成', 'setupConfigSheet')
    .addToUi();
}

/**
 * 設定シートを用意する（初回セットアップ用）。
 */
function setupConfigSheet() {
  var sheet = getOrCreateConfigSheet();
  SpreadsheetApp.getActiveSpreadsheet().setActiveSheet(sheet);
  SpreadsheetApp.getUi().alert(
    '設定シート',
    '「設定」シートを用意しました。\n\n' +
    'コンシューマー鍵とSOQLを入力してから、\n' +
    'メニューの「Salesforceに接続」→「SOQLを実行」と進んでください。',
    SpreadsheetApp.getUi().ButtonSet.OK
  );
}

/**
 * 認可画面へのリンクを出す。利用者が自分のSalesforceアカウントでログインする。
 */
function showAuthorizationDialog() {
  var ui = SpreadsheetApp.getUi();
  try {
    var service = getSalesforceService();
    if (service.hasAccess()) {
      ui.alert('接続済み', 'すでに接続されています。\n別のアカウントで入り直す場合は「認証を解除」してください。', ui.ButtonSet.OK);
      return;
    }

    var html = HtmlService.createHtmlOutput(
      '<p style="font-family:sans-serif; font-size:14px; line-height:1.7;">' +
      '下のリンクを開いて、Salesforce にログインしてください。<br>' +
      '許可が終わったらこのダイアログを閉じて、もう一度「SOQLを実行」を選びます。</p>' +
      '<p><a href="' + service.getAuthorizationUrl() + '" target="_blank" ' +
      'style="font-family:sans-serif; font-size:15px;">Salesforce にログインする</a></p>'
    ).setWidth(420).setHeight(160);

    ui.showModalDialog(html, 'Salesforce に接続');
  } catch (e) {
    ui.alert('設定を確認してください', e.message, ui.ButtonSet.OK);
  }
}

/**
 * いま接続できているかを表示する。
 */
function showConnectionStatus() {
  var ui = SpreadsheetApp.getUi();
  try {
    var service = getSalesforceService();
    if (!service.hasAccess()) {
      ui.alert('接続状態', '未接続です。\n「Salesforceに接続」から認証してください。', ui.ButtonSet.OK);
      return;
    }
    var info = salesforceGet('/services/oauth2/userinfo');
    ui.alert(
      '接続状態',
      '接続中です。\n\nユーザー: ' + (info.preferred_username || info.email || '(不明)') +
      '\n組織: ' + (getSalesforceService().getToken().instance_url || ''),
      ui.ButtonSet.OK
    );
  } catch (e) {
    ui.alert('接続状態', e.message, ui.ButtonSet.OK);
  }
}

/**
 * 認証を解除する。
 */
function disconnect() {
  var ui = SpreadsheetApp.getUi();
  logout();
  ui.alert('認証を解除', '認証を解除しました。次回は再度ログインが必要です。', ui.ButtonSet.OK);
}

/**
 * コールバックURLを表示する。接続アプリに登録する値。
 */
function showRedirectUri() {
  var ui = SpreadsheetApp.getUi();
  try {
    ui.alert(
      'コールバックURL',
      'この値を接続アプリ（外部クライアントアプリケーション）の\n' +
      'コールバックURLに追加してください。\n\n' + getRedirectUri(),
      ui.ButtonSet.OK
    );
  } catch (e) {
    ui.alert('コールバックURL', e.message, ui.ButtonSet.OK);
  }
}

/**
 * 設定シートのSOQLを実行し、結果を出力先シートに書き出す。
 */
function runQuery() {
  var ui = SpreadsheetApp.getUi();
  var ss = SpreadsheetApp.getActiveSpreadsheet();

  try {
    var soql = getValidatedSoql();          // 打ち間違いはここで止める
    var cfg = getConfig();

    if (!getSalesforceService().hasAccess()) {
      showAuthorizationDialog();
      return;
    }

    ss.toast('Salesforce に問い合わせています…', 'SOQL実行', -1);
    var result = runSoql(soql);

    var table = buildTable(result.records);
    var sheet = writeResults(table, cfg.outputSheet);
    ss.setActiveSheet(sheet);
    ss.toast(result.records.length + ' 件を取得しました。', 'SOQL実行', 5);

    if (result.truncated) {
      ui.alert('途中で打ち切りました', result.reason, ui.ButtonSet.OK);
    }
  } catch (e) {
    ss.toast('', 'SOQL実行', 1);  // 実行中トーストを消す
    ui.alert('エラー', e.message, ui.ButtonSet.OK);
  }
}
