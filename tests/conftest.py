"""テスト共通の準備。

Salesforce に接続せずに全機能を検証できるよう、偽の接続（FakeConn）を使う。
設定は **配布している config.example.yaml** を土台にする。こうすると
「配布物の設定がそのまま動くか」も自動で確かめられる。
"""

from __future__ import annotations

import copy
import os

import pytest
import yaml

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXAMPLE_CONFIG = os.path.join(BASE_DIR, "config", "config.example.yaml")


@pytest.fixture
def config():
    """配布用 config.example.yaml を読み込んだ設定（テストごとに独立）。"""
    with open(EXAMPLE_CONFIG, encoding="utf-8") as fh:
        return copy.deepcopy(yaml.safe_load(fh))


class FakeConn:
    """Salesforce の代わりに決め打ちの応答を返す偽の接続。

    query() に渡された SOQL を self.queries に記録するので、
    「どんなクエリを投げたか」もテストできる。
    """

    def __init__(self, files_per_record: dict | None = None, legacy: bool = False):
        # record_id -> [(title, ext), ...]
        self.files = files_per_record if files_per_record is not None else {}
        self.legacy = legacy
        self.queries: list[str] = []
        self.downloads: list[tuple] = []

    def query(self, soql: str) -> list[dict]:
        self.queries.append(soql)

        if "FROM Attachment" in soql:
            rows = []
            for rid, items in self.files.items():
                if f"'{rid}'" not in soql:
                    continue
                for i, (title, ext) in enumerate(items):
                    name = f"{title}.{ext}" if ext else title
                    rows.append({"Id": f"00P{rid}{i}", "Name": name, "ParentId": rid})
            return rows

        if "ContentDocumentLink" in soql:
            rows = []
            for rid, items in self.files.items():
                if f"'{rid}'" not in soql:
                    continue
                for i in range(len(items)):
                    rows.append({"LinkedEntityId": rid, "ContentDocumentId": f"069{rid}{i}"})
            return rows

        if "ContentVersion" in soql:
            rows = []
            for rid, items in self.files.items():
                for i, (title, ext) in enumerate(items):
                    doc_id = f"069{rid}{i}"
                    if f"'{doc_id}'" not in soql:
                        continue
                    rows.append({"Id": f"068{rid}{i}", "Title": title,
                                 "FileExtension": ext, "ContentDocumentId": doc_id})
            return rows

        return []

    def download_blob(self, sobject: str, record_id: str, blob_field: str, dest_path: str) -> str:
        self.downloads.append((sobject, record_id, blob_field))
        with open(dest_path, "wb") as fh:
            fh.write(b"%PDF-1.4 dummy")
        return dest_path


@pytest.fixture
def conn():
    """添付を持つ標準的な偽接続。

    a001: 1件 / a002: 2件（枝番の検証用） / a003: 0件（添付なしの検証用）
    """
    return FakeConn({
        "a001": [("請求書", "pdf")],
        "a002": [("見積書", "pdf"), ("内訳", "xlsx")],
        "a003": [],
    })


@pytest.fixture
def records():
    """画面の一覧と同じ並び順で渡されるレコード（連番の検証用）。"""
    return [
        {"Id": "a001", "Name": "SSD購入", "Status__c": "Done", "ApplicationDate__c": "2026-08-11"},
        {"Id": "a002", "Name": "HDD購入", "Status__c": "Done", "ApplicationDate__c": "2026-08-08"},
        {"Id": "a003", "Name": "添付なし稟議", "Status__c": "Submitted", "ApplicationDate__c": "2026-07-01"},
    ]
