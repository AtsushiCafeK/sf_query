# 稟議書 添付ファイル一括ダウンロードツール

TeamSpirit（Salesforce上のマネージドパッケージ）の稟議書を、**レコードの種類・期間・ステータス**で絞り込み、
紐づく添付ファイルを一括ダウンロードするツール。ローカルWeb UIで条件を選ぶだけで実行できる。

TeamSpirit環境は未入手のため、開発orgに作った稟議書モック `Ringi__c` で開発・検証している。
本番へは **config のAPI名を差し替えるだけ**で移行できる設計。

## セットアップ

**pyenv + Poetry** で環境を管理している。Python のバージョンは [.python-version](.python-version)、
依存パッケージは [pyproject.toml](pyproject.toml) / `poetry.lock` に固定されている。

初回のみ（Python 3.12.10 が未インストールの場合）:

```bash
pyenv install 3.12.10
```

依存パッケージをインストール（`.venv/` がプロジェクト直下に作られる）:

```bash
poetry install
```

設定ファイルを用意する（`config.yaml` は機密を含むため git 管理外）:

```bash
cp config/config.example.yaml config/config.yaml
```

## 使い方

```bash
poetry run python src/app.py
```

ブラウザで <http://127.0.0.1:8080> を開く。

> `poetry shell` を使うか `.venv/Scripts/activate` で仮想環境に入れば、
> 以降は `python src/app.py` だけで起動できる。

### 依存パッケージを追加するとき

```bash
poetry add <パッケージ名>
```

`pyproject.toml` と `poetry.lock` が自動更新される。`poetry.lock` は**コミットする**
（全員が同じバージョンで動くようにするため）。

### 2つの検索モード

| モード | 用途 |
|---|---|
| **条件を選ぶ** | レコードの種類・ステータスをプルダウン、期間を日付で選択。日常運用向け |
| **SOQLを直接入力** | 任意のSOQLを貼り付けて実行。**別オブジェクトや任意の項目**にも対応。柔軟に絞り込みたいとき向け |

SOQL直接入力の制約（安全のため）:
- `SELECT` で始まる**読み取り専用**のクエリのみ（更新系キーワードは拒否）
- **SELECT句に `Id` が必須**（添付ファイルの取得に使うため）
- 表示される列は、クエリで指定した項目に自動追随する

検索結果を確認したうえで「添付ファイルを一括ダウンロード」を押すと、`downloads/` に保存され
ZIPにまとめられる。

### 印刷作業を想定した並び順

ファイル／フォルダ名の先頭に **一覧と同じ並び順の連番**（`001_`, `002_` …）が付く。
名前順に並べれば画面の一覧と一致するので、印刷した紙の順番と突き合わせやすい。

> レコードID（`a00bm...`）は採番キーで業務的な並び順を持たないため、**ID順は一覧順と一致しない**。
> そのため順序は連番が担い、IDは追跡用に名前の末尾へ残している。

配置は [config/config.yaml](config/config.yaml) の `download.group_by_record` で切り替える。

| 設定 | 出力 | 向き |
|---|---|---|
| `true`（既定） | `001_件名_ID/請求書.pdf` | 整理・保管 |
| `false` | `001-1_件名_請求書.pdf`（平置き） | **印刷**（全選択して一括印刷しやすい） |

`download.manifest: true` で **一覧表（CSV）** も出力される。連番・件名・申請日・ステータス・
レコードID・ファイル名を一覧順で書き出すので、印刷物のチェックリストに使える
（Excelで開けるようBOM付きUTF-8）。添付が無いレコードも「添付なし」として行に残る。

## アーキテクチャ

```
[ブラウザ (ローカルWeb UI)]
        │  条件選択 または SOQL貼り付け
        ▼
[ローカルPythonサーバ (Flask)]
        │  1) SOQL を組み立て/検証        → query_builder.py
        │  2) 稟議書を検索                → /services/data/vXX.0/query
        │  3) 添付を特定                  → ContentDocumentLink → ContentVersion
        │  4) 本体を取得しZIP化           → /sobjects/ContentVersion/{id}/VersionData
        ▼
[Salesforce組織（開発org → 将来TeamSpirit）]
```

### モジュール構成

| ファイル | 役割 |
|---|---|
| [src/sf_client.py](src/sf_client.py) | 認証とREST呼び出し。`get_connection()` が唯一の入口 |
| [src/query_builder.py](src/query_builder.py) | config駆動のSOQL組み立て＋直接入力SOQLの検証 |
| [src/downloader.py](src/downloader.py) | 添付の特定・ダウンロード・ZIP化 |
| [src/app.py](src/app.py) | Flaskルーティング（`/` `/search` `/download`） |
| [config/config.yaml](config/config.yaml) | **API名マッピング**（本番切替の要）・認証設定 |

## 認証について

`config.yaml` の `salesforce.auth_mode` で切り替える。

| モード | 内容 | 状況 |
|---|---|---|
| `cli`（既定） | **sf CLI パススルー**（`sf api request rest`）。sf が資格情報を保管・更新し、アプリはトークンを一切扱わない。接続アプリ不要 | ✅ 開発用 |
| `oauth` | **OAuth 2.0 認可コードフロー + PKCE**。ユーザーごとにブラウザログイン | ✅ 本番用（接続アプリ要作成） |

`cli` のままでも接続ボタンは使え、OAuth接続した場合はそちらが優先される（未接続ならCLIにフォールバック）。

画面右上の **「Salesforceに接続」** ボタンからOAuthログインできる（同じ画面内で完結）。
接続すると右上に接続中のユーザー名が表示され、以降の検索・ダウンロードは**そのユーザーの権限**で実行される。
アクセストークンの期限切れはリフレッシュトークンで自動更新、「切断」でトークンを失効できる。

利用するには**接続アプリ（Connected App）の作成が必要**：
👉 [docs/04_oauth_setup.md](docs/04_oauth_setup.md)

SOAP API（ユーザー名＋パスワード＋セキュリティトークン方式）は使わない。

## TeamSpirit本番への切り替え

コードにオブジェクト名・項目名をベタ書きしていないため、[config/config.yaml](config/config.yaml) の
`ringi:` セクションを差し替えるだけでよい。

```yaml
ringi:
  object_api_name: "Ringi__c"        # → 例 "teamspirit__XXXX__c"
  fields:
    status: "Status__c"              # → TeamSpiritの実項目名
    application_date: "ApplicationDate__c"
  statuses:                          # → TeamSpiritの実際の選択リスト値
    - { value: "Submitted", label: "申請中" }
```

実API名の調べ方（接続後）:

```bash
sf sobject list --sobject custom
```

```bash
sf sobject describe --sobject <オブジェクトAPI名>
```

## 開発ロードマップ

1. ✅ モックスキーマ設計 … [docs/01_mock_schema.md](docs/01_mock_schema.md)
2. ✅ 開発org セットアップ … [docs/02_dev_org_setup.md](docs/02_dev_org_setup.md)
3. ✅ SOQL絞り込み検索（プルダウン＋直接入力）
4. ✅ 添付ファイル一括ダウンロード（連番付与・配置切替・一覧表CSV・ZIP化）
5. ✅ ローカルWeb UI
6. ✅ OAuth 2.0 認可コードフロー+PKCE（画面内の接続ボタン）… 接続アプリ作成後に疎通確認
7. ⬜ 段階3: 進捗表示・大量件数対応（Bulk API）・配布
8. ⬜ 段階4: TeamSpirit本番接続・検証

## ドキュメント

- [モックスキーマ設計](docs/01_mock_schema.md)
- [開発org セットアップ手順](docs/02_dev_org_setup.md)
- [SOQL / スキーマ探索 チートシート](docs/03_soql_cheatsheet.md)
- [OAuth 2.0 接続設定（接続アプリの作成）](docs/04_oauth_setup.md)
