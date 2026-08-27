"""フィルタ入力 → SOQL を config 駆動で組み立てる。

★フィルタは config の `ringi.filters` に**宣言**として書く。ここのコードは
  その宣言を解釈するだけなので、検索条件を増やすときに Python/HTML の変更は要らない。

セキュリティ（SOQLインジェクション対策）:
- オブジェクト名・項目名・表示列・並び順は「識別子として妥当な形式か」を検証する
  （config の書き間違いがそのままSOQLに流れ込まないようにするため）。
- operator は許可リストからのみ選ぶ。
- 利用者が入力する値は型ごとに検証する:
    select … config の options に完全一致するものだけ
    date   … YYYY-MM-DD 形式のみ
    number … 数値形式のみ
  いずれも自由文字列がそのまま WHERE に入る経路を作らない。
"""

from __future__ import annotations

import re

# 例: Name / Status__c / RecordType.DeveloperName
_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)*$")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_NUMBER_RE = re.compile(r"^-?\d+(\.\d+)?$")
_ORDER_ITEM = r"[A-Za-z_][A-Za-z0-9_.]*(\s+(ASC|DESC))?(\s+NULLS\s+(FIRST|LAST))?"
_ORDER_RE = re.compile(rf"^{_ORDER_ITEM}(\s*,\s*{_ORDER_ITEM})*$", re.IGNORECASE)

# config の operator → SOQL の演算子
_OPERATORS = {"eq": "=", "ne": "!=", "gt": ">", "gte": ">=", "lt": "<", "lte": "<="}
_TYPES = ("select", "date", "number")


class QueryBuildError(ValueError):
    """フィルタ定義または入力値が不正なときに送出する。"""


def get_filter_defs(config: dict) -> list[dict]:
    """config に宣言されたフィルタ定義の一覧。画面描画にも使う。"""
    return list(config.get("ringi", {}).get("filters") or [])


def _check_identifier(value, what: str) -> str:
    text = str(value or "")
    if not _IDENT_RE.match(text):
        raise QueryBuildError(f"{what}が不正です: {value}")
    return text


def _parse_def(fdef: dict) -> tuple[str, str, str, str]:
    """フィルタ定義を検証して (name, field, type, SOQL演算子) を返す。"""
    name = str(fdef.get("name") or "").strip()
    if not name:
        raise QueryBuildError("フィルタ定義に name がありません。")

    ftype = str(fdef.get("type") or "").strip()
    if ftype not in _TYPES:
        raise QueryBuildError(
            f"フィルタ '{name}' の type が未対応です: {ftype}"
            f"（使えるのは {', '.join(_TYPES)}）"
        )

    op = str(fdef.get("operator") or "eq").strip()
    if op not in _OPERATORS:
        raise QueryBuildError(
            f"フィルタ '{name}' の operator が未対応です: {op}"
            f"（使えるのは {', '.join(_OPERATORS)}）"
        )

    field = _check_identifier(fdef.get("field"), f"フィルタ '{name}' の項目名(field)")
    return name, field, ftype, _OPERATORS[op]


def _literal(fdef: dict, name: str, ftype: str, value: str) -> str:
    """入力値を型に応じて検証し、SOQL に埋め込める形にして返す。"""
    label = fdef.get("label") or name

    if ftype == "select":
        allowed = {str(o.get("value")) for o in (fdef.get("options") or [])}
        if value not in allowed:
            raise QueryBuildError(f"{label}に不正な値が指定されました: {value}")
        return f"'{value}'"  # 許可リスト一致済みなのでクォートの混入はない

    if ftype == "date":
        if not _DATE_RE.match(value):
            raise QueryBuildError(f"{label}は YYYY-MM-DD 形式で指定してください: {value}")
        return value  # SOQL の日付リテラルはクォート無し

    if ftype == "number":
        if not _NUMBER_RE.match(value):
            raise QueryBuildError(f"{label}は数値で指定してください: {value}")
        return value

    raise QueryBuildError(f"フィルタ '{name}' の type が未対応です: {ftype}")


def build_ringi_query(config: dict, filters: dict | None = None) -> str:
    """config の宣言と入力値から SOQL を組み立てる。

    filters は {フィルタ名: 入力値} 。空・未指定のものは条件に含めない。
    """
    ringi = config["ringi"]
    filters = filters or {}

    obj = _check_identifier(ringi.get("object_api_name"), "オブジェクト名")

    columns = ringi.get("columns") or [ringi["fields"]["title"]]
    cols = ["Id"] + [
        _check_identifier(c, "表示列(columns)") for c in columns
    ]
    soql = f"SELECT {', '.join(dict.fromkeys(cols))} FROM {obj}"

    clauses: list[str] = []
    for fdef in get_filter_defs(config):
        name, field, ftype, op = _parse_def(fdef)
        raw = filters.get(name)
        value = str(raw).strip() if raw is not None else ""
        if not value:
            continue  # 未入力の条件は無視する
        clauses.append(f"{field} {op} {_literal(fdef, name, ftype, value)}")

    if clauses:
        soql += " WHERE " + " AND ".join(clauses)

    order_by = str(ringi.get("order_by") or "").strip()
    if order_by:
        if not _ORDER_RE.match(order_by):
            raise QueryBuildError(f"並び順(order_by)が不正です: {order_by}")
        soql += f" ORDER BY {order_by}"

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
