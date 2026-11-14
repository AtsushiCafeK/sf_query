/**
 * 設定シートの読み書き。
 *
 * 設定は「設定」という名前のシートに置く。初回実行時に自動で作られるので、
 * 利用者は空のスプレッドシートを用意するだけでよい。
 *
 * コンシューマー鍵について:
 *   PKCE の構成では、鍵（client_id）は秘密情報ではなく、これだけでは
 *   誰もログインできない（各自のSalesforceログインが必要）。
 *   そのためシートに置いて差し支えないが、スクリプトプロパティに
 *   SALESFORCE_CLIENT_ID を設定すればそちらが優先される。
 */

var CONFIG_SHEET_NAME = '設定';
var DEFAULT_OUTPUT_SHEET = '結果';
var DEFAULT_API_VERSION = '60.0';

/** 設定シートの行定義。[ラベル, 既定値, 補足] */
var CONFIG_ROWS = [
  ['ログインURL', 'https://login.salesforce.com',
   '本番はこのまま。Sandbox は https://test.salesforce.com。My Domain の組織は https://<ドメイン>.my.salesforce.com'],
  ['コンシューマー鍵', '',
   '接続アプリ（外部クライアントアプリ）のコンシューマー鍵を貼り付け'],
  ['APIバージョン', DEFAULT_API_VERSION,
   '通常は変更不要。親→子の入れ子クエリを5階層まで使うには 58.0 以上'],
  ['出力先シート名', DEFAULT_OUTPUT_SHEET, '結果を書き出すシート。無ければ作成される'],
  ['最大取得件数', '5000',
   '安全のための上限。GASは1回の実行が6分までのため、大量件数は分けて取得する'],
  ['SOQL', 'SELECT Id, Name FROM Account ORDER BY Name LIMIT 100',
   'ここに実行したいSOQLを書く（複数行可）']
];

/** SOQL を書く行（1始まり。見出し行の次から数える） */
var SOQL_ROW_INDEX = 6;

/**
 * 設定シートを取得する。無ければ作って初期値を入れる。
 * @return {Sheet}
 */
function getOrCreateConfigSheet() {
  var ss = SpreadsheetApp.getActiveSpreadsheet();
  var sheet = ss.getSheetByName(CONFIG_SHEET_NAME);
  if (sheet) {
    return sheet;
  }

  sheet = ss.insertSheet(CONFIG_SHEET_NAME, 0);
  sheet.getRange('A1:C1')
    .setValues([['設定項目', '値', '説明']])
    .setFontWeight('bold')
    .setBackground('#2c313a')
    .setFontColor('#ffffff');

  var rows = CONFIG_ROWS.map(function (r) { return [r[0], r[1], r[2]]; });
  // 「60.0」が数値の 60 に変換されないよう、値の列は書式を文字列にしてから書く
  sheet.getRange(2, 2, rows.length, 1).setNumberFormat('@');
  sheet.getRange(2, 1, rows.length, 3).setValues(rows);

  sheet.getRange(2, 1, rows.length, 1).setFontWeight('bold');
  sheet.setColumnWidth(1, 160);
  sheet.setColumnWidth(2, 420);
  sheet.setColumnWidth(3, 460);
  // SOQL は複数行になることがあるので折り返す
  sheet.getRange(SOQL_ROW_INDEX + 1, 2).setWrap(true);
  sheet.getRange(2, 3, rows.length, 1).setFontColor('#666666');
  sheet.setFrozenRows(1);

  return sheet;
}

/**
 * "60" や 60 を "60.0" の形に直す。
 * シートが数値に変換しても、URLが /v60/ にならないようにする。
 * @param {*} value
 * @return {string}
 */
function normalizeApiVersion_(value) {
  var n = parseFloat(value);
  return isNaN(n) ? DEFAULT_API_VERSION : n.toFixed(1);
}

/**
 * 設定シートの値を読む。
 * @return {{loginUrl: string, clientId: string, apiVersion: string,
 *           outputSheet: string, maxRecords: number, soql: string}}
 */
function getConfig() {
  var sheet = getOrCreateConfigSheet();
  var values = sheet.getRange(2, 2, CONFIG_ROWS.length, 1).getValues();

  var raw = {};
  CONFIG_ROWS.forEach(function (row, i) {
    raw[row[0]] = String(values[i][0] == null ? '' : values[i][0]).trim();
  });

  // スクリプトプロパティがあればそちらを優先（共有シートで鍵を隠したい場合）
  var fromProps = PropertiesService.getScriptProperties()
    .getProperty('SALESFORCE_CLIENT_ID');

  var loginUrl = (raw['ログインURL'] || 'https://login.salesforce.com').replace(/\/+$/, '');
  var clientId = fromProps || raw['コンシューマー鍵'];
  var maxRecords = parseInt(raw['最大取得件数'], 10);

  if (!clientId) {
    throw new Error(
      '設定シートの「コンシューマー鍵」が空です。接続アプリのコンシューマー鍵を入力してください。'
    );
  }
  if (loginUrl.indexOf('https://') !== 0) {
    throw new Error('ログインURLは https:// で始まる必要があります: ' + loginUrl);
  }

  return {
    loginUrl: loginUrl,
    clientId: clientId,
    apiVersion: normalizeApiVersion_(raw['APIバージョン']),
    outputSheet: raw['出力先シート名'] || DEFAULT_OUTPUT_SHEET,
    maxRecords: (isNaN(maxRecords) || maxRecords <= 0) ? 5000 : maxRecords,
    soql: raw['SOQL']
  };
}

/**
 * 設定シートに書かれた SOQL を取り出して検証する。
 *
 * SOQL は SELECT しかない読み取り専用の言語で、各自のOAuthトークンで動くため
 * その人が見られるデータしか取れない。ここでは打ち間違いを早く知らせる目的で
 * 最低限の形だけ確認する。
 *
 * @return {string} 実行する SOQL
 */
function getValidatedSoql() {
  var soql = String(getConfig().soql || '').trim().replace(/;+$/, '').trim();

  if (!soql) {
    throw new Error('設定シートの「SOQL」が空です。実行したいクエリを入力してください。');
  }
  if (!/^SELECT\b/i.test(soql)) {
    throw new Error('SOQL は SELECT で始まる必要があります。\n入力された内容: ' + soql.slice(0, 80));
  }
  if (!/\bFROM\b/i.test(soql)) {
    throw new Error('SOQL に FROM 句がありません。\n入力された内容: ' + soql.slice(0, 80));
  }
  return soql;
}
