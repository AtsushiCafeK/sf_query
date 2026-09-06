"""設定の保存・バックアップ・復元のテスト。

設定画面から保存する以上、「壊れた設定を書き込まない」「戻せる」の2点が
守られていることが要になる。
"""

from __future__ import annotations

import io
import os

import pytest

import config_store
from config_store import ConfigSaveError

VALID = """\
# 説明コメント
salesforce:
  login_url: "https://login.salesforce.com"
  api_version: "60.0"
  auth_mode: "cli"

ringi:
  object_api_name: "Ringi__c"
  fields:
    title: "Name"
  columns:
    - "Name"
  order_by: "Name ASC"
  filters:
    - name: status
      label: "ステータス"
      type: select
      field: "Status__c"
      operator: eq
      options:
        - { value: "Done", label: "完了" }
  attachment_type: "files"

download:
  output_dir: "./downloads"
  zip: true
  group_by_record: true
  manifest: true
"""


@pytest.fixture
def store(tmp_path, monkeypatch):
    """本物の config を触らないよう、一時ディレクトリに差し替える。"""
    cfg = tmp_path / "config.yaml"
    cfg.write_text(VALID, encoding="utf-8")
    monkeypatch.setattr(config_store, "CONFIG_PATH", str(cfg))
    monkeypatch.setattr(config_store, "BACKUP_DIR", str(tmp_path / "backups"))
    return cfg


def _read(path) -> str:
    return io.open(path, encoding="utf-8").read()


def test_保存で内容が書き換わりコメントも残る(store):
    data = config_store.load_roundtrip(str(store))
    data["ringi"]["object_api_name"] = "Other__c"
    config_store.save(data)
    text = _read(store)
    assert "Other__c" in text
    assert "# 説明コメント" in text


def test_保存のたびにバックアップが作られる(store):
    data = config_store.load_roundtrip(str(store))
    result = config_store.save(data)
    assert result["backup"] and os.path.exists(result["backup"])
    assert len(config_store.list_backups()) == 1


def test_壊れた設定は書き込まれない(store):
    """検証に落ちる内容は、ファイルに一切残さない。"""
    before = _read(store)
    data = config_store.load_roundtrip(str(store))
    del data["ringi"]["fields"]["title"]      # 必須の割り当てを外す
    with pytest.raises(ConfigSaveError) as exc:
        config_store.save(data)
    assert "title" in str(exc.value)
    assert _read(store) == before             # 元のまま


def test_バックアップから復元できる(store):
    data = config_store.load_roundtrip(str(store))
    data["ringi"]["object_api_name"] = "Changed__c"
    config_store.save(data)
    assert "Changed__c" in _read(store)

    oldest = config_store.list_backups()[-1]["name"]
    config_store.restore(oldest)
    assert "Ringi__c" in _read(store)          # 元に戻った


def test_復元の前にも控えが作られる(store):
    data = config_store.load_roundtrip(str(store))
    data["ringi"]["object_api_name"] = "Changed__c"
    config_store.save(data)

    target = config_store.list_backups()[-1]["name"]
    result = config_store.restore(target)
    assert result["backup"]                     # 戻す直前の状態も残る
    assert any("beforerestore" in b["name"] for b in config_store.list_backups())


def test_存在しないバックアップは復元しない(store):
    with pytest.raises(ConfigSaveError):
        config_store.restore("config_does_not_exist.yaml")


def test_バックアップ名でディレクトリを移動できない(store):
    """`../` を含む指定で別の場所を読ませない。"""
    with pytest.raises(ConfigSaveError):
        config_store.restore("../../config.example.yaml")


def test_バックアップは一定数で打ち切られる(store, monkeypatch):
    monkeypatch.setattr(config_store, "MAX_BACKUPS", 3)
    data = config_store.load_roundtrip(str(store))
    for i in range(5):
        config_store.make_backup(label=f"n{i}")
    assert len(config_store.list_backups()) <= 3
