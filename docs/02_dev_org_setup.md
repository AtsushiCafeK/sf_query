# 開発org セットアップ手順

`docs/01_mock_schema.md` の設計を、Salesforce開発組織に実際に作る手順。
すべて **Setup（設定）画面のUI操作**で完結する（SFDK/CLI不要）。所要 20〜30分程度。

---

## A. 稟議書モックオブジェクトを作る

1. 設定 → オブジェクトマネージャ → **作成 → カスタムオブジェクト**
2. 以下を入力
   - 表示ラベル: `稟議書（テスト）`
   - 複数形のラベル: `稟議書（テスト）`
   - オブジェクト名（API）: `Ringi`（保存時に自動で `Ringi__c` になる）
   - レコード名: `件名`、データ型 = **テキスト**
   - ☑ **レポートを許可**、☑ **活動を許可**、☑ **検索を許可**
3. 保存

## B. カスタム項目を追加

オブジェクトマネージャ → `稟議書（テスト）` → **項目とリレーション → 新規** で以下を作成。

| ラベル | 型 | API名 | 選択リスト値など |
|---|---|---|---|
| ステータス | 選択リスト | `Status` | `Submitted`, `Approved`, `Rejected`, `Returned`（値は英語、ラベル日本語で表示名を付けてもOK） |
| 申請日 | 日付 | `ApplicationDate` | — |
| 金額 | 通貨 | `Amount` | 任意 |
| 申請者 | テキスト(255) | `Applicant` | 任意 |

> 選択リストの「値」はSOQLで一致させる文字列になるので、`docs/01_mock_schema.md` のAPI値と揃えること。

## C. レコードタイプを作る

オブジェクトマネージャ → `稟議書（テスト）` → **レコードタイプ → 新規** を3回。

| レコードタイプラベル | レコードタイプ名（DeveloperName） |
|---|---|
| 購買稟議 | `Purchase` |
| 契約稟議 | `Contract` |
| 支払稟議 | `Payment` |

- 各作成時に「システム管理者」など自分のプロファイルに割り当て（有効化）する。

## D. タブを作って画面から入力できるようにする（任意だが便利）

1. 設定 → **タブ → カスタムオブジェクトタブ → 新規**
2. オブジェクト = `稟議書（テスト）`、スタイル任意 → 保存
3. アプリランチャーから「稟議書（テスト）」を開けるようになる

## E. テストデータとファイルを投入

1. 稟議書タブ → **新規** で、レコードタイプを選びながら数件ずつ作成
   - ステータス4種・申請日を数ヶ月に分散
2. 各レコード詳細画面の **「ファイル」関連リスト → ファイルを追加** で
   PDF・画像・Excelなどを1〜複数添付
   - これで `ContentDocumentLink`（レコード↔ファイルの紐づき）が自動生成される

---

## F. 動作確認（開発者コンソールでSOQLを試す）

設定右上 → **開発者コンソール** → 下部の **Query Editor** で:

```sql
SELECT Id, Name, Status__c, ApplicationDate__c, RecordType.DeveloperName
FROM Ringi__c
WHERE Status__c = 'Approved'
```

紐づくファイルの確認:

```sql
SELECT ContentDocumentId, LinkedEntityId
FROM ContentDocumentLink
WHERE LinkedEntityId = '<上で出た稟議のId>'
```

ここまで結果が返れば、モックは完成。次は接続アプリ（Connected App）登録とOAuth疎通に進む。

---

## 次のステップ：Connected App（OAuthの土台）

1. 設定 → **アプリケーションマネージャ → 新規接続アプリケーション**
2. OAuth設定を有効化し
   - コールバックURL: `http://localhost:8080/oauth/callback`
   - 選択したOAuthスコープ: `api`, `refresh_token, offline_access`
   - ☑ **PKCEの要求（Require Proof Key for Code Exchange）** を有効化
3. 保存後に発行される **Consumer Key（client_id）** を `config/config.yaml` に設定

> Consumer Secret は認可コードフロー+PKCEのデスクトップ/ローカル用途では原則不要（公開クライアント扱い）。
> サーバ側で秘匿保管できる構成にするなら secret も併用可。ここは実装時に確定する。
