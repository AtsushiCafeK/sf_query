# モックスキーマ設計（稟議書ダミーオブジェクト）

TeamSpiritがなくても開発・テストできるよう、開発org上に稟議書を模した**カスタムオブジェクト1つ**を作る。
TeamSpiritの稟議書も実体はSalesforceの標準機能（カスタムオブジェクト＋RecordType＋選択リスト＋Files）なので、
同じ構造をモックで再現すれば、本番はAPI名を差し替えるだけで動く。

## オブジェクト

| 項目 | 値 |
|---|---|
| 表示ラベル | 稟議書（テスト） |
| API参照名 | `Ringi__c` |
| 「レコードタイプ」を有効化 | ✅ する（種類の絞り込みに使う） |
| 「ファイル」を有効化（Notes & Attachments / Files） | ✅ する（添付の絞り込みに使う） |

> ⚠️ 本番TeamSpiritでは、このオブジェクト名が `teamspirit__XXXX__c` のような名前になる。
> コードはすべて `config.yaml` のマッピング経由で参照するので、名前が違っても設定変更だけで対応できる。

## レコードタイプ（＝「レコードの種類」の絞り込み対象）

| 表示ラベル | API名 | 用途 |
|---|---|---|
| 購買稟議 | `Purchase` | 物品購入の稟議 |
| 契約稟議 | `Contract` | 契約締結の稟議 |
| 支払稟議 | `Payment` | 支払承認の稟議 |

## カスタム項目

| 表示ラベル | API参照名 | 型 | 用途 | 備考 |
|---|---|---|---|---|
| 件名 | （標準 `Name`） | テキスト | 稟議のタイトル | 標準の名前項目を利用 |
| ステータス | `Status__c` | 選択リスト | **ステータス絞り込み** | 下記の値 |
| 申請日 | `ApplicationDate__c` | 日付 | **期間絞り込み** | `CreatedDate`でも可だが専用項目が安全 |
| 金額 | `Amount__c` | 通貨 | 稟議金額（リアリティ用） | 任意 |
| 申請者 | `Applicant__c` | テキスト（またはユーザー参照） | 申請者名 | 任意 |

### `Status__c` の選択リスト値

> ⚠️ 下記は当初の設計案。**開発orgに実際に作られた値は `Submitted` / `Agree` / `Done` の3つ**
> （`sobjects/Ringi__c/describe` で確認済み）。`config.yaml` はこの実値に合わせてある。
> 選択リスト値を変えたときは config の `ringi.statuses` も合わせて更新すること。

| API値（＝保存される文字列） | 表示ラベル |
|---|---|
| `Submitted` | 申請中 |
| `Agree` | 承認 |
| `Done` | 完了 |

> UIのプルダウンにはこれらを表示し、内部ではAPI値でSOQL絞り込みする。
> 本番TeamSpiritのステータス値が異なる場合も、`config.yaml` の選択肢定義を差し替えるだけでよい。

## 絞り込みとSOQLの対応

UIで選ぶ3条件が、そのままSOQLのWHERE句になる。

| UIの選択 | 対応するSOQL条件（例） |
|---|---|
| レコードの種類＝購買稟議 | `RecordType.DeveloperName = 'Purchase'` |
| 期間＝2026-01-01〜2026-06-30 | `ApplicationDate__c >= 2026-01-01 AND ApplicationDate__c <= 2026-06-30` |
| ステータス＝承認済 | `Status__c = 'Approved'` |

組み立て後のSOQL（イメージ）:

```sql
SELECT Id, Name, Status__c, ApplicationDate__c
FROM Ringi__c
WHERE RecordType.DeveloperName = 'Purchase'
  AND Status__c = 'Approved'
  AND ApplicationDate__c >= 2026-01-01
  AND ApplicationDate__c <= 2026-06-30
```

## 添付ファイルの取得経路（Salesforce Files を採用）

TeamSpiritの添付は多くの場合 **Salesforce Files（ContentDocument系）**。取得は3段:

1. 稟議レコードのIdで紐づくファイルを特定
   ```sql
   SELECT ContentDocumentId FROM ContentDocumentLink WHERE LinkedEntityId = '<稟議レコードId>'
   ```
2. 最新バージョンのContentVersionを取得
   ```sql
   SELECT Id, Title, FileExtension, VersionData
   FROM ContentVersion
   WHERE ContentDocumentId = '<ContentDocumentId>' AND IsLatest = true
   ```
3. バイナリをダウンロード（REST Blob Get）
   ```
   GET /services/data/vXX.0/sobjects/ContentVersion/{ContentVersionId}/VersionData
   ```

> 旧「添付ファイル（Attachment）」形式のデータが混在する場合に備え、`config.yaml` の
> `attachment_type` で `files` / `attachment` を切り替えられる設計にする。

## テストデータの投入（最低限）

- 各レコードタイプに2〜3件ずつ稟議レコードを作成
- ステータスを4種類ばらけさせる
- 申請日を数ヶ月に分散
- いくつかのレコードにファイルを1〜複数添付（PDF・画像・Excelなど混在させると検証に良い）

具体的な作成手順は `docs/02_dev_org_setup.md` を参照。
