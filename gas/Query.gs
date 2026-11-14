/**
 * SOQL の実行と、結果のスプレッドシートへの書き出し。
 */

/** GASの実行上限は6分。余裕を見て打ち切る（ミリ秒） */
var TIME_BUDGET_MS = 4.5 * 60 * 1000;

/** 一度に書き込む行数。大量件数で setValues を分割するため */
var WRITE_CHUNK = 2000;

/**
 * SOQL を実行してレコードを取得する。
 *
 * Salesforce は 2000 件ずつ返し、続きを nextRecordsUrl で示す。
 * 件数上限と時間上限のどちらかに達したら、そこで打ち切って知らせる。
 *
 * @param {string} soql 実行する SOQL
 * @return {{records: Array<Object>, truncated: boolean, reason: string}}
 */
function runSoql(soql) {
  var cfg = getConfig();
  var started = new Date().getTime();

  var path = '/services/data/v' + cfg.apiVersion + '/query/?q=' + encodeURIComponent(soql);
  var records = [];
  var truncated = false;
  var reason = '';

  while (path) {
    var data = salesforceGet(path);
    records = records.concat(data.records || []);

    if (records.length >= cfg.maxRecords) {
      records = records.slice(0, cfg.maxRecords);
      truncated = true;
      reason = '最大取得件数（' + cfg.maxRecords + '件）に達したため、ここまでを出力しました。';
      break;
    }
    if (new Date().getTime() - started > TIME_BUDGET_MS) {
      truncated = true;
      reason = 'GASの実行時間の上限が近いため、ここまでを出力しました。'
             + 'SOQL に WHERE 条件を足して件数を絞ってください。';
      break;
    }

    path = data.nextRecordsUrl || null;  // 相対パスがそのまま返る
  }

  return { records: records, truncated: truncated, reason: reason };
}

/**
 * レコードを "RecordType.DeveloperName" のようなドット表記に平坦化する。
 *
 * Salesforce はリレーション項目を入れ子のオブジェクトで返すため、
 * そのままでは表に並べられない。
 *
 * @param {Object} record 1レコード
 * @param {string=} prefix 再帰用の接頭辞
 * @return {Object} 平坦化した {列名: 値}
 */
function flattenRecord(record, prefix) {
  prefix = prefix || '';
  var flat = {};

  for (var key in record) {
    if (!record.hasOwnProperty(key) || key === 'attributes') {
      continue;  // attributes は型とURLのメタ情報なので出さない
    }
    var value = record[key];
    var name = prefix + key;

    if (value !== null && typeof value === 'object' && !Array.isArray(value)) {
      var nested = flattenRecord(value, name + '.');
      for (var k in nested) {
        if (nested.hasOwnProperty(k)) { flat[k] = nested[k]; }
      }
    } else if (Array.isArray(value)) {
      // 子リレーション（サブクエリ）は件数だけ示す。表には展開できないため
      flat[name] = '[' + value.length + '件]';
    } else {
      flat[name] = value;
    }
  }
  return flat;
}

/**
 * レコード群から、列の並びと表データを作る。
 *
 * 列は SOQL が返した項目に追随する（レコードごとに項目が欠けても揃う）。
 *
 * @param {Array<Object>} records
 * @return {{columns: Array<string>, rows: Array<Array>}}
 */
function buildTable(records) {
  var columns = [];
  var flats = records.map(function (rec) {
    var flat = flattenRecord(rec);
    for (var key in flat) {
      if (flat.hasOwnProperty(key) && columns.indexOf(key) === -1) {
        columns.push(key);
      }
    }
    return flat;
  });

  var rows = flats.map(function (flat) {
    return columns.map(function (col) {
      var v = flat[col];
      if (v === null || v === undefined) { return ''; }
      // 文字列として扱いたい値をスプレッドシートが数式や日付に変換しないようにする
      return (typeof v === 'object') ? JSON.stringify(v) : v;
    });
  });

  return { columns: columns, rows: rows };
}

/**
 * 結果を出力先シートに書き出す。既存の内容は消してから書く。
 *
 * @param {{columns: Array<string>, rows: Array<Array>}} table
 * @param {string} sheetName
 * @return {Sheet}
 */
function writeResults(table, sheetName) {
  var ss = SpreadsheetApp.getActiveSpreadsheet();
  var sheet = ss.getSheetByName(sheetName) || ss.insertSheet(sheetName);
  sheet.clear();

  if (!table.columns.length) {
    sheet.getRange(1, 1).setValue('該当するレコードがありません。');
    return sheet;
  }

  sheet.getRange(1, 1, 1, table.columns.length)
    .setValues([table.columns])
    .setFontWeight('bold')
    .setBackground('#2c313a')
    .setFontColor('#ffffff');

  // 大量件数で setValues が重くならないよう分割して書く
  for (var start = 0; start < table.rows.length; start += WRITE_CHUNK) {
    var chunk = table.rows.slice(start, start + WRITE_CHUNK);
    sheet.getRange(2 + start, 1, chunk.length, table.columns.length).setValues(chunk);
  }

  sheet.setFrozenRows(1);
  sheet.autoResizeColumns(1, Math.min(table.columns.length, 20));
  return sheet;
}
