# Salesforce SOQL → スプレッドシート（Google Apps Script 版）

スプレッドシートのメニューから、Salesforce へ SOQL を投げて結果を表に出力する。
各利用者が自分の Salesforce アカウントでログインし、**その人の権限で見えるデータだけ**を取得する
（OAuth 2.0 認可コードフロー + PKCE）。

## ファイル

| ファイル | 役割 |
|---|---|
| `Code.gs` | メニュー（`onOpen`）と各メニューの処理 |
| `SalesforceService.gs` | OAuth 認証、REST API の GET、エラー文言 |
| `config.gs` | 「設定」シートの作成・読み取り・SOQL の簡易検証 |
| `Query.gs` | クエリ実行（ページング）、入れ子の平坦化、シート出力 |
| `OAuth2.gs`（**リポジトリには含めない**） | [apps-script-oauth2](https://github.com/googleworkspace/apps-script-oauth2) の `dist/OAuth2.gs` を無改変で貼り付ける（Apache-2.0） |

## セットアップ

1. スプレッドシートの「拡張機能 → Apps Script」に、上記ファイルを同名で作成して貼り付ける。
   `OAuth2.gs` は上記リポジトリの `dist/OAuth2.gs` をそのまま貼り付ける
   （ライブラリ追加でも可。スクリプトID: `1B7FSrk5Zi6L1rSxxTDgDEUsPzlukDsi4KGuTMorsTQHhGBzBkMun4iDF`、識別子は `OAuth2`）。
2. Salesforce に **外部クライアントアプリ**（または接続アプリ）を作成する。
   - OAuth を有効化。スコープ: `api` / `refresh_token` / `offline_access`
   - コールバックURL: メニュー「Salesforce → コールバックURLを表示」で出る値
     （`https://script.google.com/macros/d/<スクリプトID>/usercallback`）
   - 認可コードフロー + **PKCE を要求**
3. スプレッドシートを開き直すと「Salesforce」メニューが出る。初回実行で「設定」シートが作られる。
4. 「設定」シートに入力する。
   - `ログインURL`: 本番 `https://login.salesforce.com` / Sandbox `https://test.salesforce.com` /
     My Domain の組織は `https://<ドメイン>.my.salesforce.com`
   - `コンシューマー鍵`
   - `SOQL`
5. 外部クライアントアプリが「Webサーバーフローで秘密を要求」の場合は、
   スクリプトプロパティ `SALESFORCE_CLIENT_SECRET` にコンシューマーの秘密を登録する
   （プロジェクトの設定 → スクリプト プロパティ）。
6. メニュー「Salesforceに接続」→ ログイン → 「SOQLを実行」。

## 運用上の注意

- **秘密情報**（コンシューマーの秘密）はコードやシートに書かず、スクリプトプロパティに置く。
  スクリプトの編集権限を持つ人は値を見られるため、編集権限は管理者に限定する。
- トークンは利用者ごとの UserProperties に保存される。共有しても他人のトークンは見えない。
- 出力は「最大取得件数」と GAS の実行時間（6分）で打ち切られる。大量件数は WHERE で絞る。
- 入れ子クエリ（親→子）で孫まで取るには API バージョン 58.0 以上が必要。
  ただし表への出力時、子リレーションは `[N件]` と件数のみ表示される。
  子を行として出したい場合は、子側を起点（FROM）にして親を `.` でたどる。

## よくあるエラー

| エラー | 主な原因 |
|---|---|
| `invalid_client_id` | コンシューマー鍵の誤り、接続先の組織違い、作成直後で未反映 |
| `redirect_uri_mismatch` | コールバックURLが登録値と不一致（`getRedirectUri()` の値をそのまま登録） |
| `invalid_client` / `invalid client credentials` | 秘密の要求と送信が不一致。`SALESFORCE_CLIENT_SECRET` を設定する／ログインURLを組織のドメインに合わせる |
| HTTP 404 `NOT_FOUND` | APIバージョンのURLが不正（`60.0` が `60` になる等）。`config.gs` で正規化済み |
| `[object Object]`（開発者コンソール） | サブクエリ列の表示制限。データは取得できている |
