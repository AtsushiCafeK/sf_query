"""OAuth 2.0 認可コードフロー + PKCE（Salesforce）。

複数ユーザー対応のための認証。各ユーザーが自分のSalesforceアカウントでブラウザログインし、
アプリはパスワードを一切扱わない。SOAP のユーザー名＋パスワード方式は使わない。

流れ:
  1. build_authorize_url() … PKCE の code_verifier / state を生成し、認可画面URLを作る
  2. ユーザーがSalesforceでログイン・許可 → redirect_uri に code が返る
  3. exchange_code()      … code + code_verifier をトークンに交換
  4. refresh_access_token() … アクセストークン期限切れ時にリフレッシュ
"""

from __future__ import annotations

import base64
import hashlib
import secrets
import urllib.parse

import requests


class OAuthError(RuntimeError):
    pass


def _b64url(raw: bytes) -> str:
    """パディング無しの base64url エンコード（RFC 7636）。"""
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def generate_pkce() -> tuple[str, str]:
    """(code_verifier, code_challenge) を生成する。S256 方式。"""
    verifier = _b64url(secrets.token_bytes(64))
    challenge = _b64url(hashlib.sha256(verifier.encode("ascii")).digest())
    return verifier, challenge


def generate_state() -> str:
    """CSRF対策の state。"""
    return secrets.token_urlsafe(24)


def _oauth_cfg(config: dict) -> dict:
    sf = config["salesforce"]
    oauth = dict(sf.get("oauth") or {})
    oauth["login_url"] = sf.get("login_url", "https://login.salesforce.com").rstrip("/")
    if not oauth.get("client_id") or oauth["client_id"].startswith("<"):
        raise OAuthError(
            "接続アプリの Consumer Key が未設定です。"
            "config/config.yaml の salesforce.oauth.client_id を設定してください。"
        )
    return oauth


def build_authorize_url(config: dict, code_challenge: str, state: str) -> str:
    """Salesforce の認可画面URLを組み立てる。"""
    oauth = _oauth_cfg(config)
    params = {
        "response_type": "code",
        "client_id": oauth["client_id"],
        "redirect_uri": oauth["redirect_uri"],
        "state": state,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
        "scope": " ".join(oauth.get("scopes") or ["api", "refresh_token"]),
    }
    return f"{oauth['login_url']}/services/oauth2/authorize?" + urllib.parse.urlencode(params)


def _token_request(config: dict, data: dict) -> dict:
    oauth = _oauth_cfg(config)
    if oauth.get("client_secret"):  # 公開クライアント(PKCE)ではsecret不要
        data["client_secret"] = oauth["client_secret"]
    data["client_id"] = oauth["client_id"]

    resp = requests.post(
        f"{oauth['login_url']}/services/oauth2/token",
        data=data,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=60,
    )
    payload = {}
    try:
        payload = resp.json()
    except ValueError:
        pass
    if not resp.ok:
        detail = payload.get("error_description") or payload.get("error") or resp.text[:200]
        raise OAuthError(f"トークン取得に失敗しました: {detail}")
    return payload


def exchange_code(config: dict, code: str, code_verifier: str) -> dict:
    """認可コードをアクセストークンに交換する。"""
    oauth = _oauth_cfg(config)
    return _token_request(config, {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": oauth["redirect_uri"],
        "code_verifier": code_verifier,
    })


def refresh_access_token(config: dict, refresh_token: str) -> dict:
    """リフレッシュトークンで新しいアクセストークンを取得する。"""
    return _token_request(config, {
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
    })


def revoke_token(config: dict, token: str) -> None:
    """トークンを失効させる（切断時）。失敗しても致命的ではないため例外は投げない。"""
    oauth = _oauth_cfg(config)
    try:
        requests.post(
            f"{oauth['login_url']}/services/oauth2/revoke",
            data={"token": token},
            timeout=30,
        )
    except requests.RequestException:
        pass


def fetch_userinfo(instance_url: str, access_token: str) -> dict:
    """接続中のユーザー情報（表示用）。取得できなければ空dictを返す。"""
    try:
        resp = requests.get(
            f"{instance_url.rstrip('/')}/services/oauth2/userinfo",
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=30,
        )
        if resp.ok:
            return resp.json()
    except (requests.RequestException, ValueError):
        pass
    return {}
