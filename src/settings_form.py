"""設定画面のフォーム入力を config の構造へ反映する。

YAMLを直接書かずに設定できるようにするのが目的。ここでは
「画面の入力 → config の値」の変換だけを担当し、検証と保存は
config_store.save() に任せる（壊れた設定を書き込まないため）。

コメントを保った状態（ruamel のオブジェクト）を受け取り、その場で
書き換えるので、既存の日本語コメントは失われない。
"""

from __future__ import annotations

import re

from ruamel.yaml.comments import CommentedMap, CommentedSeq

# 画面から来るフィルタ名。SOQLに触れないが、フォーム項目名になるため素性を絞る
_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

# 秘密情報の欄に「変更しない」意思を表す印（保存済みの値は画面に出さない）
SECRET_PLACEHOLDER = "********"


class FormError(ValueError):
    """入力が受け付けられないときに送出する。"""


def _section(data, key: str):
    """セクションが無ければ作って返す（コメント付きの既存構造は壊さない）。"""
    if key not in data or not isinstance(data.get(key), dict):
        data[key] = CommentedMap()
    return data[key]


def _set(node, key: str, value) -> None:
    """値を設定する。None は「触らない」を意味する。"""
    if value is not None:
        node[key] = value


def _replace_seq(node, key: str, items: list) -> None:
    """リストの中身だけ入れ替える。

    `node[key] = 新しいリスト` と再代入すると、そのキーに紐づくコメント
    （直後のキーの前に置かれた説明ブロックなど）が失われる。既存の
    リストを使い回して中身だけ差し替えることで、コメントを保つ。
    """
    existing = node.get(key)
    if not isinstance(existing, CommentedSeq):
        node[key] = CommentedSeq(items)
        return

    # リスト末尾に付いたコメント（次のキーの前に置かれた説明）を引き継ぐ
    trailing = existing.ca.items.get(len(existing) - 1) if len(existing) else None
    del existing[:]
    existing.extend(items)
    if trailing is not None and items:
        existing.ca.items[len(items) - 1] = trailing


def _checkbox(form, name: str) -> bool:
    return form.get(name) in ("on", "true", "1", "yes")


def apply_connection(data, form) -> None:
    """① 接続設定（テナント・キー類）。"""
    sf = _section(data, "salesforce")

    login_url = (form.get("login_url") or "").strip()
    if login_url and not login_url.startswith("https://"):
        raise FormError("ログインURLは https:// で始まる必要があります。")
    _set(sf, "login_url", login_url or None)

    auth_mode = (form.get("auth_mode") or "").strip()
    if auth_mode and auth_mode not in ("oauth", "cli"):
        raise FormError(f"認証方式が不正です: {auth_mode}")
    _set(sf, "auth_mode", auth_mode or None)

    api_version = (form.get("api_version") or "").strip()
    if api_version and not re.match(r"^\d+\.\d+$", api_version):
        raise FormError("APIバージョンは 60.0 のような形式で指定してください。")
    _set(sf, "api_version", api_version or None)

    _set(sf, "cli_target_org", (form.get("cli_target_org") or "").strip() or None)

    oauth = _section(sf, "oauth")
    # 空欄やプレースホルダのままなら既存の値を維持する（キーを消させない）
    for field in ("client_id", "client_secret"):
        raw = (form.get(field) or "").strip()
        if raw and raw != SECRET_PLACEHOLDER:
            oauth[field] = raw
        elif field not in oauth:
            oauth[field] = ""
    _set(oauth, "redirect_uri", (form.get("redirect_uri") or "").strip() or None)


def apply_object(data, form) -> None:
    """② 対象オブジェクトと項目の割り当て・表示列。"""
    ringi = _section(data, "ringi")

    _set(ringi, "object_api_name", (form.get("object_api_name") or "").strip() or None)

    fields = _section(ringi, "fields")
    for role in ("title", "status", "application_date"):
        submitted = form.get(f"field_{role}")
        value = (submitted or "").strip()
        if value:
            fields[role] = value
        elif role == "title":
            # 件名はフォルダ名・一覧表に使うため外させない。空で送られたら理由を返す。
            if submitted is not None:
                raise FormError(
                    "件名にする項目は必須です。ダウンロード時のフォルダ名と"
                    "一覧表の件名に使います。"
                )
        elif role in fields:
            del fields[role]   # 件名以外は割り当てを外せる

    columns = [c.strip() for c in form.getlist("columns") if c.strip()]
    if columns:
        _replace_seq(ringi, "columns", columns)

    order_field = (form.get("order_by_field") or "").strip()
    if order_field:
        direction = (form.get("order_by_dir") or "DESC").strip().upper()
        if direction not in ("ASC", "DESC"):
            raise FormError(f"並び順の向きが不正です: {direction}")
        ringi["order_by"] = f"{order_field} {direction} NULLS LAST"

    attach = (form.get("attachment_type") or "").strip()
    if attach:
        if attach not in ("files", "attachment"):
            raise FormError(f"添付形式が不正です: {attach}")
        ringi["attachment_type"] = attach


def _parse_filter(form, index: str) -> dict | None:
    """1件分のフィルタ入力を読む。項目が空なら「未入力」として無視する。"""
    field = (form.get(f"f_{index}_field") or "").strip()
    if not field or _checkbox(form, f"f_{index}_delete"):
        return None

    name = (form.get(f"f_{index}_name") or "").strip()
    if not name:
        raise FormError(f"検索条件の項目名（name）が空です（{field}）。")
    if not _NAME_RE.match(name):
        raise FormError(
            f"検索条件の項目名は英数字とアンダースコアで指定してください: {name}"
        )

    ftype = (form.get(f"f_{index}_type") or "").strip()
    if ftype not in ("select", "date", "number"):
        raise FormError(f"検索条件の型が不正です（{name}）: {ftype}")

    operator = (form.get(f"f_{index}_operator") or "eq").strip()

    entry = CommentedMap()
    entry["name"] = name
    entry["label"] = (form.get(f"f_{index}_label") or name).strip()
    entry["type"] = ftype
    entry["field"] = field
    entry["operator"] = operator

    if ftype == "select":
        options = CommentedSeq()
        raw_values = form.getlist(f"f_{index}_opt_value")
        raw_labels = form.getlist(f"f_{index}_opt_label")
        for i, value in enumerate(raw_values):
            value = (value or "").strip()
            if not value:
                continue
            label = (raw_labels[i] if i < len(raw_labels) else "").strip() or value
            opt = CommentedMap()
            opt["value"] = value
            opt["label"] = label
            options.append(opt)
        if not options:
            raise FormError(
                f"「{entry['label']}」はプルダウンですが選択肢がありません。"
                "Salesforceから取得するか、手で追加してください。"
            )
        entry["options"] = options

    return entry


def apply_filters(data, form) -> None:
    """③ 検索条件（フィルタ）。画面の並び順をそのまま設定の順序にする。"""
    ringi = _section(data, "ringi")
    indexes = form.getlist("filter_index")

    entries = []
    seen: set[str] = set()
    for index in indexes:
        entry = _parse_filter(form, index)
        if entry is None:
            continue
        if entry["name"] in seen:
            raise FormError(f"検索条件の項目名が重複しています: {entry['name']}")
        seen.add(entry["name"])
        entries.append(entry)

    _replace_seq(ringi, "filters", entries)


def apply_download(data, form) -> None:
    """④ ダウンロード設定。"""
    dl = _section(data, "download")
    _set(dl, "output_dir", (form.get("output_dir") or "").strip() or None)
    dl["zip"] = _checkbox(form, "zip")
    dl["group_by_record"] = _checkbox(form, "group_by_record")
    dl["manifest"] = _checkbox(form, "manifest")


def apply_all(data, form) -> None:
    """設定画面の全セクションを反映する。"""
    apply_connection(data, form)
    apply_object(data, form)
    apply_filters(data, form)
    apply_download(data, form)
