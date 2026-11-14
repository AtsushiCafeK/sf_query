/**
 * スプレッドシートの行を Salesforce に一括登録する（プロトタイプ）。
 *
 * 毎月まとめて稟議書を作るような作業を想定。
 * composite/sobjects を使い、最大200件を1リクエストで作成する。
 *
 * 安全のための決めごと:
 *   - 送信前にシート側で検証し、問題があれば1件も送らない
 *   - allOrNone = true。1件でも失敗したら Salesforce 側で全件取り消し
 *   - ドライランで「何が作られるか」だけ先に確認できる
 *   - 作成されたIDを各行に書き戻す（間違えたときに消せるようにするため）
 *
 * ※ 本番のTeamSpirit稟議は「申請」であり承認フローを伴う。APIで作った
 *   レコードが申請として成立するかは別途検証が必要（README参照）。
 */

/** composite/sobjects の1回あたりの上限（Salesforce仕様） */
var COMPOSITE_LIMIT = 200;

/** 取り込みシートの既定名 */
var IMPORT_SHEET_NAME = '登録データ';

/** 書き戻す列の見出し */
var RESULT_ID_HEADER = '作成されたID';
var RESULT_STATUS_HEADER = '登録結果';

/**
 * 取り込みシートを用意する。対象オブジェクトの必須項目から列を作る。
 */
function setupImportSheet() {
  var ui = SpreadsheetApp.getUi();
  try {
    var cfg = getConfig();
    var meta = describeObject(cfg.importObject);
    var ss = SpreadsheetApp.getActiveSpreadsheet();

    var sheet = ss.getSheetByName(IMPORT_SHEET_NAME);
    if (sheet) {
      var ok = ui.alert('確認',
        '「' + IMPORT_SHEET_NAME + '」シートはすでにあります。\n列見出しを作り直しますか？（データは消えます）',
        ui.ButtonSet.YES_NO);
      if (ok !== ui.Button.YES) { return; }
      sheet.clear();
    } else {
      sheet = ss.insertSheet(IMPORT_SHEET_NAME);
    }

    // 必須項目を先に、その後に任意項目を並べる
    var required = meta.createable.filter(function (f) { return f.required; });
    var optional = meta.createable.filter(function (f) { return !f.required && f.recommended; });
    var headers = required.concat(optional).map(function (f) { return f.name; });

    sheet.getRange(1, 1, 1, headers.length)
      .setValues([headers])
      .setFontWeight('bold')
      .setBackground('#2c313a')
      .setFontColor('#ffffff');

    // 2行目に、項目の説明と選択肢を注記として入れる
    var notes = required.concat(optional).map(function (f) {
      var note = f.label + '（' + f.type + '）';
      if (f.required) { note += '\n★必須'; }
      if (f.picklist && f.picklist.length) {
        note += '\n選択肢: ' + f.picklist.join(' / ');
      }
      return note;
    });
    sheet.getRange(1, 1, 1, headers.length).setNotes([notes]);

    sheet.setFrozenRows(1);
    sheet.autoResizeColumns(1, headers.length);
    ss.setActiveSheet(sheet);

    ui.alert('取り込みシートを用意しました',
      '「' + IMPORT_SHEET_NAME + '」シートの2行目以降にデータを入れてください。\n\n' +
      '列見出しにカーソルを合わせると、項目の説明と選択肢が出ます。\n' +
      '★が付いた列は必須です。',
      ui.ButtonSet.OK);
  } catch (e) {
    ui.alert('エラー', e.message, ui.ButtonSet.OK);
  }
}

/**
 * シートを読み、登録する行の配列にする。
 * @return {{objectName: string, rows: Array<Object>, headers: Array<string>,
 *           sheet: Sheet, startRow: number}}
 */
function readImportRows() {
  var cfg = getConfig();
  var ss = SpreadsheetApp.getActiveSpreadsheet();
  var sheet = ss.getSheetByName(IMPORT_SHEET_NAME);
  if (!sheet) {
    throw new Error(
      '「' + IMPORT_SHEET_NAME + '」シートがありません。' +
      'メニューの「登録シートを用意」から作成してください。'
    );
  }

  var values = sheet.getDataRange().getValues();
  if (values.length < 2) {
    throw new Error('登録するデータがありません。2行目以降に入力してください。');
  }

  var headers = values[0].map(function (h) { return String(h || '').trim(); });
  // 書き戻し用の列は入力として扱わない
  var inputCols = headers.map(function (h, i) {
    return (h === RESULT_ID_HEADER || h === RESULT_STATUS_HEADER || !h) ? null : i;
  }).filter(function (i) { return i !== null; });

  var rows = [];
  for (var r = 1; r < values.length; r++) {
    var record = {};
    var hasValue = false;
    inputCols.forEach(function (i) {
      var v = values[r][i];
      if (v !== '' && v !== null && v !== undefined) { hasValue = true; }
      record[headers[i]] = v;
    });
    if (hasValue) {   // 空行は飛ばす
      rows.push({ rowNumber: r + 1, values: record });
    }
  }

  return {
    objectName: cfg.importObject,
    rows: rows,
    headers: headers,
    sheet: sheet
  };
}

/**
 * 送信前に、シートの内容が登録できる形か確かめる。
 *
 * Salesforce に送ってから弾かれるより、手元で止めたほうが原因が分かりやすい。
 *
 * @param {Array<Object>} rows readImportRows の rows
 * @param {Object} meta describeObject の結果
 * @return {{records: Array<Object>, problems: Array<string>}}
 */
function validateImportRows(rows, meta) {
  var problems = [];
  var byName = {};
  meta.createable.forEach(function (f) { byName[f.name] = f; });

  var records = rows.map(function (row) {
    var out = { attributes: { type: meta.name } };

    // 必須項目の欠けを先に見る
    meta.createable.forEach(function (f) {
      if (!f.required) { return; }
      var v = row.values[f.name];
      if (v === '' || v === null || v === undefined) {
        problems.push(row.rowNumber + '行目: 必須項目「' + f.label + '（' + f.name + '）」が空です。');
      }
    });

    for (var key in row.values) {
      if (!row.values.hasOwnProperty(key)) { continue; }
      var raw = row.values[key];
      if (raw === '' || raw === null || raw === undefined) { continue; }

      var field = byName[key];
      if (!field) {
        problems.push(row.rowNumber + '行目: 「' + key + '」は作成時に書き込めない項目です。列を外してください。');
        continue;
      }
      try {
        out[key] = coerceValue_(field, raw);
      } catch (e) {
        problems.push(row.rowNumber + '行目: ' + e.message);
      }
    }
    return out;
  });

  return { records: records, problems: problems };
}

/**
 * セルの値を、その項目の型に合う形へそろえる。
 * @private
 */
function coerceValue_(field, raw) {
  var label = field.label + '（' + field.name + '）';

  if (field.picklist && field.picklist.length) {
    var s = String(raw).trim();
    if (field.picklist.indexOf(s) === -1) {
      throw new Error('「' + label + '」の値が選択肢にありません: ' + s +
                      '\n  使える値: ' + field.picklist.join(' / '));
    }
    return s;
  }

  switch (field.type) {
    case 'date':
      return formatDate_(raw, label);
    case 'datetime':
      return (raw instanceof Date) ? raw.toISOString() : String(raw).trim();
    case 'currency':
    case 'double':
    case 'int':
    case 'percent':
      var n = (typeof raw === 'number') ? raw : Number(String(raw).replace(/[,\s¥]/g, ''));
      if (isNaN(n)) {
        throw new Error('「' + label + '」は数値で入力してください: ' + raw);
      }
      return n;
    case 'boolean':
      var b = String(raw).trim().toLowerCase();
      return (b === 'true' || b === 'はい' || b === '1' || raw === true);
    default:
      return String(raw);
  }
}

/**
 * 日付を YYYY-MM-DD にそろえる。セルが日付型でも文字列でも受ける。
 * @private
 */
function formatDate_(raw, label) {
  if (raw instanceof Date) {
    return Utilities.formatDate(raw, Session.getScriptTimeZone(), 'yyyy-MM-dd');
  }
  var s = String(raw).trim().replace(/\//g, '-');
  if (!/^\d{4}-\d{1,2}-\d{1,2}$/.test(s)) {
    throw new Error('「' + label + '」は YYYY-MM-DD 形式で入力してください: ' + raw);
  }
  var parts = s.split('-');
  return parts[0] + '-' + ('0' + parts[1]).slice(-2) + '-' + ('0' + parts[2]).slice(-2);
}

/**
 * composite/sobjects でまとめて作成する。
 *
 * @param {Array<Object>} records 作成するレコード（attributes.type 入り）
 * @return {Array<Object>} Salesforce の結果（件数・順序は records と一致）
 */
function createRecords(records) {
  var cfg = getConfig();
  var path = '/services/data/v' + cfg.apiVersion + '/composite/sobjects';

  var payload = {
    allOrNone: true,       // 1件でも失敗したら全件取り消し
    records: records
  };
  return salesforcePost(path, payload);
}
