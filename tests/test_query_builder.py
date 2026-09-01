"""SOQL の組み立てと、利用者入力の検証（インジェクション対策）のテスト。"""

from __future__ import annotations

import pytest

from query_builder import (
    build_ringi_query,
    validate_raw_soql,
    extract_object_name,
    QueryBuildError,
)


def test_条件なしなら_WHERE_が付かない(config):
    soql = build_ringi_query(config)
    assert "WHERE" not in soql
    assert soql.startswith("SELECT Id, ")
    assert "FROM Ringi__c" in soql


def test_未入力の条件は無視される(config):
    soql = build_ringi_query(config, {"status": "", "date_from": None})
    assert "WHERE" not in soql


def test_選択条件がWHEREになる(config):
    soql = build_ringi_query(config, {"record_type": "Purchase", "status": "Done"})
    assert "RecordType.DeveloperName = 'Purchase'" in soql
    assert "Status__c = 'Done'" in soql
    assert " AND " in soql


def test_日付は範囲条件になりクォートが付かない(config):
    soql = build_ringi_query(config, {"date_from": "2026-08-01", "date_to": "2026-08-31"})
    assert "ApplicationDate__c >= 2026-08-01" in soql
    assert "ApplicationDate__c <= 2026-08-31" in soql
    assert "'2026-08-01'" not in soql  # 日付リテラルはクォート無し


def test_columns_と_order_by_が設定から反映される(config):
    config["ringi"]["columns"] = ["Name", "Amount__c"]
    config["ringi"]["order_by"] = "Name ASC"
    soql = build_ringi_query(config)
    assert soql.startswith("SELECT Id, Name, Amount__c FROM")
    assert soql.endswith("ORDER BY Name ASC")


def test_configにフィルタを足すだけで条件が増える(config):
    """コードを変えずに検索条件を追加できること（この設計の核心）。"""
    config["ringi"]["filters"].append({
        "name": "amount_min", "label": "金額（以上）",
        "type": "number", "field": "Amount__c", "operator": "gte",
    })
    soql = build_ringi_query(config, {"amount_min": "10000"})
    assert "Amount__c >= 10000" in soql


# --- 利用者入力の検証（自由文字列を WHERE に入れない） -----------------------
@pytest.mark.parametrize("filters", [
    {"status": "'; DROP TABLE--"},          # 選択肢外
    {"status": "Done' OR Id!=null--"},      # クォート脱出の試み
    {"record_type": "Unknown"},             # 存在しない選択肢
])
def test_選択肢外の値は拒否される(config, filters):
    with pytest.raises(QueryBuildError):
        build_ringi_query(config, filters)


@pytest.mark.parametrize("value", [
    "2026/08/01",                # 書式違反
    "2026-08-01 OR Id!=null",    # 注入の試み
    "yesterday",
])
def test_不正な日付は拒否される(config, value):
    with pytest.raises(QueryBuildError):
        build_ringi_query(config, {"date_from": value})


@pytest.mark.parametrize("value", ["abc", "1 OR Id!=null", "1;2"])
def test_不正な数値は拒否される(config, value):
    config["ringi"]["filters"].append({
        "name": "amount_min", "label": "金額", "type": "number",
        "field": "Amount__c", "operator": "gte",
    })
    with pytest.raises(QueryBuildError):
        build_ringi_query(config, {"amount_min": value})


# --- 設定側の誤りも SOQL に流し込まない -------------------------------------
def test_不正な項目名を持つ設定は拒否される(config):
    config["ringi"]["filters"][0]["field"] = "Name; DROP TABLE"
    with pytest.raises(QueryBuildError):
        build_ringi_query(config, {"record_type": "Purchase"})


def test_未対応の演算子は拒否される(config):
    config["ringi"]["filters"][0]["operator"] = "like"
    with pytest.raises(QueryBuildError):
        build_ringi_query(config, {"record_type": "Purchase"})


def test_未対応の型は拒否される(config):
    config["ringi"]["filters"][0]["type"] = "textarea"
    with pytest.raises(QueryBuildError):
        build_ringi_query(config, {"record_type": "Purchase"})


def test_不正な並び順は拒否される(config):
    config["ringi"]["order_by"] = "Name; DELETE FROM Ringi__c"
    with pytest.raises(QueryBuildError):
        build_ringi_query(config)


def test_不正なオブジェクト名は拒否される(config):
    config["ringi"]["object_api_name"] = "Ringi__c WHERE Id!=null"
    with pytest.raises(QueryBuildError):
        build_ringi_query(config)


# --- SOQL 直接入力モード ----------------------------------------------------
def test_直接入力は読み取り専用のみ許可():
    ok = validate_raw_soql("SELECT Id, Name FROM Ringi__c")
    assert ok.startswith("SELECT")
    assert extract_object_name(ok) == "Ringi__c"


@pytest.mark.parametrize("soql", [
    "",
    "DELETE FROM Ringi__c",
    "UPDATE Ringi__c SET Name='x'",
    "SELECT Id",                      # FROM が無い
    "SELECT Name FROM Ringi__c",      # Id が無い（添付取得に必要）
])
def test_不正な直接入力SOQLは拒否される(soql):
    with pytest.raises(QueryBuildError):
        validate_raw_soql(soql)
