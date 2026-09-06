"""設定画面の入力 → config への反映のテスト。

設定画面の役目は「YAMLを書けない人でも安全に設定できること」なので、
・正しい入力が正しく反映されること
・不正な入力を受け付けないこと（保存前に弾く）
・手書きのコメントを壊さないこと
を守る。
"""

from __future__ import annotations

import io

import pytest
from ruamel.yaml import YAML

import settings_form
from settings_form import FormError
from query_builder import validate_config, build_ringi_query

SAMPLE = """\
# 先頭の説明コメント
salesforce:
  login_url: "https://login.salesforce.com"
  api_version: "60.0"
  auth_mode: "cli"
  oauth:
    client_id: "EXISTING_KEY"
    client_secret: "EXISTING_SECRET"
    redirect_uri: "http://localhost:8080/oauth/callback"

ringi:
  object_api_name: "Ringi__c"
  fields:
    title: "Name"
    status: "Status__c"
    application_date: "ApplicationDate__c"
  columns:
    - "Name"
    - "Status__c"

  # 並び順の説明コメント
  order_by: "ApplicationDate__c DESC NULLS LAST"

  # フィルタの説明コメント（保持されること）
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


class FakeForm:
    """Flask の request.form を模した最小の実装（getlist を持つ）。"""

    def __init__(self, pairs):
        self._pairs = list(pairs)

    def get(self, key, default=None):
        for k, v in self._pairs:
            if k == key:
                return v
        return default

    def getlist(self, key):
        return [v for k, v in self._pairs if k == key]


def _load():
    y = YAML()
    y.preserve_quotes = True
    return y.load(io.StringIO(SAMPLE))


def _dump(data) -> str:
    y = YAML()
    y.preserve_quotes = True
    y.width = 4096
    buf = io.StringIO()
    y.dump(data, buf)
    return buf.getvalue()


def _base_pairs(**over):
    pairs = [
        ("login_url", "https://login.salesforce.com"),
        ("auth_mode", "cli"),
        ("api_version", "60.0"),
        ("redirect_uri", "http://localhost:8080/oauth/callback"),
        ("client_id", ""), ("client_secret", ""),
        ("object_api_name", "Ringi__c"),
        ("field_title", "Name"),
        ("field_status", "Status__c"),
        ("field_application_date", "ApplicationDate__c"),
        ("columns", "Name"), ("columns", "Status__c"),
        ("order_by_field", "ApplicationDate__c"), ("order_by_dir", "DESC"),
        ("attachment_type", "files"),
        ("output_dir", "./downloads"), ("zip", "on"),
        ("group_by_record", "on"), ("manifest", "on"),
        ("filter_index", "0"),
        ("f_0_name", "status"), ("f_0_label", "ステータス"), ("f_0_type", "select"),
        ("f_0_field", "Status__c"), ("f_0_operator", "eq"),
        ("f_0_opt_value", "Done"), ("f_0_opt_label", "完了"),
    ]
    for key, value in over.items():
        pairs = [(k, v) for k, v in pairs if k != key]
        if value is not None:
            pairs.append((key, value))
    return pairs


# --- 反映されること ---------------------------------------------------------
def test_保存した設定がそのまま検証を通る():
    data = _load()
    settings_form.apply_all(data, FakeForm(_base_pairs()))
    assert validate_config(data) == []


def test_フィルタを追加できる():
    pairs = _base_pairs() + [
        ("filter_index", "1"),
        ("f_1_name", "amount_min"), ("f_1_label", "金額（以上）"),
        ("f_1_type", "number"), ("f_1_field", "Amount__c"), ("f_1_operator", "gte"),
    ]
    data = _load()
    settings_form.apply_all(data, FakeForm(pairs))
    names = [f["name"] for f in data["ringi"]["filters"]]
    assert names == ["status", "amount_min"]
    assert "Amount__c >= 10" in build_ringi_query(data, {"amount_min": "10"})


def test_チェックでフィルタを削除できる():
    pairs = _base_pairs() + [("f_0_delete", "on")]
    data = _load()
    settings_form.apply_all(data, FakeForm(pairs))
    assert list(data["ringi"]["filters"]) == []


def test_選択肢のラベルだけ日本語に変えられる():
    pairs = _base_pairs()
    pairs = [(k, "完了（承認済）") if k == "f_0_opt_label" else (k, v) for k, v in pairs]
    data = _load()
    settings_form.apply_all(data, FakeForm(pairs))
    opt = data["ringi"]["filters"][0]["options"][0]
    assert opt["value"] == "Done"          # Salesforceの保存値は変えない
    assert opt["label"] == "完了（承認済）"


def test_ダウンロード設定のチェックが反映される():
    data = _load()
    settings_form.apply_all(data, FakeForm(_base_pairs(zip=None, manifest=None)))
    assert data["download"]["zip"] is False
    assert data["download"]["manifest"] is False
    assert data["download"]["group_by_record"] is True


def test_並び順は項目と向きから組み立てられる():
    data = _load()
    settings_form.apply_all(data, FakeForm(_base_pairs(order_by_dir="ASC")))
    assert data["ringi"]["order_by"] == "ApplicationDate__c ASC NULLS LAST"


# --- 鍵の扱い ---------------------------------------------------------------
def test_鍵は空欄なら変更されない():
    """毎回入力させると消し忘れ・打ち間違いが起きるため、空欄は「変更しない」。"""
    data = _load()
    settings_form.apply_all(data, FakeForm(_base_pairs()))
    assert data["salesforce"]["oauth"]["client_id"] == "EXISTING_KEY"
    assert data["salesforce"]["oauth"]["client_secret"] == "EXISTING_SECRET"


def test_鍵を入力すれば更新される():
    data = _load()
    settings_form.apply_all(data, FakeForm(_base_pairs(client_id="NEW_KEY")))
    assert data["salesforce"]["oauth"]["client_id"] == "NEW_KEY"


def test_伏せ字のままなら変更されない():
    data = _load()
    settings_form.apply_all(
        data, FakeForm(_base_pairs(client_id=settings_form.SECRET_PLACEHOLDER)))
    assert data["salesforce"]["oauth"]["client_id"] == "EXISTING_KEY"


# --- 受け付けないこと -------------------------------------------------------
@pytest.mark.parametrize("over, part", [
    ({"f_0_name": "ステータス"}, "英数字"),          # 内部名が日本語
    ({"f_0_name": ""}, "空です"),
    ({"f_0_type": "textarea"}, "型が不正"),
    ({"api_version": "sixty"}, "APIバージョン"),
    ({"login_url": "http://example.com"}, "https"),
    ({"auth_mode": "basic"}, "認証方式"),
    ({"attachment_type": "zip"}, "添付形式"),
    ({"field_title": ""}, "件名"),
])
def test_不正な入力は受け付けない(over, part):
    data = _load()
    with pytest.raises(FormError) as exc:
        settings_form.apply_all(data, FakeForm(_base_pairs(**over)))
    assert part in str(exc.value)


def test_選択肢のないプルダウンは受け付けない():
    pairs = [(k, v) for k, v in _base_pairs() if not k.startswith("f_0_opt")]
    data = _load()
    with pytest.raises(FormError) as exc:
        settings_form.apply_all(data, FakeForm(pairs))
    assert "選択肢" in str(exc.value)


def test_内部名の重複は受け付けない():
    pairs = _base_pairs() + [
        ("filter_index", "1"),
        ("f_1_name", "status"), ("f_1_type", "date"),
        ("f_1_field", "ApplicationDate__c"), ("f_1_operator", "gte"),
    ]
    data = _load()
    with pytest.raises(FormError) as exc:
        settings_form.apply_all(data, FakeForm(pairs))
    assert "重複" in str(exc.value)


# --- コメントを壊さないこと -------------------------------------------------
def test_手書きのコメントが保持される():
    """GUIで保存しても、YAMLに書いた説明が消えないこと。"""
    data = _load()
    settings_form.apply_all(data, FakeForm(_base_pairs()))
    out = _dump(data)
    assert "# 先頭の説明コメント" in out
    assert "# フィルタの説明コメント（保持されること）" in out
    assert "# 並び順の説明コメント" in out


def test_リストを書き換えても後続のコメントが残る():
    """列やフィルタを入れ替えたときに、その直後の説明が消えないこと。"""
    pairs = _base_pairs()
    pairs = [(k, v) for k, v in pairs if k != "columns"]
    pairs += [("columns", "Name"), ("columns", "ApplicationDate__c")]
    data = _load()
    settings_form.apply_all(data, FakeForm(pairs))
    out = _dump(data)
    assert "# 並び順の説明コメント" in out
    assert "ApplicationDate__c" in out


# --- 対象オブジェクトの一覧（探せることを守る） -----------------------------
class FakeSchemaConn:
    """オブジェクト一覧だけ返す偽の接続。"""

    def __init__(self, objects):
        self._objects = objects

    def list_objects(self):
        return self._objects


def test_対象になり得るオブジェクトに印が付く():
    """組織には標準オブジェクトが千件以上あり、そのままでは目的のものを探せない。"""
    import schema
    schema.clear_cache()
    conn = FakeSchemaConn([
        {"name": "Account", "label": "取引先", "custom": False, "queryable": True},
        {"name": "Ringi__c", "label": "稟議", "custom": True, "queryable": True},
        {"name": "teamspirit__Ringi__c", "label": "稟議申請", "custom": True, "queryable": True},
        {"name": "AccountFeed", "label": "取引先フィード", "custom": False, "queryable": True},
        {"name": "NotQueryable", "label": "対象外", "custom": True, "queryable": False},
    ])
    objs = schema.list_objects(conn)
    schema.clear_cache()

    names = [o["name"] for o in objs]
    assert "NotQueryable" not in names          # 検索できないものは出さない
    assert names[0] in ("Ringi__c", "teamspirit__Ringi__c")   # 候補が先頭に来る

    relevant = {o["name"] for o in objs if o["relevant"]}
    assert relevant == {"Ringi__c", "teamspirit__Ringi__c"}
    assert not any(o["relevant"] for o in objs if o["name"].startswith("Account"))


def test_パッケージの名前空間を取り出せる():
    """TeamSpirit のようなインストール済みパッケージを見分けるため。"""
    import schema
    assert schema._namespace("teamspirit__Ringi__c") == "teamspirit"
    assert schema._namespace("Ringi__c") == ""
    assert schema._namespace("Account") == ""
