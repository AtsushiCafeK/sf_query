"""Salesforce のスキーマ（オブジェクト・項目・選択リスト値）を設定画面向けに整形する。

設定画面でAPI名を手入力させないための土台。項目のラベル・型・選択リスト値を
そのまま画面のプルダウンに使える形にして返す。

describe は応答が大きく呼び出しも遅いため、プロセス内に短時間キャッシュする。
"""

from __future__ import annotations

import time

# Salesforce の項目型 → このツールのフィルタ型
_TYPE_MAP = {
    "picklist": "select",
    "multipicklist": "select",
    "combobox": "select",
    "boolean": "select",
    "date": "date",
    "datetime": "date",
    "int": "number",
    "double": "number",
    "currency": "number",
    "percent": "number",
}

_CACHE: dict[str, tuple[float, object]] = {}
_TTL_SECONDS = 300


def _cached(key: str, produce):
    hit = _CACHE.get(key)
    now = time.time()
    if hit and now - hit[0] < _TTL_SECONDS:
        return hit[1]
    value = produce()
    _CACHE[key] = (now, value)
    return value


def clear_cache() -> None:
    """設定を保存した直後など、取り直したいときに使う。"""
    _CACHE.clear()


def list_objects(conn) -> list[dict]:
    """設定画面のオブジェクト選択に使う一覧。

    カスタムオブジェクトを先に並べる（探しているものが上に来るように）。
    """
    def produce():
        items = []
        for obj in conn.list_objects():
            if not obj.get("queryable"):
                continue
            name = obj.get("name", "")
            items.append({
                "name": name,
                "label": obj.get("label") or name,
                "custom": bool(obj.get("custom")),
            })
        items.sort(key=lambda o: (not o["custom"], o["name"].lower()))
        return items

    return _cached("objects", produce)


def field_type_for(sf_type: str) -> str | None:
    """Salesforce の項目型から、フィルタの型を推測する（不明なら None）。"""
    return _TYPE_MAP.get(str(sf_type or "").lower())


def describe_fields(conn, sobject: str) -> list[dict]:
    """オブジェクトの項目一覧。選択リストは選択肢もそのまま持たせる。"""
    def produce():
        described = conn.describe(sobject)
        fields = []
        for f in described.get("fields", []):
            name = f.get("name", "")
            sf_type = f.get("type", "")
            options = [
                {"value": p.get("value"), "label": p.get("label") or p.get("value")}
                for p in (f.get("picklistValues") or [])
                if p.get("active", True)
            ]
            if sf_type == "boolean":  # true/false もプルダウンで扱えるようにする
                options = [{"value": "true", "label": "はい"},
                           {"value": "false", "label": "いいえ"}]
            fields.append({
                "name": name,
                "label": f.get("label") or name,
                "sf_type": sf_type,
                "filter_type": field_type_for(sf_type),
                "custom": bool(f.get("custom")),
                "sortable": bool(f.get("sortable")),
                "options": options,
                # 画面のプルダウン表示（例: 分類 (bunrui__c)）
                "display": f"{f.get('label') or name} ({name})",
            })
        fields.sort(key=lambda f: f["name"].lower())
        return fields

    return _cached(f"fields:{sobject}", produce)


def schema_for_settings(conn, sobject: str | None) -> dict:
    """設定画面が必要とするスキーマ情報をまとめて返す。

    接続できない場合でも画面自体は開けるよう、例外は呼び出し側で扱う。
    """
    data = {"objects": list_objects(conn), "fields": [], "object": sobject}
    if sobject:
        data["fields"] = describe_fields(conn, sobject)
    return data
