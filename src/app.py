"""ローカルWeb UI（Flask）。

条件をプルダウン/日付で選び、検索→一覧確認→添付ファイル一括ダウンロードを行う。
127.0.0.1 のみで待ち受ける単一ユーザー向け（第1弾）。
"""

from __future__ import annotations

import os
import secrets

import yaml
from flask import Flask, redirect, render_template, request, session, url_for

import oauth
from sf_client import get_connection, SalesforceError
from query_builder import (
    build_ringi_query,
    get_filter_defs,
    validate_config,
    check_config_warnings,
    validate_raw_soql,
    extract_object_name,
    QueryBuildError,
)
import downloader

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_PATH = os.path.join(BASE_DIR, "config", "config.yaml")
SECRET_PATH = os.path.join(BASE_DIR, ".flask_secret")

app = Flask(__name__)


def _load_secret_key() -> bytes:
    """セッション署名鍵。再起動でログアウトされないようファイルに保持する。"""
    if os.path.exists(SECRET_PATH):
        with open(SECRET_PATH, "rb") as fh:
            return fh.read()
    key = secrets.token_bytes(32)
    with open(SECRET_PATH, "wb") as fh:
        fh.write(key)
    return key


app.secret_key = _load_secret_key()

# トークンはクッキーに置かず、プロセス内に保持する（セッションIDのみクッキー）
_TOKEN_STORE: dict[str, dict] = {}


class ConfigError(RuntimeError):
    """config.yaml が読めない・内容が不正なときに送出する。"""


def load_config() -> dict:
    """config.yaml を読む。書式ミスは原因が分かる形にして送出する。"""
    try:
        with open(CONFIG_PATH, encoding="utf-8") as fh:
            return yaml.safe_load(fh)
    except FileNotFoundError:
        raise ConfigError(
            f"設定ファイルが見つかりません: {CONFIG_PATH}\n"
            "config/config.example.yaml をコピーして config/config.yaml を作成してください。"
        ) from None
    except yaml.YAMLError as exc:
        # 行番号が分かると直す場所が特定できる
        mark = getattr(exc, "problem_mark", None)
        where = f"（{mark.line + 1} 行目付近）" if mark else ""
        problem = getattr(exc, "problem", None) or str(exc)
        raise ConfigError(
            f"config.yaml の書式が正しくありません{where}: {problem}\n"
            "インデント（半角スペース）のずれがないか確認してください。"
        ) from None


# ---------------------------------------------------------------------------
# OAuth セッション
# ---------------------------------------------------------------------------
def _current_tokens() -> dict | None:
    sid = session.get("sid")
    return _TOKEN_STORE.get(sid) if sid else None


def _store_tokens(tokens: dict) -> None:
    sid = session.get("sid")
    if not sid:
        sid = secrets.token_urlsafe(24)
        session["sid"] = sid
    _TOKEN_STORE[sid] = tokens


def _clear_tokens() -> dict | None:
    sid = session.pop("sid", None)
    return _TOKEN_STORE.pop(sid, None) if sid else None


def _connect(config: dict):
    """現在のセッション状態に応じた接続を返す（OAuth優先・CLIフォールバック）。"""
    tokens = _current_tokens()

    def on_refresh():
        """アクセストークン期限切れ時に呼ばれる。新しいトークンを返す。"""
        if not tokens or not tokens.get("refresh_token"):
            return None
        try:
            new = oauth.refresh_access_token(config, tokens["refresh_token"])
        except oauth.OAuthError:
            return None
        tokens["access_token"] = new.get("access_token", tokens["access_token"])
        if new.get("instance_url"):
            tokens["instance_url"] = new["instance_url"]
        # 「更新トークンのローテーション」が有効な組織では、更新のたびに新しい
        # リフレッシュトークンが発行され古いものは失効する。必ず保存し直す。
        if new.get("refresh_token"):
            tokens["refresh_token"] = new["refresh_token"]
        _store_tokens(tokens)
        return tokens["access_token"]

    return get_connection(config, oauth_session=tokens, on_refresh=on_refresh)


def _connection_status(config: dict) -> dict:
    """画面ヘッダーに出す接続状態。"""
    tokens = _current_tokens()
    if tokens:
        return {
            "connected": True,
            "via": "oauth",
            "user": tokens.get("username") or "(ユーザー不明)",
            "org": tokens.get("instance_url", ""),
        }
    if (config["salesforce"].get("auth_mode") or "cli") == "cli":
        return {
            "connected": True,
            "via": "cli",
            "user": f"sf CLI: {config['salesforce'].get('cli_target_org', '既定org')}",
            "org": "",
        }
    return {"connected": False, "via": None, "user": None, "org": ""}


def _read_filters(config: dict, form) -> dict:
    """config に宣言されたフィルタ名だけをフォームから読む。

    フィルタを増やしてもここは変更不要（config の定義に自動で追随する）。
    """
    filters: dict = {}
    for fdef in get_filter_defs(config):
        name = str(fdef.get("name") or "").strip()
        if not name:
            continue
        value = (form.get(name) or "").strip()
        filters[name] = value or None
    return filters


def _build_soql(config: dict, mode: str, filters: dict, raw_soql: str | None) -> str:
    """モードに応じて SOQL を用意する。"""
    if mode == "soql":
        return validate_raw_soql(raw_soql)
    return build_ringi_query(config, filters)


def _get_path(record: dict, dotted: str):
    """'RecordType.DeveloperName' のようなリレーション項目を安全に取り出す。"""
    cur = record
    for part in dotted.split("."):
        if not isinstance(cur, dict):
            return None
        cur = cur.get(part)
    return cur


def _flatten(record: dict, prefix: str = "") -> dict:
    """レコードを 'RecordType.DeveloperName' のようなドット表記に平坦化する。"""
    flat: dict = {}
    for key, value in record.items():
        if key == "attributes":
            continue
        name = f"{prefix}{key}"
        if isinstance(value, dict):
            flat.update(_flatten(value, prefix=f"{name}."))
        else:
            flat[name] = value
    return flat


def _to_table(records: list[dict], counts: dict) -> tuple[list[str], list[dict]]:
    """クエリが返した項目に追随する動的な列とデータ行を作る。

    SOQL直接入力モードでは列が可変のため、固定列にせずレコード側から列を決める。
    """
    columns: list[str] = []
    flat_rows: list[dict] = []
    for rec in records:
        flat = _flatten(rec)
        flat_rows.append(flat)
        for key in flat:
            if key not in columns:
                columns.append(key)

    rows = []
    for rec, flat in zip(records, flat_rows):
        # キー名 "values" は Jinja で dict.values メソッドと衝突するため "cells" とする
        rows.append({
            "id": rec.get("Id"),
            "cells": [flat.get(col) for col in columns],
            "attachments": counts.get(rec.get("Id"), 0),
        })
    return columns, rows


def _config_error_page(message: str):
    """設定が読めない場合でも、原因を伝える画面を返す（500にしない）。"""
    empty = {"ringi": {"filters": []}, "salesforce": {}}
    return render_template(
        "index.html", config=empty, filters={}, mode="form",
        raw_soql="", status=None, config_error=message,
    )


@app.route("/")
def index():
    try:
        config = load_config()
    except ConfigError as exc:
        return _config_error_page(str(exc))

    problems = validate_config(config)
    if problems:
        return _config_error_page("\n".join(problems))

    default_soql = build_ringi_query(config)
    return render_template(
        "index.html", config=config, filters={}, mode="form",
        raw_soql=default_soql, status=_connection_status(config),
        config_warnings=check_config_warnings(config),
    )


def _run(config: dict, do_download: bool):
    """検索（＋任意でダウンロード）を実行し、テンプレートを描画する。"""
    mode = request.form.get("mode") or "form"
    filters = _read_filters(config, request.form)
    raw_soql = request.form.get("raw_soql") or ""

    ctx = {
        "config": config, "filters": filters, "mode": mode, "raw_soql": raw_soql,
        "status": _connection_status(config),
        "config_warnings": check_config_warnings(config),
    }

    try:
        soql = _build_soql(config, mode, filters, raw_soql)
        conn = _connect(config)
        records = conn.query(soql)
        counts = downloader.attachment_counts(conn, config, [r["Id"] for r in records])
        summary = (
            downloader.download_for_records(conn, config, records) if do_download else None
        )
    except (QueryBuildError, SalesforceError) as exc:
        return render_template("index.html", error=str(exc), **ctx)

    columns, rows = _to_table(records, counts)
    if mode == "form":
        ctx["raw_soql"] = soql  # フォームで組んだSOQLを直接入力欄にも反映する

    return render_template(
        "index.html",
        columns=columns,
        results=rows,
        soql=soql,
        object_name=extract_object_name(soql),
        download_summary=summary,
        **ctx,
    )


# ---------------------------------------------------------------------------
# OAuth ルート（画面内の「Salesforceに接続」ボタンから使う）
# ---------------------------------------------------------------------------
@app.route("/oauth/login")
def oauth_login():
    """PKCE を生成し、Salesforce の認可画面へリダイレクトする。"""
    try:
        config = load_config()
    except ConfigError as exc:
        return _config_error_page(str(exc))
    try:
        verifier, challenge = oauth.generate_pkce()
        state = oauth.generate_state()
        session["pkce_verifier"] = verifier
        session["oauth_state"] = state
        return redirect(oauth.build_authorize_url(config, challenge, state))
    except oauth.OAuthError as exc:
        return render_template(
            "index.html", config=config, filters={}, mode="form",
            raw_soql="", status=_connection_status(config), error=str(exc),
        )


@app.route("/oauth/callback")
def oauth_callback():
    """認可コードを受け取り、トークンに交換して保存する。"""
    try:
        config = load_config()
    except ConfigError as exc:
        return _config_error_page(str(exc))
    error = request.args.get("error")
    code = request.args.get("code")
    state = request.args.get("state")
    expected_state = session.pop("oauth_state", None)
    verifier = session.pop("pkce_verifier", None)

    message = None
    if error:
        message = f"認可が拒否されました: {request.args.get('error_description') or error}"
    elif not code or not verifier:
        message = "認可コードが取得できませんでした。もう一度接続してください。"
    elif not state or state != expected_state:
        message = "state が一致しません（CSRFの可能性）。もう一度接続してください。"
    else:
        try:
            tokens = oauth.exchange_code(config, code, verifier)
            info = oauth.fetch_userinfo(tokens.get("instance_url", ""), tokens["access_token"])
            _store_tokens({
                "access_token": tokens["access_token"],
                "refresh_token": tokens.get("refresh_token"),
                "instance_url": tokens.get("instance_url", ""),
                "username": info.get("preferred_username") or info.get("email"),
            })
            return redirect(url_for("index"))
        except (oauth.OAuthError, KeyError) as exc:
            message = str(exc)

    return render_template(
        "index.html", config=config, filters={}, mode="form",
        raw_soql=build_ringi_query(config), status=_connection_status(config),
        error=message,
    )


@app.route("/oauth/logout", methods=["POST"])
def oauth_logout():
    """トークンを失効させて切断する。"""
    try:
        config = load_config()
    except ConfigError as exc:
        return _config_error_page(str(exc))
    tokens = _clear_tokens()
    if tokens and tokens.get("access_token"):
        oauth.revoke_token(config, tokens["access_token"])
    return redirect(url_for("index"))


@app.route("/search", methods=["POST"])
def search():
    try:
        return _run(load_config(), do_download=False)
    except ConfigError as exc:
        return _config_error_page(str(exc))


@app.route("/download", methods=["POST"])
def download():
    try:
        return _run(load_config(), do_download=True)
    except ConfigError as exc:
        return _config_error_page(str(exc))


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=8080, debug=True)
