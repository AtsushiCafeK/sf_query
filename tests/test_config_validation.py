"""設定ファイルの検証テスト。

このツールは「設定を変えて使う」ことが前提なので、設定を書き間違えたときに
・500エラーで止まる
・エラーも出ずに黙って機能が消える
のどちらにもならないことを守る。
"""

from __future__ import annotations

import pytest

from query_builder import validate_config, check_config_warnings


def test_配布している設定例はそのまま妥当(config):
    """config.example.yaml をコピーした利用者が、すぐ使えること。"""
    assert validate_config(config) == []


def test_ringiセクションが無い場合():
    problems = validate_config({"salesforce": {}})
    assert problems and "ringi" in problems[0]


def test_キー名の綴り誤りを検出する(config):
    """filters を filter と書くと、従来はエラーも出ずに検索欄が消えていた。"""
    config["ringi"]["filter"] = config["ringi"].pop("filters")
    problems = validate_config(config)
    assert any("filter" in p and "綴り間違い" in p for p in problems)
    assert any("filters" in p for p in problems)


def test_フィルタ内のキー名の綴り誤りを検出する(config):
    config["ringi"]["filters"][0]["labl"] = "レコードの種類"
    problems = validate_config(config)
    assert any("labl" in p for p in problems)


def test_fieldsのtitle欠落を検出する(config):
    """title が無いとダウンロード時に KeyError で 500 になっていた。"""
    config["ringi"]["fields"]["titel"] = config["ringi"]["fields"].pop("title")
    problems = validate_config(config)
    assert any("fields.title" in p for p in problems)


def test_fieldsの項目名が不正なら検出する(config):
    config["ringi"]["fields"]["title"] = "Name; DROP"
    problems = validate_config(config)
    assert any("fields.title" in p for p in problems)


def test_selectにoptionsが無ければ検出する(config):
    config["ringi"]["filters"][0].pop("options")
    problems = validate_config(config)
    assert any("options" in p for p in problems)


def test_フィルタ名の重複を検出する(config):
    dup = dict(config["ringi"]["filters"][0])
    config["ringi"]["filters"].append(dup)
    problems = validate_config(config)
    assert any("重複" in p for p in problems)


def test_object_api_nameの欠落を検出する(config):
    config["ringi"].pop("object_api_name")
    problems = validate_config(config)
    assert any("object_api_name" in p for p in problems)


@pytest.mark.parametrize("value", ["file", "attachments", "salesforce"])
def test_attachment_typeの誤りを検出する(config, value):
    config["ringi"]["attachment_type"] = value
    problems = validate_config(config)
    assert any("attachment_type" in p for p in problems)


@pytest.mark.parametrize("value", ["files", "attachment"])
def test_attachment_typeの正しい値は通る(config, value):
    config["ringi"]["attachment_type"] = value
    assert validate_config(config) == []


# --- 警告（動作は止まらないが意図しない結果になりうるもの） -----------------
def test_件名がcolumnsに無ければ警告する(config):
    """フォルダ名と一覧表の件名が空になることを、黙って進めずに知らせる。"""
    config["ringi"]["columns"] = ["Status__c", "ApplicationDate__c"]
    assert validate_config(config) == []          # 動作自体は止めない
    warnings = check_config_warnings(config)
    assert any("件名" in w for w in warnings)


def test_旧Attachment形式は未検証である旨を警告する(config):
    config["ringi"]["attachment_type"] = "attachment"
    warnings = check_config_warnings(config)
    assert any("検証" in w for w in warnings)


def test_正常な設定では警告が出ない(config):
    assert check_config_warnings(config) == []
