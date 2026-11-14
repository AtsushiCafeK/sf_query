// 一括登録のロジックを偽の環境で検証する。
//
//   node gas/test/test_import.js
//
// 書き込み機能のため、特に「送ってはいけないものを送らない」ことを重点的に確認する。
// Googleアカウントも Salesforce 接続も不要。

const fs = require('fs');
const path = require('path');
const vm = require('vm');

const GAS_DIR = path.join(__dirname, '..');

// --- 偽のスプレッドシート -------------------------------------------------
const sheets = {};
function fakeSheet(name) {
  const data = [];
  const notes = [];
  const sheet = {
    name, data, notes,
    clear: () => { data.length = 0; },
    getRange(r, c, nr = 1, nc = 1) {
      return {
        setValues(vals) {
          vals.forEach((row, i) => {
            data[r - 1 + i] = data[r - 1 + i] || [];
            row.forEach((v, j) => { data[r - 1 + i][c - 1 + j] = v; });
          });
          return this;
        },
        getValues() {
          const out = [];
          for (let i = 0; i < nr; i++) {
            const row = [];
            for (let j = 0; j < nc; j++) row.push((data[r - 1 + i] || [])[c - 1 + j] ?? '');
            out.push(row);
          }
          return out;
        },
        setValue(v) { data[r - 1] = data[r - 1] || []; data[r - 1][c - 1] = v; return this; },
        setNotes(n) { notes.push(n); return this; },
        setFontWeight() { return this; }, setBackground() { return this; },
        setFontColor() { return this; }, setWrap() { return this; },
      };
    },
    getDataRange() {
      const width = Math.max(...data.map(r => (r || []).length), 1);
      return {
        getValues: () => data.map(r => {
          const row = (r || []).slice();
          while (row.length < width) row.push('');
          return row.map(v => v === undefined ? '' : v);
        })
      };
    },
    getLastRow: () => data.length,
    getLastColumn: () => Math.max(...data.map(r => (r || []).length), 0),
    setColumnWidth() {}, setFrozenRows() {}, autoResizeColumns() {},
  };
  return sheet;
}
const ss = {
  getSheetByName: (n) => sheets[n] || null,
  insertSheet: (n) => (sheets[n] = fakeSheet(n)),
  setActiveSheet: () => {}, toast: () => {},
};

let alerts = [];
let answerYes = true;
globalThis.SpreadsheetApp = {
  getActiveSpreadsheet: () => ss,
  getUi: () => ({
    alert: (title, body) => { alerts.push({ title, body }); return answerYes ? 'YES' : 'NO'; },
    ButtonSet: { OK: 'OK', YES_NO: 'YES_NO' },
    Button: { YES: 'YES', NO: 'NO' },
  }),
};
globalThis.PropertiesService = {
  getUserProperties: () => ({ getProperty: () => null, setProperty: () => {} }),
  getScriptProperties: () => ({ getProperty: () => null }),
};
globalThis.Session = { getScriptTimeZone: () => 'Asia/Tokyo' };
globalThis.Utilities = {
  formatDate: (d, tz, fmt) => {
    const p = (n) => ('0' + n).slice(-2);
    return fmt === 'yyyy-MM-dd'
      ? `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`
      : `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
  },
};
globalThis.HtmlService = { createHtmlOutput: () => ({ setWidth() { return this; }, setHeight() { return this; } }) };
globalThis.Logger = { log: () => {} };

// --- 偽の Salesforce ------------------------------------------------------
const DESCRIBE = {
  name: 'Ringi__c', label: '稟議(テスト)', createable: true,
  fields: [
    { name: 'Id', createable: false, type: 'id', label: 'ID', nillable: false, defaultedOnCreate: true },
    { name: 'Name', createable: true, type: 'string', label: '件名', nillable: true, defaultedOnCreate: false },
    { name: 'OwnerId', createable: true, type: 'reference', label: '所有者 ID', nillable: false, defaultedOnCreate: true },
    { name: 'Status__c', createable: true, type: 'picklist', label: 'ステータス', nillable: true, defaultedOnCreate: false,
      picklistValues: [{ value: 'Submitted', active: true }, { value: 'Agree', active: true }, { value: 'Done', active: true }] },
    { name: 'ApplicationDate__c', createable: true, type: 'date', label: '申請日', nillable: false, defaultedOnCreate: false },
    { name: 'Amount__c', createable: true, type: 'currency', label: '金額', nillable: false, defaultedOnCreate: false },
    { name: 'Applicant__c', createable: true, type: 'string', label: '申請者', nillable: false, defaultedOnCreate: false },
    { name: 'bunrui__c', createable: true, type: 'picklist', label: '分類', nillable: false, defaultedOnCreate: false,
      picklistValues: [{ value: 'honban', active: true }, { value: 'test', active: true }] },
    { name: 'CreatedDate', createable: false, type: 'datetime', label: '作成日', nillable: false, defaultedOnCreate: true },
  ],
};

let posted = [];                 // 送信されたリクエスト
let postResponder = null;        // 応答を差し替える
globalThis.UrlFetchApp = {
  fetch: (url, opts) => {
    if (opts && opts.method === 'post') {
      posted.push({ url, body: JSON.parse(opts.payload) });
      const body = postResponder
        ? postResponder(JSON.parse(opts.payload))
        : JSON.parse(opts.payload).records.map((_, i) => ({ id: 'a00NEW' + i, success: true, errors: [] }));
      return { getResponseCode: () => 200, getContentText: () => JSON.stringify(body) };
    }
    return { getResponseCode: () => 200, getContentText: () => JSON.stringify(DESCRIBE) };
  },
};
globalThis.OAuth2 = {
  createService: () => {
    const s = {
      setAuthorizationBaseUrl: () => s, setTokenUrl: () => s, setClientId: () => s,
      setClientSecret: () => s, setCallbackFunction: () => s, setPropertyStore: () => s,
      setScope: () => s, generateCodeVerifier: () => s,
      hasAccess: () => true, getAccessToken: () => 'T',
      getToken: () => ({ instance_url: 'https://example.my.salesforce.com' }),
      getRedirectUri: () => 'https://script.google.com/macros/d/ID/usercallback',
      reset() {}, handleCallback: () => true, getAuthorizationUrl: () => 'https://auth',
    };
    return s;
  },
};

for (const f of ['Config.gs', 'SalesforceService.gs', 'Query.gs', 'Import.gs', 'Code.gs']) {
  vm.runInThisContext(fs.readFileSync(path.join(GAS_DIR, f), 'utf8'), { filename: f });
}

// --- 検証 ------------------------------------------------------------------
let pass = 0, fail = 0;
function check(label, cond, extra) {
  if (cond) { console.log('  OK  ' + label); pass++; }
  else { console.log('  NG  ' + label + (extra !== undefined ? '  → ' + extra : '')); fail++; }
}

function resetSheets(rows) {
  for (const k of Object.keys(sheets)) delete sheets[k];
  const cfg = fakeSheet('設定');
  sheets['設定'] = cfg;
  getOrCreateConfigSheet();                       // 既定値を入れる
  cfg.getRange(3, 2).setValue('KEY');             // コンシューマー鍵
  cfg.getRange(8, 2).setValue('Ringi__c');        // 登録先オブジェクト
  cfg.getRange(9, 2).setValue('200');             // 上限
  if (rows) {
    const sh = fakeSheet('登録データ');
    sheets['登録データ'] = sh;
    sh.getRange(1, 1, rows.length, rows[0].length).setValues(rows);
  }
  alerts = []; posted = []; postResponder = null;
}

const HEAD = ['ApplicationDate__c', 'Amount__c', 'Applicant__c', 'bunrui__c', 'Name', 'Status__c'];
const GOOD = [HEAD,
  ['2026-04-01', 12000, '山田', 'honban', 'ノートPC購入', 'Submitted'],
  ['2026-04-02', 8000, '鈴木', 'test', 'モニタ購入', 'Submitted']];

console.log('【1】describe から取り込み用の定義を作る');
resetSheets();
const meta = describeObject('Ringi__c');
const req = meta.createable.filter(f => f.required).map(f => f.name).sort();
check('必須4項目を拾う',
  // 辞書順では Applicant__c < ApplicationDate__c（'n' < 't'）
  JSON.stringify(req) === JSON.stringify(['Amount__c', 'Applicant__c', 'ApplicationDate__c', 'bunrui__c']), req);
check('作成不可の項目を除外', !meta.createable.some(f => f.name === 'Id' || f.name === 'CreatedDate'));
check('既定値のある必須は必須にしない（OwnerId）',
  !meta.createable.find(f => f.name === 'OwnerId').required);
check('選択肢を拾う',
  JSON.stringify(meta.createable.find(f => f.name === 'bunrui__c').picklist) === '["honban","test"]');

console.log('\n【2】正常な行を送信できる形にする');
resetSheets(GOOD);
let prep = prepareImport_();
check('問題なし', prep.problems.length === 0, prep.problems);
check('2件', prep.records.length === 2);
check('type を付ける', prep.records[0].attributes.type === 'Ringi__c');
check('日付を整形', prep.records[0].ApplicationDate__c === '2026-04-01');
check('金額は数値', prep.records[0].Amount__c === 12000);

console.log('\n【3】ドライランでは送信しない');
resetSheets(GOOD);
dryRunImport();
check('POSTしていない', posted.length === 0);
check('件数を伝える', /2 件/.test(alerts[0].body), alerts[0].body.slice(0, 60));

console.log('\n【4】不正な行は送信前に止める');
const BAD = [HEAD,
  ['2026-04-01', 12000, '山田', 'honban', 'OK行', 'Submitted'],
  ['', 8000, '鈴木', 'test', '申請日なし', 'Submitted'],              // 必須が空
  ['2026-04-03', 'いくらか', '佐藤', 'honban', '金額が文字', ''],      // 数値でない
  ['2026-04-04', 100, '高橋', 'unknown', '分類が不正', ''],            // 選択肢外
  ['2026/4/5', 200, '伊藤', 'test', 'スラッシュ日付', '']];            // 日付表記ゆれ
resetSheets(BAD);
prep = prepareImport_();
check('問題を検出', prep.problems.length >= 3, prep.problems.length);
check('必須の空欄を指摘', prep.problems.some(p => /3行目.*申請日/.test(p)), prep.problems[0]);
check('数値でないことを指摘', prep.problems.some(p => /4行目.*数値/.test(p)));
check('選択肢外を指摘', prep.problems.some(p => /5行目.*選択肢/.test(p)));
check('スラッシュ日付は受け入れて整形', prep.records[4].ApplicationDate__c === '2026-04-05',
  prep.records[4].ApplicationDate__c);

resetSheets(BAD);
runImport();
check('問題があれば1件も送らない', posted.length === 0);
check('送信していないと明示', /送信していません/.test(alerts[0].title));

console.log('\n【5】書き込めない列があれば止める');
resetSheets([['ApplicationDate__c', 'Amount__c', 'Applicant__c', 'bunrui__c', 'CreatedDate'],
             ['2026-04-01', 1, '山田', 'honban', '2026-01-01']]);
prep = prepareImport_();
check('作成不可の列を指摘', prep.problems.some(p => /CreatedDate.*書き込めない/.test(p)), prep.problems);

console.log('\n【6】上限を超えたら止める');
const many = [HEAD];
for (let i = 0; i < 250; i++) many.push(['2026-04-01', 1, '山田', 'honban', '件名' + i, '']);
resetSheets(many);
try { prepareImport_(); check('上限で止める', false); }
catch (e) { check('上限で止める', /200 件まで/.test(e.message), e.message); }

console.log('\n【7】確認ダイアログで「いいえ」なら送らない');
resetSheets(GOOD);
answerYes = false;
runImport();
check('キャンセルで送信しない', posted.length === 0);
answerYes = true;

console.log('\n【8】送信内容と allOrNone');
resetSheets(GOOD);
runImport();
check('1回で送る', posted.length === 1, posted.length);
check('allOrNone が true', posted[0].body.allOrNone === true);
check('composite/sobjects へ', /\/composite\/sobjects$/.test(posted[0].url), posted[0].url);
check('2件入っている', posted[0].body.records.length === 2);

console.log('\n【9】成功したらIDを書き戻す');
const sh = sheets['登録データ'];
const head = sh.getDataRange().getValues()[0];
const idCol = head.indexOf('作成されたID');
check('ID列ができる', idCol !== -1, head);
check('1行目のID', sh.getDataRange().getValues()[1][idCol] === 'a00NEW0');
check('2行目のID', sh.getDataRange().getValues()[2][idCol] === 'a00NEW1');
check('成功と記録', /登録成功/.test(sh.getDataRange().getValues()[1][head.indexOf('登録結果')]));

console.log('\n【10】Salesforce側で失敗したとき');
resetSheets(GOOD);
postResponder = () => ([
  { success: false, errors: [{ statusCode: 'FIELD_CUSTOM_VALIDATION_EXCEPTION', message: '金額が上限を超えています' }] },
  { success: false, errors: [{ statusCode: 'ALL_OR_NONE_OPERATION_ROLLED_BACK', message: 'rolled back' }] },
]);
runImport();
const sh2 = sheets['登録データ'];
const head2 = sh2.getDataRange().getValues()[0];
const stCol = head2.indexOf('登録結果');
check('失敗理由を書き戻す', /金額が上限/.test(sh2.getDataRange().getValues()[1][stCol]),
  sh2.getDataRange().getValues()[1][stCol]);
check('巻き添えを区別', /他の行のエラー/.test(sh2.getDataRange().getValues()[2][stCol]),
  sh2.getDataRange().getValues()[2][stCol]);
check('全件取り消しを伝える', /取り消されました/.test(alerts[alerts.length - 1].body));

console.log('\n【11】作成不可のオブジェクトは断る');
const orig = DESCRIBE.createable;
DESCRIBE.createable = false;
resetSheets(GOOD);
try { describeObject('Ringi__c'); check('作成不可を検出', false); }
catch (e) { check('作成不可を検出', /作成できない設定/.test(e.message), e.message); }
DESCRIBE.createable = orig;

console.log('\n小計: ' + pass + ' 件成功, ' + fail + ' 件失敗');

console.log('\n【12】古い設定シートの移行（今回の不具合）');
// 旧バージョンで作られた6行の設定シートに、新しい設定項目が足されるか
(function () {
  for (const k of Object.keys(sheets)) delete sheets[k];
  const old = fakeSheet('設定');
  sheets['設定'] = old;
  old.getRange(1, 1, 7, 3).setValues([
    ['設定項目', '値', '説明'],
    ['ログインURL', 'https://login.salesforce.com', ''],
    ['コンシューマー鍵', 'OLDKEY', ''],
    ['APIバージョン', '60.0', ''],
    ['出力先シート名', '結果', ''],
    ['最大取得件数', '5000', ''],
    ['SOQL', 'SELECT Id FROM Ringi__c', ''],
  ]);

  const cfg = getConfig();            // 読む過程で移行が走る
  const keys = old.getDataRange().getValues().slice(1).map(r => r[0]);
  check('登録先オブジェクトの行が足される', keys.indexOf('登録先オブジェクト') !== -1, keys);
  check('上限件数の行が足される', keys.indexOf('一括登録の上限件数') !== -1);
  check('既存の値を壊さない', cfg.clientId === 'OLDKEY', cfg.clientId);
  check('既定値が入る', cfg.importObject === 'Ringi__c', cfg.importObject);

  getConfig();                        // 2回目で重複して足さないこと
  const keys2 = old.getDataRange().getValues().slice(1).map(r => r[0]);
  const dup = keys2.filter(k => k === '登録先オブジェクト').length;
  check('重複して足さない', dup === 1, dup);
})();

console.log('\n【13】項目名で読むので並び順に依存しない');
(function () {
  for (const k of Object.keys(sheets)) delete sheets[k];
  const sh = fakeSheet('設定');
  sheets['設定'] = sh;
  sh.getRange(1, 1, 4, 3).setValues([
    ['設定項目', '値', '説明'],
    ['登録先オブジェクト', 'Other__c', ''],
    ['コンシューマー鍵', 'K', ''],
    ['APIバージョン', '61.0', ''],
  ]);
  const cfg = getConfig();
  check('並び順が違っても正しく読む',
    cfg.importObject === 'Other__c' && cfg.apiVersion === '61.0',
    cfg.importObject + ' / ' + cfg.apiVersion);
})();

console.log('\n最終結果: ' + pass + ' 件成功, ' + fail + ' 件失敗');
process.exit(fail ? 1 : 0);
