"""フィルタ入力 → SOQL を config 駆動で組み立てる。

オブジェクト名・項目名はコードにベタ書きせず config から解決する。
本番 TeamSpirit へは config の API 名を差し替えるだけで対応できる。

セキュリティ（SOQLインジェクション対策）:
- record_type / status は config の許可リスト(value)と完全一致するもののみ採用。
- 日付は YYYY-MM-DD 形式を検証してから埋め込む。
- 自由入力文字列を WHERE に直接連結しない。
"""

from __future__ import annotations

import re

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class QueryBuildError(ValueError):
    """フィルタ入力が不正なときに送出する。"""


def _allowed_values(items: list[dict]) -> set[str]:
    return {str(item["value"]) for item in items}


def build_ringi_query(
    config: dict,
    record_type: str | None = None,
    status: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
) -> str:
    ringi = config["ringi"]
    obj = ringi["object_api_name"]
    fields = ringi["fields"]
    rt_field = ringi["record_type_developer_name_field"]

    # SELECT 句（重複列は除去して順序維持）
    select_cols = list(dict.fromkeys(
        ["Id", fields["title"], fields["status"], fields["application_date"], rt_field]
    ))
    soql = f"SELECT {', '.join(select_cols)} FROM {obj}"

    clauses: list[str] = []

    if record_type:
        if record_type not in _allowed_values(ringi["record_types"]):
            raise QueryBuildError(f"不正なレコードタイプです: {record_type}")
        clauses.append(f"{rt_field} = '{record_type}'")

    if status:
        if status not in _allowed_values(ringi["statuses"]):
            raise QueryBuildError(f"不正なステータスです: {status}")
        clauses.append(f"{fields['status']} = '{status}'")

    if date_from:
        if not _DATE_RE.match(date_from):
            raise QueryBuildError(f"開始日は YYYY-MM-DD 形式で指定してください: {date_from}")
        clauses.append(f"{fields['application_date']} >= {date_from}")

    if date_to:
        if not _DATE_RE.match(date_to):
            raise QueryBuildError(f"終了日は YYYY-MM-DD 形式で指定してください: {date_to}")
        clauses.append(f"{fields['application_date']} <= {date_to}")

    if clauses:
        soql += " WHERE " + " AND ".join(clauses)

    soql += f" ORDER BY {fields['application_date']} DESC NULLS LAST"
    return soql


# ---------------------------------------------------------------------------
# SOQL 直接入力モード
# ---------------------------------------------------------------------------
_FORBIDDEN = re.compile(
    r"\b(INSERT|UPDATE|DELETE|UPSERT|MERGE|DROP|ALTER|CREATE)\b", re.IGNORECASE
)
_FROM_RE = re.compile(r"\bFROM\s+([A-Za-z0-9_]+)", re.IGNORECASE)


def validate_raw_soql(soql: str) -> str:
    """貼り付けられた SOQL を検証して返す（読み取り専用のSELECTのみ許可）。

    任意のオブジェクト・項目に柔軟対応するためのモード。ダウンロード処理は
    レコードの Id を使うため、SELECT に Id が含まれることを必須とする。
    """
    text = (soql or "").strip().rstrip(";").strip()
    if not text:
        raise QueryBuildError("SOQL が空です。")

    if not re.match(r"^SELECT\b", text, re.IGNORECASE):
        raise QueryBuildError("SELECT で始まる読み取り専用のSOQLのみ実行できます。")

    if _FORBIDDEN.search(text):
        raise QueryBuildError("更新系のキーワードは使用できません（読み取り専用）。")

    if not _FROM_RE.search(text):
        raise QueryBuildError("FROM 句が見つかりません。")

    # 添付DLに Id が必要。SELECT句（最初のFROMまで）に Id があるか確認する。
    select_part = text[: _FROM_RE.search(text).start()]
    if not re.search(r"(^|[\s,])Id([\s,]|$)", select_part, re.IGNORECASE):
        raise QueryBuildError(
            "SELECT 句に Id を含めてください（添付ファイルの取得に必要です）。"
        )

    return text


def extract_object_name(soql: str) -> str | None:
    """SOQL から FROM のオブジェクト名を取り出す（表示用）。"""
    m = _FROM_RE.search(soql or "")
    return m.group(1) if m else None
