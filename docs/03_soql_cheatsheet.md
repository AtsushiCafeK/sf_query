# SOQL / スキーマ探索 チートシート

Salesforceを「テーブル＝オブジェクト / 行＝レコード / 列＝項目」と読み替えると分かりやすい。
自分でクエリを書いたりコードを直したりするための最小知識をここにまとめる。

> 💡 **ここで書いたSOQLは、ツールの「SOQLを直接入力」タブにそのまま貼り付けて実行できる。**
> 検索結果の一覧表示も、添付ファイルの一括ダウンロードもそのまま使える。
> 制約は2つだけ:
> - `SELECT` で始まる**読み取り専用**のクエリのみ（`UPDATE`/`DELETE` 等は拒否される）
> - **SELECT句に `Id` を必ず含める**（添付ファイルを辿るのに使うため）
>
> 表示される列は、クエリで指定した項目に自動で追随する。

---

## 1. スキーマを探索する（＝ ls / describe 相当）

### テーブル（オブジェクト）一覧を見る

開発者コンソール → Query Editor に貼る:

```sql
-- カスタムオブジェクトだけ見たいなら QualifiedApiName LIKE '%__c'
SELECT QualifiedApiName, Label
FROM EntityDefinition
ORDER BY QualifiedApiName
LIMIT 200
```

### あるテーブルの項目（列）一覧を見る = describe

```sql
SELECT QualifiedApiName, Label, DataType
FROM FieldDefinition
WHERE EntityDefinition.QualifiedApiName = 'Ringi__c'
```

> `QualifiedApiName` が、コードや config.yaml に書く「実API名」。
> 本番TeamSpiritに繋いだ後、実際のオブジェクト/項目名を調べる時もこれを使う。

### 選択リスト（ステータス等）の値を確認したいとき

項目定義はオブジェクトマネージャのGUIが早いが、実データにどんな値が入っているかは:

```sql
SELECT Status__c, COUNT(Id) FROM Ringi__c GROUP BY Status__c
```

---

## 2. SOQLの基本形

```sql
SELECT 項目1, 項目2, ...
FROM   オブジェクト
WHERE  条件
ORDER BY 項目 [ASC|DESC]
LIMIT  件数
```

### 例：稟議書を条件で絞り込む（このツールの中核）

```sql
SELECT Id, Name, Status__c, ApplicationDate__c, RecordType.DeveloperName
FROM Ringi__c
WHERE RecordType.DeveloperName = 'Purchase'      -- レコードの種類
  AND Status__c = 'Approved'                     -- ステータス
  AND ApplicationDate__c >= 2026-01-01           -- 期間（開始）
  AND ApplicationDate__c <= 2026-06-30           -- 期間（終了）
ORDER BY ApplicationDate__c DESC
```

### WHERE でよく使う書き方

| やりたいこと | 書き方 |
|---|---|
| 一致 | `Status__c = 'Approved'` |
| 複数候補のどれか | `Status__c IN ('Approved','Submitted')` |
| 部分一致 | `Name LIKE '%契約%'` |
| 日付範囲 | `ApplicationDate__c >= 2026-01-01 AND ApplicationDate__c <= 2026-06-30` |
| 相対日付 | `ApplicationDate__c = THIS_MONTH` / `LAST_N_DAYS:30` |
| 空でない | `Applicant__c != null` |
| かつ / または | `AND` / `OR`（優先順位は `()` で明示） |

> 日付リテラルは **クォート無し**（`2026-01-01`）。日時は `2026-01-01T00:00:00Z` の形式。
> 文字列はシングルクォート（`'Approved'`）。

---

## 3. 添付ファイル（Salesforce Files）を辿る3段クエリ

このツールが一括ダウンロードで内部的にやること。

```sql
-- ① レコードに紐づくファイルのIDを得る
SELECT ContentDocumentId, ContentDocument.Title, ContentDocument.FileExtension
FROM ContentDocumentLink
WHERE LinkedEntityId = '<稟議レコードのId>'
```

```sql
-- ② 最新バージョン（ダウンロード対象）を得る
SELECT Id, Title, FileExtension, ContentSize
FROM ContentVersion
WHERE ContentDocumentId = '<①のContentDocumentId>' AND IsLatest = true
```

③ 本体のバイナリは SOQLではなく REST の Blob Get で取得:

```
GET /services/data/v60.0/sobjects/ContentVersion/{②のId}/VersionData
```

> 複数レコードをまとめて辿るときは IN が使える:
> `WHERE LinkedEntityId IN ('id1','id2','id3', ...)`

---

## 4. コマンドライン（Salesforce CLI `sf`）

「サーバをコマンドで操作する」感覚に一番近い。インストール後、まず一度だけブラウザでログイン:

```bash
sf org login web --alias devorg
```

| やりたいこと | コマンド |
|---|---|
| 接続済み組織の一覧 | `sf org list` |
| テーブル一覧（カスタムのみ） | `sf sobject list --sobject custom` |
| テーブルの項目定義（describe） | `sf sobject describe --sobject Ringi__c` |
| SOQLを実行 | `sf data query --query "SELECT Id, Name FROM Ringi__c"` |
| 結果をCSVで | `sf data query --query "SELECT Id FROM Ringi__c" --result-format csv` |
| ファイル(添付)を1つDL | `sf data get file --content-version-id <ContentVersionId> --output-file out.pdf` |

> `sf org login web` は、このツール本体でも使う **OAuth 2.0 認可コードフロー**と同じ仕組み。
> CLIで一度体験しておくと、後のOAuth実装の理解が早い。

---

## 5. REST APIエンドポイント早見（コードが内部で叩くもの）

| 目的 | メソッド / パス |
|---|---|
| 全オブジェクト一覧 | `GET /services/data/v60.0/sobjects/` |
| オブジェクトのdescribe | `GET /services/data/v60.0/sobjects/Ringi__c/describe/` |
| SOQL実行 | `GET /services/data/v60.0/query/?q=<URLエンコードしたSOQL>` |
| バイナリ取得 | `GET /services/data/v60.0/sobjects/ContentVersion/{id}/VersionData` |

`vXX.0` はAPIバージョン。config.yaml の `api_version` と揃える。
