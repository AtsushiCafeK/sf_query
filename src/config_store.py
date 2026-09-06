"""config.yaml の読み書き・バックアップ・復元。

設定画面から保存するときも、手で書いた**日本語コメントを残す**ため
ruamel.yaml のラウンドトリップ方式で読み書きする。

保存の流れ:
  1. 変更後の内容を検証する（validate_config）
  2. 問題があれば**書き込まない**（壊れた設定がファイルに残らないようにする）
  3. 現在のファイルをバックアップしてから書き込む
"""

from __future__ import annotations

import os
import shutil
from datetime import datetime

from ruamel.yaml import YAML

from query_builder import validate_config

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_PATH = os.path.join(BASE_DIR, "config", "config.yaml")
EXAMPLE_PATH = os.path.join(BASE_DIR, "config", "config.example.yaml")
BACKUP_DIR = os.path.join(BASE_DIR, "config", "backups")

# 保持するバックアップの世代数（古いものから消す）
MAX_BACKUPS = 20


class ConfigSaveError(RuntimeError):
    """検証に通らないなど、保存できないときに送出する。"""


def _yaml() -> YAML:
    y = YAML()
    y.preserve_quotes = True
    y.width = 4096          # 長い行を勝手に折り返さない
    y.indent(mapping=2, sequence=4, offset=2)
    return y


def load_roundtrip(path: str = CONFIG_PATH):
    """コメントを保持したまま読み込む（保存し直す用）。"""
    with open(path, encoding="utf-8") as fh:
        return _yaml().load(fh)


def list_backups() -> list[dict]:
    """新しい順のバックアップ一覧。"""
    if not os.path.isdir(BACKUP_DIR):
        return []
    items = []
    for name in os.listdir(BACKUP_DIR):
        if not name.endswith(".yaml"):
            continue
        full = os.path.join(BACKUP_DIR, name)
        stat = os.stat(full)
        items.append({
            "name": name,
            "path": full,
            "saved_at": datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
            "size": stat.st_size,
        })
    return sorted(items, key=lambda i: i["name"], reverse=True)


def _prune_backups() -> None:
    for old in list_backups()[MAX_BACKUPS:]:
        try:
            os.remove(old["path"])
        except OSError:
            pass


def make_backup(label: str = "") -> str | None:
    """現在の config.yaml を控えておく。ファイルが無ければ何もしない。"""
    if not os.path.exists(CONFIG_PATH):
        return None
    os.makedirs(BACKUP_DIR, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    suffix = f"_{label}" if label else ""
    dest = os.path.join(BACKUP_DIR, f"config_{stamp}{suffix}.yaml")
    shutil.copy2(CONFIG_PATH, dest)
    _prune_backups()
    return dest


def save(data, *, skip_validation: bool = False) -> dict:
    """設定を検証し、バックアップを取ってから書き込む。

    data は load_roundtrip() で読んだオブジェクトを書き換えたもの。
    戻り値は {"backup": バックアップのパス}。
    """
    if not skip_validation:
        problems = validate_config(data)
        if problems:
            # 壊れた設定を書き込まない。原因はそのまま画面に出す。
            raise ConfigSaveError("\n".join(problems))

    backup = make_backup()
    tmp_path = CONFIG_PATH + ".tmp"
    with open(tmp_path, "w", encoding="utf-8", newline="\n") as fh:
        _yaml().dump(data, fh)
    os.replace(tmp_path, CONFIG_PATH)  # 書き込み途中で壊れないよう原子的に置換
    return {"backup": backup}


def restore(backup_name: str) -> dict:
    """バックアップから復元する。復元前の状態も控えておく。"""
    safe = os.path.basename(backup_name)  # ディレクトリ移動を許さない
    src = os.path.join(BACKUP_DIR, safe)
    if not os.path.isfile(src):
        raise ConfigSaveError(f"バックアップが見つかりません: {safe}")

    before = make_backup(label="beforerestore")
    shutil.copy2(src, CONFIG_PATH)
    return {"restored_from": safe, "backup": before}
