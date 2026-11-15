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
    // 既定は PKCE の公開クライアント構成（シークレットなし）。
    // 接続アプリ側で秘密が必須の設定になっている場合に限り、スクリプトプロパティ
    // SALESFORCE_CLIENT_SECRET に入れると送信する。シートには置かない。
    .setClientSecret(
      PropertiesService.getScriptProperties().getProperty('SALESFORCE_CLIENT_SECRET') || '')
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
  var style = 'font-family:sans-serif; font-size:15px; line-height:1.7; padding:8px;';
  try {
    var authorized = getSalesforceService().handleCallback(request);
    var message = authorized
      ? '接続しました。このタブを閉じて、スプレッドシートに戻ってください。'
      : '接続をキャンセルしました。このタブを閉じてください。';
    return HtmlService.createHtmlOutput('<p style="' + style + '">' + message + '</p>');
  } catch (e) {
    // 生のエラー画面では原因が分からないため、確認項目を添えて返す
    return HtmlService.createHtmlOutput(
      '<div style="' + style + '">' +
      '<p><strong>接続に失敗しました。</strong></p>' +
      '<p style="color:#b00020;">' + escapeHtml_(e.message) + '</p>' +
      authFailureHints_(e.message) +
      '</div>');
  }
}

/**
 * 認証エラーの内容に応じて、確認すべき項目を返す（HTML）。
 * \nparam {string} message エラーメッセージ
 * \nreturn {string}
 * \nprivate
 */
function authFailureHints_(message) {
  var items = [];
  if (/invalid_client/.test(message)) {
    items.push('設定シートの「コンシューマー鍵」が、接続アプリのものと一致しているか（前後の空白・貼り間違い）');
    items.push('接続アプリ側の「Webサーバーフローの秘密が必要」「更新トークンフローの秘密が必要」の設定。' +
               '秘密を必要とする設定になっている場合は、プロジェクトの設定 → スクリプト プロパティに ' +
               '<code>SALESFORCE_CLIENT_SECRET</code> を登録する');
    items.push('接続アプリを編集した直後は、反映まで最大10分かかる');
    items.push('ログインURL（本番・開発組織 / Sandbox）が接続先と合っているか');
  } else if (/redirect_uri/.test(message)) {
    items.push('接続アプリのコールバックURLに、メニュー「コールバックURLを表示」のURLが登録されているか');
  } else if (/invalid_grant/.test(message)) {
    items.push('「認証を解除」してから、もう一度「Salesforceに接続」をやり直す');
  }
  if (!items.length) { return ''; }
  return '<p>確認してください:</p><ul>' + items.map(function (t) {
    return '<li>' + t + '</li>';
  }).join('') + '</ul>';
}

/**
 * HTML に埋め込む文字列をエスケープする。
 * エラー本文には Salesforce の応答が入るため、そのまま埋め込まない。
 * \nparam {string} text
 * \nreturn {string}
 * \nprivate
 */
function escapeHtml_(text) {
  return String(text == null ? '' : text)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

/**
 * 直前の認証（トークン更新）が失敗していれば、その理由を返す。
 *
 * ライブラリの hasAccess() は更新に失敗しても例外を投げず false を返すだけなので、
 * 利用者には「未接続」としか見えない。理由を取り出して伝える。
 *
 * \nparam {Object} service 認証サービス
 * \nreturn {string} 理由。無ければ空文字
 */
function authFailureReason_(service) {
  var e = service.getLastError();
  return e ? String(e.message || e) : '';
}

/**
 * 「接続されていません」のメッセージ。更新に失敗していれば理由を添える。
 * \nparam {Object} service 認証サービス
 * \nreturn {string}
 * \nprivate
 */
function notConnectedMessage_(service) {
  var base = 'Salesforce に接続されていません。メニューの「Salesforce」→「Salesforceに接続」から認証してください。';
  var reason = authFailureReason_(service);
  return reason
    ? base + '\n\n' + '直前の認証の更新に失敗しています:\n' + reason
    : base;
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
    throw new Error(notConnectedMessage_(service));
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

  // 404 は「パスが違う」ことが多い。原因を追えるよう実際のURLを添える
  throw new Error(describeSalesforceError_(code, body) +
                  (code === 404 ? '\n\n実際に要求したURL:\n' + url : ''));
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
  if (code === 404) {
    return 'Salesforce に該当するリソースがありません (HTTP 404)。\n' +
           'オブジェクトのAPI名が間違っているか、APIバージョンがこの組織で' +
           '使えない可能性があります。設定シートの「登録先オブジェクト」と' +
           '「APIバージョン」を確認してください。\n' + detail;
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
    throw new Error(notConnectedMessage_(service));
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
