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
    .addItem('登録シートを用意', 'setupImportSheet')
    .addItem('一括登録を確認（ドライラン）', 'dryRunImport')
    .addItem('一括登録を実行', 'runImport')
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

    var reason = authFailureReason_(service);
    var reasonHtml = reason
      ? '<p style="font-family:sans-serif; font-size:12px; color:#b00020;">' +
        '直前の認証の更新に失敗しています:<br>' + escapeHtml_(reason) + '</p>'
      : '';

    var html = HtmlService.createHtmlOutput(
      reasonHtml +
      '<p style="font-family:sans-serif; font-size:14px; line-height:1.7;">' +
      '下のリンクを開いて、Salesforce にログインしてください。<br>' +
      '許可が終わったらこのダイアログを閉じて、もう一度「SOQLを実行」を選びます。</p>' +
      '<p><a href="' + service.getAuthorizationUrl() + '" target="_blank" ' +
      'style="font-family:sans-serif; font-size:15px;">Salesforce にログインする</a></p>'
    ).setWidth(460).setHeight(reason ? 260 : 160);

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
      var why = authFailureReason_(service);
      ui.alert('接続状態', '未接続です。\n「Salesforceに接続」から認証してください。' +
        (why ? '\n\n直前の認証の更新に失敗しています:\n' + why : ''), ui.ButtonSet.OK);
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

// ---------------------------------------------------------------------------
// 一括登録（プロトタイプ）
// ---------------------------------------------------------------------------

/**
 * 登録せずに、何が作られるかだけ確認する。
 *
 * 書き込みはやり直しが効かないため、まずこちらで中身を確かめる。
 */
function dryRunImport() {
  var ui = SpreadsheetApp.getUi();
  try {
    var prepared = prepareImport_();
    if (prepared.problems.length) {
      ui.alert('登録できない内容があります（送信していません）',
        formatProblems_(prepared.problems), ui.ButtonSet.OK);
      return;
    }

    ui.alert('ドライランの結果',
      '問題は見つかりませんでした。送信はしていません。\n\n' +
      '対象オブジェクト: ' + prepared.objectName + '\n' +
      '登録される件数  : ' + prepared.records.length + ' 件\n\n' +
      '--- 1件目の内容 ---\n' + previewRecord_(prepared.records[0]) +
      '\n\n問題なければ「一括登録を実行」を選んでください。',
      ui.ButtonSet.OK);
  } catch (e) {
    ui.alert('エラー', e.message, ui.ButtonSet.OK);
  }
}

/**
 * シートの内容を Salesforce に登録する。
 */
function runImport() {
  var ui = SpreadsheetApp.getUi();
  var ss = SpreadsheetApp.getActiveSpreadsheet();

  try {
    var prepared = prepareImport_();
    if (prepared.problems.length) {
      ui.alert('登録できない内容があります（送信していません）',
        formatProblems_(prepared.problems), ui.ButtonSet.OK);
      return;
    }

    var answer = ui.alert('一括登録の確認',
      prepared.objectName + ' に ' + prepared.records.length + ' 件のレコードを作成します。\n\n' +
      'この操作は取り消せません。\n' +
      '（1件でも失敗した場合は、全件が取り消されます）\n\n' +
      '実行してよろしいですか？',
      ui.ButtonSet.YES_NO);
    if (answer !== ui.Button.YES) { return; }

    ss.toast('Salesforce に登録しています…', '一括登録', -1);
    var results = createRecords(prepared.records);
    ss.toast('', '一括登録', 1);

    var summary = writeImportResults_(prepared, results);
    if (summary.failed === 0) {
      ui.alert('登録しました',
        summary.created + ' 件を作成しました。\n' +
        '各行の「' + RESULT_ID_HEADER + '」列に、作成されたレコードIDを書き込みました。',
        ui.ButtonSet.OK);
    } else {
      ui.alert('登録されませんでした',
        summary.failed + ' 件でエラーがあったため、Salesforce 側で全件取り消されました。\n' +
        '各行の「' + RESULT_STATUS_HEADER + '」列に理由を書き込みました。\n\n' +
        '最初のエラー:\n' + summary.firstError,
        ui.ButtonSet.OK);
    }
  } catch (e) {
    ss.toast('', '一括登録', 1);
    ui.alert('エラー', e.message, ui.ButtonSet.OK);
  }
}

/**
 * シートを読んで検証し、送信できる形にする（ドライランと本番で共通）。
 * @private
 */
function prepareImport_() {
  var cfg = getConfig();
  var input = readImportRows();
  var meta = describeObject(input.objectName);

  if (!input.rows.length) {
    throw new Error('登録するデータがありません。2行目以降に入力してください。');
  }
  if (input.rows.length > cfg.importLimit) {
    throw new Error(
      '一度に登録できるのは ' + cfg.importLimit + ' 件までです（' + input.rows.length + ' 件あります）。\n' +
      '行を分けて実行してください。'
    );
  }

  var validated = validateImportRows(input.rows, meta);
  return {
    objectName: input.objectName,
    sheet: input.sheet,
    headers: input.headers,
    rows: input.rows,
    records: validated.records,
    problems: validated.problems
  };
}

/**
 * 検証結果を読みやすく整える（多すぎる場合は打ち切る）。
 * @private
 */
function formatProblems_(problems) {
  var shown = problems.slice(0, 15);
  var text = shown.join('\n');
  if (problems.length > shown.length) {
    text += '\n… ほか ' + (problems.length - shown.length) + ' 件';
  }
  return text + '\n\n直してから、もう一度実行してください。';
}

/**
 * 1件分の内容を確認用に文字列にする。
 * @private
 */
function previewRecord_(record) {
  var lines = [];
  for (var key in record) {
    if (key === 'attributes' || !record.hasOwnProperty(key)) { continue; }
    lines.push('  ' + key + ': ' + record[key]);
  }
  return lines.join('\n');
}

/**
 * 登録結果を各行に書き戻す。
 *
 * 間違えて登録したときに消せるよう、作成されたIDを必ず残す。
 * @private
 */
function writeImportResults_(prepared, results) {
  var sheet = prepared.sheet;
  var headers = prepared.headers.slice();

  function columnFor(name) {
    var idx = headers.indexOf(name);
    if (idx === -1) {
      headers.push(name);
      idx = headers.length - 1;
      sheet.getRange(1, idx + 1).setValue(name).setFontWeight('bold')
        .setBackground('#2c313a').setFontColor('#ffffff');
    }
    return idx + 1;
  }

  var idCol = columnFor(RESULT_ID_HEADER);
  var statusCol = columnFor(RESULT_STATUS_HEADER);

  var created = 0, failed = 0, firstError = '';
  var stamp = Utilities.formatDate(new Date(), Session.getScriptTimeZone(), 'yyyy-MM-dd HH:mm');

  results.forEach(function (res, i) {
    var row = prepared.rows[i].rowNumber;
    if (res.success) {
      created++;
      sheet.getRange(row, idCol).setValue(res.id);
      sheet.getRange(row, statusCol).setValue('登録成功 ' + stamp);
    } else {
      failed++;
      var msg = (res.errors || []).map(function (e) {
        return (e.statusCode ? '[' + e.statusCode + '] ' : '') + (e.message || '');
      }).join(' / ') || 'エラー';
      // allOrNone のため、他行の失敗で巻き添えになったものと区別する
      if (msg.indexOf('ALL_OR_NONE') !== -1 || msg.indexOf('PROCESSING_HALTED') !== -1) {
        msg = '他の行のエラーにより取り消されました';
      } else if (!firstError) {
        firstError = prepared.rows[i].rowNumber + '行目: ' + msg;
      }
      sheet.getRange(row, statusCol).setValue(msg);
    }
  });

  return { created: created, failed: failed, firstError: firstError || '(なし)' };
}
