"""Salesforce 接続と REST 呼び出し。

2つの認証バックエンドを同一インターフェイス（query / download_blob）で提供する:

- SfCliConnection … sf CLI パススルー（`sf api request rest`）。トークンを露出せず、
  接続アプリも不要。sf が資格情報の保管・更新を担うため最も堅牢。第1弾の既定。
- TokenConnection  … アクセストークン + requests による直接 REST。段階2で
  OAuth 2.0 認可コードフロー+PKCE を実装したらこちらに切り替える。

呼び出し側は get_connection(config) だけを使い、config の salesforce.auth_mode で
バックエンドが切り替わる。
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
import urllib.parse

import requests


class SalesforceError(RuntimeError):
    pass


# sf は環境によって ANSI カラーコード付きで出力する（JSON解析を壊すため除去する）
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def _strip_ansi(text: str) -> str:
    return _ANSI_RE.sub("", text or "")


# ---------------------------------------------------------------------------
# 共通ヘルパ
# ---------------------------------------------------------------------------
def _extract_json(stdout: str):
    """sf の stdout（前後に注記が混ざる場合あり）から JSON 本体を取り出す。

    Salesforce のエラー応答は配列（[{"message": ...}]）で返るため、オブジェクト/配列の
    どちらの開始位置も探して先に現れた方から解析する。
    """
    text = _strip_ansi(stdout).strip()
    candidates = [pos for pos in (text.find("{"), text.find("[")) if pos != -1]
    if not candidates:
        raise SalesforceError(f"JSON応答が見つかりません: {text[:300]}")
    try:
        return json.loads(text[min(candidates):])
    except ValueError as exc:
        raise SalesforceError(f"応答を解析できません: {text[:300]}") from exc


# ---------------------------------------------------------------------------
# バックエンド1: sf CLI パススルー
# ---------------------------------------------------------------------------
class SfCliConnection:
    """`sf api request rest` 経由で REST を叩く。トークンを直接扱わない。"""

    def __init__(self, target_org: str | None, api_version: str):
        self.target_org = target_org
        self.api_version = str(api_version)

    def _run_sf(self, args: list[str]) -> subprocess.CompletedProcess:
        cmd = ["sf", *args]
        if self.target_org:
            cmd += ["--target-org", self.target_org]
        # カラー出力を無効化（ANSI escape が JSON 解析を壊すため）
        env = {**os.environ, "NO_COLOR": "1", "FORCE_COLOR": "0", "TERM": "dumb"}
        # sf は UTF-8 で出力する。Windows既定の cp932 で読むと日本語が壊れるため明示する。
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            shell=(os.name == "nt"),
            env=env,
        )
        if proc.returncode != 0:
            raise SalesforceError(
                "sf コマンド失敗。'sf org login web' でログイン済みか、"
                f"対象orgの別名が正しいか確認してください。\n{proc.stderr or proc.stdout}"
            )
        return proc

    def _rest_get(self, path: str) -> dict:
        # シェルのクォート/`%`展開問題を避けるため、リクエスト仕様を一時JSONで渡す
        spec = {"url": path, "method": "GET"}
        fd, spec_path = tempfile.mkstemp(suffix=".json", prefix="sfreq_")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(spec, fh)
            proc = self._run_sf(["api", "request", "rest", "--file", spec_path])
        finally:
            try:
                os.remove(spec_path)
            except OSError:
                pass
        return _extract_json(proc.stdout)

    def query(self, soql: str) -> list[dict]:
        records: list[dict] = []
        path = f"/services/data/v{self.api_version}/query/?q=" + urllib.parse.quote(soql, safe="")
        while path:
            data = self._rest_get(path)
            if isinstance(data, list):  # Salesforce のエラー応答（配列）
                msg = "; ".join(
                    str(e.get("message", e)) for e in data if isinstance(e, dict)
                ) or str(data)
                raise SalesforceError(f"クエリ失敗: {msg}")
            records.extend(data.get("records", []))
            path = data.get("nextRecordsUrl")  # 相対パス。そのまま次リクエストへ
        return records

    def download_blob(self, content_version_id: str, dest_path: str) -> str:
        path = f"/services/data/v{self.api_version}/sobjects/ContentVersion/{content_version_id}/VersionData"
        # VersionData は特殊文字を含まない安全なパス。--stream-to-file でバイナリ保存
        self._run_sf(["api", "request", "rest", path, "--stream-to-file", os.path.abspath(dest_path)])
        return dest_path


# ---------------------------------------------------------------------------
# バックエンド2: アクセストークン + requests（段階2 OAuth 用）
# ---------------------------------------------------------------------------
class TokenConnection:
    """OAuth などで取得したアクセストークンで直接 REST を叩く。

    on_refresh に「新しいアクセストークンを返す関数」を渡すと、401（期限切れ）を
    検知したときに一度だけ自動でリフレッシュして再試行する。
    """

    def __init__(self, instance_url: str, access_token: str, api_version: str,
                 on_refresh=None):
        self.instance_url = instance_url.rstrip("/")
        self.access_token = access_token
        self.api_version = str(api_version)
        self.on_refresh = on_refresh

    @property
    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self.access_token}"}

    def _data_url(self, path: str) -> str:
        return f"{self.instance_url}/services/data/v{self.api_version}/{path}"

    def _try_refresh(self) -> bool:
        """トークンを更新できたら True。"""
        if not self.on_refresh:
            return False
        new_token = self.on_refresh()
        if not new_token:
            return False
        self.access_token = new_token
        return True

    def _get(self, url: str, **kwargs):
        """401 なら一度だけリフレッシュして再試行する GET。"""
        resp = requests.get(url, headers=self._headers, **kwargs)
        if resp.status_code == 401 and self._try_refresh():
            resp.close()
            resp = requests.get(url, headers=self._headers, **kwargs)
        return resp

    def query(self, soql: str) -> list[dict]:
        records: list[dict] = []
        url = self._data_url("query/") + "?q=" + urllib.parse.quote(soql, safe="")
        while url:
            resp = self._get(url, timeout=60)
            if not resp.ok:
                raise SalesforceError(f"クエリ失敗 ({resp.status_code}): {resp.text[:300]}")
            data = resp.json()
            records.extend(data.get("records", []))
            next_url = data.get("nextRecordsUrl")
            url = f"{self.instance_url}{next_url}" if next_url else None
        return records

    def download_blob(self, content_version_id: str, dest_path: str) -> str:
        url = self._data_url(f"sobjects/ContentVersion/{content_version_id}/VersionData")
        with self._get(url, stream=True, timeout=300) as resp:
            if not resp.ok:
                raise SalesforceError(
                    f"ファイル取得失敗 ({resp.status_code}) id={content_version_id}: {resp.text[:200]}"
                )
            with open(dest_path, "wb") as fh:
                for chunk in resp.iter_content(chunk_size=8192):
                    if chunk:
                        fh.write(chunk)
        return dest_path


# ---------------------------------------------------------------------------
# 入り口
# ---------------------------------------------------------------------------
def get_connection(config: dict, oauth_session: dict | None = None, on_refresh=None):
    """認証の唯一の入り口。

    oauth_session（画面から接続したOAuthトークン）があればそれを最優先で使い、
    無ければ config の salesforce.auth_mode に従う（既定は sf CLI パススルー）。
    """
    sf_cfg = config["salesforce"]
    api_version = sf_cfg.get("api_version", "60.0")

    if oauth_session and oauth_session.get("access_token"):
        return TokenConnection(
            oauth_session["instance_url"],
            oauth_session["access_token"],
            api_version,
            on_refresh=on_refresh,
        )
    auth_mode = sf_cfg.get("auth_mode", "cli")

    if auth_mode == "cli":
        return SfCliConnection(sf_cfg.get("cli_target_org"), api_version)

    if auth_mode in ("token", "oauth"):
        token = sf_cfg.get("access_token")
        instance_url = sf_cfg.get("instance_url")
        if not token or not instance_url:
            raise SalesforceError(
                "Salesforceに接続されていません。画面右上の「Salesforceに接続」から"
                "ログインしてください。"
            )
        return TokenConnection(instance_url, token, api_version)

    raise SalesforceError(f"未知の auth_mode: {auth_mode}")
