# Salesforce SOQL → スプレッドシート（Google Apps Script）

スプレッドシートのセルに SOQL を書いて実行し、**結果の一覧をシートに書き出す**だけの軽量版。
添付ファイルのダウンロードは行わない（それは [Pythonツール](../README.md) 側の役目）。

認証は本体と同じ **OAuth 2.0 認可コードフロー + PKCE**。
利用者それぞれが自分のSalesforceアカウントでログインし、**その人が見られるデータだけ**取得できる。

## ファイル構成

| ファイル | 役割 |
|---|---|
| `Code.gs` | メニューと実行の入口 |
| `Config.gs` | 設定シートの読み書き（初回に自動作成） |
| `SalesforceService.gs` | OAuth認証（PKCE）とREST呼び出し |
| `Query.gs` | SOQL実行・入れ子項目の平坦化・シート書き出し |
| `appsscript.json` | マニフェスト（ライブラリ・権限） |

---

## セットアップ

### 1. スプレッドシートとスクリプトを用意

1. 新しいスプレッドシートを作る（空でよい）
2. **拡張機能 → Apps Script** を開く
3. 既定の `コード.gs` を消し、このフォルダの `.gs` を4つとも貼り付ける
   （ファイル名は任意だが、`Code` / `Config` / `SalesforceService` / `Query` と分けると分かりやすい）

### 2. OAuth2 ライブラリを追加

Apps Script の左メニュー **ライブラリ ＋** で、次のIDを追加する。

```
1B7FSrk5Zi6L1rSxxTDgDEUsPzlukDsi4KGuTMorsTQHhGBzBkMun4iDF
```

ID（参照名）は **`OAuth2`** のままにする。バージョンは最新を選ぶ。

> `appsscript.json` を直接編集してもよい（マニフェストを表示する設定が必要）。

### 3. コールバックURLを接続アプリに登録

1. スプレッドシートを開き直すと **「Salesforce」メニュー** が出る
2. **Salesforce → コールバックURLを表示** を選び、表示されたURLをコピー

   形式: `https://script.google.com/macros/d/{スクリプトID}/usercallback`

3. Salesforce の接続アプリ（外部クライアントアプリケーション）の
   **コールバックURL** に、既存のものと**改行で並べて**追加する

   ```
   http://localhost:8080/oauth/callback
   https://script.google.com/macros/d/XXXXXXXX/usercallback
   ```

   > 既存の接続アプリをそのまま使える。新規作成は不要。
   > 反映に最大10分かかる。

### 4. 設定シートを埋める

**Salesforce → 設定シートを作成** を選ぶと「設定」シートができる。

| 設定項目 | 内容 |
|---|---|
| ログインURL | 本番・開発組織はそのまま。Sandbox は `https://test.salesforce.com` |
| **コンシューマー鍵** | 接続アプリのコンシューマー鍵を貼り付ける |
| APIバージョン | 通常は変更不要（`60.0`） |
| 出力先シート名 | 結果を書き出すシート名。無ければ作られる |
| 最大取得件数 | 安全のための上限（既定 5000） |
| **SOQL** | 実行したいクエリ |

### 5. 接続して実行

1. **Salesforce → Salesforceに接続** → リンクを開いてログイン・許可
2. **Salesforce → SOQLを実行**

結果が出力先シートに並ぶ。1行目が項目名。

---

## 使い方

設定シートの「SOQL」を書き換えて **SOQLを実行** するだけ。

```sql
SELECT Id, Name, Status__c, ApplicationDate__c, RecordType.DeveloperName
FROM Ringi__c
WHERE Status__c = 'Done'
ORDER BY ApplicationDate__c DESC
```

- `RecordType.DeveloperName` のような**リレーション項目**は、その名前のまま列になる
- 列は SOQL が返した項目に**自動で追随**する（固定ではない）
- 実行のたびに出力先シートは**上書き**される

SOQL の書き方は [SOQLチートシート](../docs/03_soql_cheatsheet.md) を参照。

---

## 安全性について

- **SOQL は読み取り専用**。`UPDATE` や `DELETE` という構文が言語自体に存在しないため、
  セルに自由にクエリを書いてもデータを壊せない
- **各自のOAuthトークンで動く**。その人がSalesforce上で参照できるデータしか取得できない
- トークンは `PropertiesService.getUserProperties()` に保存され、
  **スプレッドシートを共有しても他人のトークンは見えない**
- コンシューマー鍵（client_id）は、PKCEの公開クライアント構成では秘密情報ではない。
  これだけでは誰もログインできない（各自のSalesforceログインが必要）
  - それでもシートに置きたくない場合は、**プロジェクトの設定 → スクリプト プロパティ** に
    `SALESFORCE_CLIENT_ID` を登録する。そちらが優先される

---

## 制約

| 制約 | 内容 |
|---|---|
| 実行時間 | GASは1回 **6分**まで。余裕を見て約4分30秒で打ち切り、取得済み分を書き出して知らせる |
| 件数 | 「最大取得件数」で上限を設ける（既定5000）。超えると打ち切って知らせる |
| 子リレーション | サブクエリの結果は表に展開できないため `[n件]` と表示する |
| 添付ファイル | 対象外。ダウンロードが必要な場合は [Pythonツール](../README.md) を使う |

大量件数を取りたい場合は、SOQL に `WHERE` 条件を足して期間などで分割する。

---

## つまずいたときは

| 症状 | 原因と対処 |
|---|---|
| `redirect_uri_mismatch` | 接続アプリのコールバックURLに、GASのURLが登録されていない。手順3を確認（反映に最大10分） |
| `invalid_client_id` | コンシューマー鍵の貼り間違い |
| `OAuth2 is not defined` | ライブラリが追加されていない。手順2を確認（参照名は `OAuth2`） |
| 認証の有効期限が切れています | 「認証を解除」してから接続し直す |
| SOQL に存在しない項目… | 項目名の綴り違い。`sf sobject describe` か、本体ツールの設定画面で確認できる |
| メニューが出ない | スプレッドシートを開き直す（`onOpen` は開いたときに動く） |
