/**
 * Salesforce への OAuth 2.0 認証（認可コードフロー + PKCE）。
 *
 * 利用者ごとに自分のアカウントでログインし、トークンは
 * PropertiesService.getUserProperties() に各自の領域で保存される。
 * スプレッドシートを共有しても、他人のトークンは見えない。
 *
 * ※ apps-script-oauth2 は PKCE に対応しているが README に記載がない。
 *   generateCodeVerifier() を呼ぶと code_challenge(S256) が自動で付く。
 *   （ライブラリには setCodeVerififer という綴り違いの関数があるため使わない）
 */

/** OAuth サービス名。UserProperties のキーの一部になる。 */
var SERVICE_NAME = 'salesforce';

/**
 * 認証サービスを組み立てる。
 * @return {Object} apps-script-oauth2 のサービス
 */
function getSalesforceService() {
  var cfg = getConfig();

  var service = OAuth2.createService(SERVICE_NAME)
    .setAuthorizationBaseUrl(cfg.loginUrl + '/services/oauth2/authorize')
    .setTokenUrl(cfg.loginUrl + '/services/oauth2/token')
    .setClientId(cfg.clientId)
    // PKCE の公開クライアント構成のため、シークレットは持たない
    .setClientSecret('')
    .setCallbackFunction('authCallback')
    .setPropertyStore(PropertiesService.getUserProperties())
    .setScope('api refresh_token offline_access');

  // PKCE を有効にする（接続アプリ側で「PKCE拡張を要求」が有効なため必須）
  service.generateCodeVerifier();

  return service;
}

/**
 * 認可画面から戻ってきたときに呼ばれる。
 * @param {Object} request コールバックのリクエスト
 * @return {HtmlOutput} 利用者に見せる結果画面
 */
function authCallback(request) {
  var authorized = getSalesforceService().handleCallback(request);
  var message = authorized
    ? '接続しました。このタブを閉じて、スプレッドシートに戻ってください。'
    : '接続をキャンセルしました。このタブを閉じてください。';
  return HtmlService.createHtmlOutput(
    '<p style="font-family:sans-serif; font-size:15px;">' + message + '</p>'
  );
}

/**
 * 認証を解除する（別のアカウントで入り直すときなど）。
 */
function logout() {
  getSalesforceService().reset();
}

/**
 * コールバックURLを返す。接続アプリに登録する値。
 * @return {string}
 */
function getRedirectUri() {
  return getSalesforceService().getRedirectUri();
}

/**
 * Salesforce の REST API を GET で叩く。
 *
 * @param {string} path  "/services/data/v60.0/query/?q=..." のような相対パス
 * @return {Object} 応答のJSON
 */
function salesforceGet(path) {
  var service = getSalesforceService();
  if (!service.hasAccess()) {
    throw new Error(
      'Salesforce に接続されていません。メニューの「Salesforce」→「Salesforceに接続」から認証してください。'
    );
  }

  var url = service.getToken().instance_url + path;
  var response = UrlFetchApp.fetch(url, {
    headers: { Authorization: 'Bearer ' + service.getAccessToken() },
    muteHttpExceptions: true  // エラー本文を読んで、原因を利用者に見せるため
  });

  var code = response.getResponseCode();
  var body = response.getContentText();

  if (code === 200) {
    return JSON.parse(body);
  }

  throw new Error(describeSalesforceError_(code, body));
}

/**
 * Salesforce のエラー応答を、原因が分かる日本語にする。
 * @param {number} code HTTPステータス
 * @param {string} body 応答本文
 * @return {string}
 * @private
 */
function describeSalesforceError_(code, body) {
  var detail = body;
  try {
    var parsed = JSON.parse(body);
    if (Array.isArray(parsed) && parsed.length) {
      detail = parsed.map(function (e) {
        return (e.errorCode ? '[' + e.errorCode + '] ' : '') + (e.message || '');
      }).join(' / ');
    }
  } catch (e) {
    // JSON でなければ本文をそのまま使う
  }

  if (code === 401) {
    return '認証の有効期限が切れています。メニューから「認証を解除」して、接続し直してください。\n' + detail;
  }
  if (detail.indexOf('INVALID_FIELD') !== -1) {
    return 'SOQL に存在しない項目またはオブジェクトが含まれています。綴りを確認してください。\n' + detail;
  }
  if (detail.indexOf('MALFORMED_QUERY') !== -1) {
    return 'SOQL の書き方に誤りがあります。\n' + detail;
  }
  return 'Salesforce でエラーが発生しました (HTTP ' + code + ')\n' + detail;
}

/**
 * Salesforce の REST API に POST する（レコード作成などの書き込み）。
 *
 * 読み取りと違い、やり直しが効かない操作なので呼び出し側で
 * 事前検証とドライランを用意すること。
 *
 * @param {string} path  相対パス
 * @param {Object} body  送信するJSON
 * @return {Object|Array} 応答のJSON
 */
function salesforcePost(path, body) {
  var service = getSalesforceService();
  if (!service.hasAccess()) {
    throw new Error(
      'Salesforce に接続されていません。メニューの「Salesforce」→「Salesforceに接続」から認証してください。'
    );
  }

  var response = UrlFetchApp.fetch(service.getToken().instance_url + path, {
    method: 'post',
    contentType: 'application/json',
    headers: { Authorization: 'Bearer ' + service.getAccessToken() },
    payload: JSON.stringify(body),
    muteHttpExceptions: true
  });

  var code = response.getResponseCode();
  var text = response.getContentText();

  // composite/sobjects は各行の成否を 200 で返すため、本文は呼び出し側で見る
  if (code === 200 || code === 201) {
    return JSON.parse(text);
  }
  throw new Error(describeSalesforceError_(code, text));
}

/**
 * オブジェクトの定義を、取り込みに必要な形に整えて返す。
 *
 * @param {string} objectName API名
 * @return {{name: string, label: string, createable: Array<Object>}}
 */
function describeObject(objectName) {
  var cfg = getConfig();
  var described = salesforceGet(
    '/services/data/v' + cfg.apiVersion + '/sobjects/' + objectName + '/describe/');

  if (!described.createable) {
    throw new Error(
      objectName + ' はAPIからレコードを作成できない設定です。' +
      '管理パッケージのオブジェクトではよくあるため、別の方法を検討してください。'
    );
  }

  var createable = (described.fields || [])
    .filter(function (f) { return f.createable; })
    .map(function (f) {
      var picklist = (f.picklistValues || [])
        .filter(function (p) { return p.active; })
        .map(function (p) { return p.value; });
      return {
        name: f.name,
        label: f.label || f.name,
        type: f.type,
        // 既定値が入らず、空も許さない項目＝入力必須
        required: (!f.nillable && !f.defaultedOnCreate),
        picklist: picklist,
        // 参照型やシステム項目は、ふつうシートに並べない
        recommended: (f.type !== 'reference')
      };
    });

  return { name: described.name, label: described.label, createable: createable };
}
