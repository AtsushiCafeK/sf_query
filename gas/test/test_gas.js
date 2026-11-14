// GASの関数を偽の環境で動かして、ロジックを検証する。
//
//   node gas/test/test_gas.js
//
// Googleアカウントもスプレッドシートも不要。SpreadsheetApp / UrlFetchApp /
// OAuth2 を差し替えて、設定の読み取り・SOQL検証・平坦化・ページング・
// 打ち切り・エラー文言・書き出しを確認する。
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const GAS_DIR = path.join(__dirname, '..');

// --- 偽のGASサービス ---------------------------------------------------
const sheets = {};
function fakeSheet(name) {
  const data = [];
  return {
    name,
    data,
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
            for (let j = 0; j < nc; j++) {
              row.push((data[r - 1 + i] || [])[c - 1 + j] ?? '');
            }
            out.push(row);
          }
          return out;
        },
        setValue(v) { data[r - 1] = data[r - 1] || []; data[r - 1][c - 1] = v; return this; },
        setFontWeight() { return this; }, setBackground() { return this; },
        setFontColor() { return this; }, setWrap() { return this; },
      };
    },
    getLastRow: () => data.length,
    getLastColumn: () => Math.max(...data.map(r => (r || []).length), 0),
    setColumnWidth() {}, setFrozenRows() {}, autoResizeColumns() {},
  };
}
const ss = {
  getSheetByName: (n) => sheets[n] || null,
  insertSheet: (n) => (sheets[n] = fakeSheet(n)),
  setActiveSheet: () => {}, toast: () => {},
};
const calls = [];
globalThis.SpreadsheetApp = { getActiveSpreadsheet: () => ss, getUi: () => ({ alert: () => {}, ButtonSet: {} }) };
globalThis.PropertiesService = {
  getUserProperties: () => ({ getProperty: () => null, setProperty: () => {} }),
  getScriptProperties: () => ({ getProperty: () => null }),
};
globalThis.UrlFetchApp = {
  fetch: (url) => { calls.push(url); return fakeResponse(); },
};
globalThis.HtmlService = { createHtmlOutput: (h) => ({ html: h, setWidth() { return this; }, setHeight() { return this; } }) };
globalThis.Logger = { log: () => {} };

let fakeResponse = () => ({ getResponseCode: () => 200, getContentText: () => '{}' });

// OAuth2 ライブラリの偽物（hasAccess は常に true）
let tokenInstanceUrl = 'https://example.my.salesforce.com';
globalThis.OAuth2 = {
  createService: () => {
    const s = {
      setAuthorizationBaseUrl() { return s; }, setTokenUrl() { return s; },
      setClientId() { return s; }, setClientSecret() { return s; },
      setCallbackFunction() { return s; }, setPropertyStore() { return s; },
      setScope() { return s; }, generateCodeVerifier() { return s; },
      hasAccess: () => true, getAccessToken: () => 'TOKEN',
      getToken: () => ({ instance_url: tokenInstanceUrl }),
      getRedirectUri: () => 'https://script.google.com/macros/d/ID/usercallback',
      reset() {}, handleCallback: () => true, getAuthorizationUrl: () => 'https://auth',
    };
    return s;
  },
};

// --- GASファイルを読み込む ---------------------------------------------
for (const f of ['Config.gs', 'SalesforceService.gs', 'Query.gs', 'Code.gs']) {
  vm.runInThisContext(fs.readFileSync(path.join(GAS_DIR, f), 'utf8'), { filename: f });
}

// --- 検証 ---------------------------------------------------------------
let pass = 0, fail = 0;
function check(label, cond, extra) {
  if (cond) { console.log('  OK  ' + label); pass++; }
  else { console.log('  NG  ' + label + (extra ? '  → ' + extra : '')); fail++; }
}

console.log('【1】設定シートの自動作成と読み取り');
const cfgSheet = getOrCreateConfigSheet();
check('設定シートが作られる', !!sheets['設定']);
cfgSheet.getRange(3, 2).setValue('3MVG9_TEST_KEY');          // コンシューマー鍵
cfgSheet.getRange(7, 2).setValue('SELECT Id, Name FROM Ringi__c');
const cfg = getConfig();
check('鍵を読める', cfg.clientId === '3MVG9_TEST_KEY', cfg.clientId);
check('ログインURLの既定', cfg.loginUrl === 'https://login.salesforce.com', cfg.loginUrl);
check('出力先シート名の既定', cfg.outputSheet === '結果', cfg.outputSheet);
check('最大件数が数値', cfg.maxRecords === 5000, cfg.maxRecords);

console.log('\n【2】SOQLの検証');
check('正しいSOQLは通る', getValidatedSoql() === 'SELECT Id, Name FROM Ringi__c');
cfgSheet.getRange(7, 2).setValue('DELETE FROM Ringi__c');
try { getValidatedSoql(); check('SELECT以外を拒否', false); }
catch (e) { check('SELECT以外を拒否', /SELECT で始まる/.test(e.message)); }
cfgSheet.getRange(7, 2).setValue('SELECT Id');
try { getValidatedSoql(); check('FROM無しを拒否', false); }
catch (e) { check('FROM無しを拒否', /FROM 句がありません/.test(e.message)); }
cfgSheet.getRange(7, 2).setValue('   SELECT Id, Name FROM Ringi__c;  ');
check('前後の空白と末尾;を除去', getValidatedSoql() === 'SELECT Id, Name FROM Ringi__c');
cfgSheet.getRange(3, 2).setValue('');
try { getConfig(); check('鍵が空ならエラー', false); }
catch (e) { check('鍵が空ならエラー', /コンシューマー鍵/.test(e.message)); }
cfgSheet.getRange(3, 2).setValue('3MVG9_TEST_KEY');

console.log('\n【3】入れ子項目の平坦化');
const flat = flattenRecord({
  attributes: { type: 'Ringi__c', url: '/x' },
  Id: 'a001', Name: 'SSD購入',
  RecordType: { attributes: {}, DeveloperName: 'Purchase' },
  LastModifiedBy: { Name: 'Shindo' },
  Nulls: null,
});
check('attributes を除外', !('attributes' in flat));
check('リレーションをドット表記に', flat['RecordType.DeveloperName'] === 'Purchase');
check('2階層目も平坦化', flat['LastModifiedBy.Name'] === 'Shindo');
check('null を保持', flat['Nulls'] === null);

console.log('\n【4】表の組み立て（列が可変でも揃う）');
const table = buildTable([
  { Id: 'a1', Name: 'A', Status__c: 'Done' },
  { Id: 'a2', Name: 'B' },                      // Status__c が欠ける
  { Id: 'a3', Amount__c: 100 },                 // 新しい列が出てくる
]);
check('列を集約', JSON.stringify(table.columns) === JSON.stringify(['Id', 'Name', 'Status__c', 'Amount__c']), table.columns);
check('欠けた項目は空文字', table.rows[1][2] === '');
check('行数が一致', table.rows.length === 3);
const sub = buildTable([{ Id: 'a1', Children: [{}, {}] }]);
check('子リレーションは件数表示', sub.rows[0][1] === '[2件]', sub.rows[0][1]);

console.log('\n【5】ページング（nextRecordsUrl を辿る）');
let page = 0;
fakeResponse = () => {
  page++;
  const body = page === 1
    ? { records: [{ Id: '1' }, { Id: '2' }], nextRecordsUrl: '/services/data/v60.0/query/01g-2000' }
    : { records: [{ Id: '3' }] };
  return { getResponseCode: () => 200, getContentText: () => JSON.stringify(body) };
};
calls.length = 0;
const res = runSoql('SELECT Id FROM Ringi__c');
check('2ページ分を取得', res.records.length === 3, res.records.length);
check('2回問い合わせた', calls.length === 2, calls.length);
check('2回目は nextRecordsUrl', calls[1].indexOf('01g-2000') !== -1, calls[1]);
check('打ち切りフラグなし', res.truncated === false);

console.log('\n【6】最大件数での打ち切り');
cfgSheet.getRange(6, 2).setValue('2');      // 最大取得件数=2
page = 0;
const res2 = runSoql('SELECT Id FROM Ringi__c');
check('上限で切る', res2.records.length === 2, res2.records.length);
check('打ち切りを知らせる', res2.truncated === true && /最大取得件数/.test(res2.reason));
cfgSheet.getRange(6, 2).setValue('5000');

console.log('\n【7】エラーの日本語化');
fakeResponse = () => ({
  getResponseCode: () => 400,
  getContentText: () => JSON.stringify([{ errorCode: 'INVALID_FIELD', message: "No such column 'Foo__c'" }]),
});
try { salesforceGet('/x'); check('INVALID_FIELD を説明', false); }
catch (e) { check('INVALID_FIELD を説明', /存在しない項目/.test(e.message) && /Foo__c/.test(e.message), e.message); }
fakeResponse = () => ({ getResponseCode: () => 401, getContentText: () => '[]' });
try { salesforceGet('/x'); check('401 を説明', false); }
catch (e) { check('401 を説明', /有効期限/.test(e.message), e.message); }

console.log('\n【8】結果の書き出し');
fakeResponse = () => ({ getResponseCode: () => 200, getContentText: () => '{}' });
writeResults(buildTable([{ Id: 'a1', Name: 'テスト' }]), '結果');
const out = sheets['結果'].data;
check('1行目がヘッダー', JSON.stringify(out[0]) === JSON.stringify(['Id', 'Name']), out[0]);
check('2行目がデータ', JSON.stringify(out[1]) === JSON.stringify(['a1', 'テスト']), out[1]);
writeResults(buildTable([]), '結果');
check('0件でも落ちない', sheets['結果'].data[0][0] === '該当するレコードがありません。');

console.log('\n【9】リクエストURLの組み立て');
calls.length = 0;
fakeResponse = () => ({ getResponseCode: () => 200, getContentText: () => JSON.stringify({ records: [] }) });
runSoql("SELECT Id FROM Ringi__c WHERE Name = 'a b'");
check('instance_url が前に付く', calls[0].indexOf(tokenInstanceUrl) === 0, calls[0]);
// encodeURIComponent は ' を変換しないが、URLとして正当でSalesforceも受け付ける。
// 問題になるのは空白・& + # のほうなので、そちらが変換されることを確かめる。
calls.length = 0;
runSoql("SELECT Id FROM Ringi__c WHERE Name = 'a&b+c#d e'");
check('空白をエンコード', calls[0].indexOf('%20') !== -1, calls[0]);
check('& をエンコード', calls[0].indexOf('%26') !== -1 && calls[0].split('?q=')[1].indexOf('&') === -1);
check('+ をエンコード', calls[0].indexOf('%2B') !== -1);
check('# をエンコード', calls[0].indexOf('%23') !== -1 && calls[0].indexOf('#') === -1);
check('APIバージョンを使う', calls[0].indexOf('/v60.0/query/') !== -1);

console.log('\n結果: ' + pass + ' 件成功, ' + fail + ' 件失敗');

// --- 10. 同梱した OAuth2.gs が「貼り付けるだけ」で使えるか ----------------
// ライブラリとして追加する代わりにファイルを貼る手順にしたため、
// 実物を読み込んでグローバル公開とPKCEの有無を確かめる。
console.log('\n【10】同梱 OAuth2.gs（貼り付け方式）');
(function () {
  const sandbox = { console };
  sandbox.globalThis = sandbox;
  vm.createContext(sandbox);
  vm.runInContext(fs.readFileSync(path.join(GAS_DIR, 'OAuth2.gs'), 'utf8'),
                  sandbox, { filename: 'OAuth2.gs' });

  check('グローバルに OAuth2 が定義される', typeof sandbox.OAuth2 === 'object');
  check('createService がある', typeof sandbox.OAuth2.createService === 'function');

  const svc = sandbox.OAuth2.createService('t');
  check('generateCodeVerifier がある（PKCE）', typeof svc.generateCodeVerifier === 'function');
  check('setCodeChallengeMethod がある', typeof svc.setCodeChallengeMethod === 'function');
  check('本体が使うメソッドが揃っている',
    ['setAuthorizationBaseUrl', 'setTokenUrl', 'setClientId', 'setClientSecret',
     'setCallbackFunction', 'setPropertyStore', 'setScope', 'hasAccess',
     'getAccessToken', 'getToken', 'getRedirectUri', 'reset',
     'handleCallback', 'getAuthorizationUrl']
      .every(function (m) { return typeof svc[m] === 'function'; }));
})();

console.log('\n最終結果: ' + pass + ' 件成功, ' + fail + ' 件失敗');
process.exit(fail ? 1 : 0);
