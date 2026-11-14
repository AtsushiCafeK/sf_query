/**
 * Salesforce への OAuth 2.0 認証（認可コードフロー + PKCE）。
 *
 * 利用者ごとに自分のアカウントでログインし、トークンは
 * PropertiesService.getUserProperties() に各自の領域で保存される。
 * スプレッドシートを共有しても、他人のトークンは見えない。
 *
 * クライアントシークレット:
 *   外部クライアントアプリが「Webサーバーフローで秘密を要求」の場合は、
 *   スクリプトプロパティ SALESFORCE_CLIENT_SECRET に設定する。
 *   未設定のときは client_secret を送らない（秘密を要求しない設定向け）。
 *
 * ※ apps-script-oauth2 は generateCodeVerifier() で PKCE(S256) に対応している。
 */

/** OAuth サービス名。UserProperties のキーの一部になる。 */
var SERVICE_NAME = 'salesforce';

/**
 * 認証サービスを組み立てる。
 * @return {Object} apps-script-oauth2 のサービス
 */
function getSalesforceService() {
  var cfg = getConfig();
  var secret = PropertiesService.getScriptProperties()
    .getProperty('SALESFORCE_CLIENT_SECRET') || '';

  var service = OAuth2.createService(SERVICE_NAME)
    .setAuthorizationBaseUrl(cfg.loginUrl + '/services/oauth2/authorize')
    .setTokenUrl(cfg.loginUrl + '/services/oauth2/token')
    .setClientId(cfg.clientId)
    .setClientSecret(secret)
    .setCallbackFunction('authCallback')
    .setPropertyStore(PropertiesService.getUserProperties())
    .setScope('api refresh_token offline_access');

  // 秘密が無いときは、空の client_secret を送らない
  // （空文字を送ると Salesforce が invalid_client を返す）
  if (!secret) {
    service.setTokenPayloadHandler(function (payload) {
      delete payload.client_secret;
      return payload;
    });
  }

  // PKCE を有効にする
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

/** 認証を解除する（別のアカウントで入り直すときなど）。 */
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
