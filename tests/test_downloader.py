"""添付ファイルの取得・命名・一覧表CSVのテスト。

印刷作業のために「一覧の並び順 = ファイル名順」であることが要なので、
そこを重点的に守る。
"""

from __future__ import annotations

import csv
import io
import os
import zipfile

import downloader
from conftest import FakeConn


def _run(conn, config, records, tmp_path, **overrides):
    config["download"] = {
        "output_dir": str(tmp_path), "zip": False, "manifest": True,
        "group_by_record": True, **overrides,
    }
    return downloader.download_for_records(conn, config, records)


def _read_manifest(path):
    with io.open(path, encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh))


# --- 連番（印刷順の担保） ---------------------------------------------------
def test_フォルダ名が一覧順に並ぶ(conn, config, records, tmp_path):
    _run(conn, config, records, tmp_path)
    dirs = sorted(d for d in os.listdir(tmp_path) if os.path.isdir(tmp_path / d))
    # 名前順に並べたとき、一覧の順序（SSD→HDD）と一致する
    assert dirs[0].startswith("001_SSD購入")
    assert dirs[1].startswith("002_HDD購入")
    # 添付が無い003はフォルダが作られない（連番は欠番になり行番号との対応は保たれる）
    assert not any(d.startswith("003") for d in dirs)


def test_平置きモードはファイル名だけで一覧順に並ぶ(conn, config, records, tmp_path):
    _run(conn, config, records, tmp_path, group_by_record=False)
    files = sorted(f for f in os.listdir(tmp_path) if f.endswith((".pdf", ".xlsx")))
    assert files == [
        "001-1_SSD購入_請求書.pdf",
        "002-1_HDD購入_見積書.pdf",
        "002-2_HDD購入_内訳.xlsx",
    ]


def test_連番はゼロ埋めされる(config, tmp_path):
    """1_,2_,10_ だと名前順で 10 が 2 より前に来てしまうため。"""
    many = [{"Id": f"a{i:03d}", "Name": f"稟議{i}"} for i in range(1, 13)]
    conn = FakeConn({r["Id"]: [("書類", "pdf")] for r in many})
    summary = _run(conn, config, many, tmp_path, group_by_record=False)
    names = sorted(os.path.basename(p) for p in summary["files"])
    assert names[0].startswith("001-1_")
    assert names[-1].startswith("012-1_")


def test_同名ファイルは連番で衝突回避(config, tmp_path):
    conn = FakeConn({"a001": [("請求書", "pdf"), ("請求書", "pdf")]})
    rec = [{"Id": "a001", "Name": "重複テスト"}]
    summary = _run(conn, config, rec, tmp_path)
    names = sorted(os.path.basename(p) for p in summary["files"])
    assert names == ["請求書(1).pdf", "請求書.pdf"]


# --- 集計 -------------------------------------------------------------------
def test_サマリの件数(conn, config, records, tmp_path):
    s = _run(conn, config, records, tmp_path)
    assert s["count"] == 3                    # 1 + 2 + 0
    assert s["records_total"] == 3
    assert s["records_with_attachment"] == 2  # a003 は添付なし


def test_添付件数の集計(conn, config):
    counts = downloader.attachment_counts(conn, config, ["a001", "a002", "a003"])
    assert counts.get("a001") == 1
    assert counts.get("a002") == 2
    assert counts.get("a003", 0) == 0


def test_対象が空でも落ちない(conn, config):
    """SOQL の IN () は構文エラーになるため、問い合わせないこと。"""
    assert downloader.attachment_counts(conn, config, []) == {}
    assert conn.queries == []


# --- 一覧表CSV --------------------------------------------------------------
def test_一覧表は一覧順で添付なしも残す(conn, config, records, tmp_path):
    s = _run(conn, config, records, tmp_path)
    rows = _read_manifest(s["manifest"])
    assert [r["連番"] for r in rows] == ["001", "002", "002", "003"]
    assert [r["件名"] for r in rows] == ["SSD購入", "HDD購入", "HDD購入", "添付なし稟議"]
    assert rows[-1]["備考"] == "添付なし"      # 印刷漏れと区別できるように残す
    assert rows[1]["枝番"] == "1" and rows[2]["枝番"] == "2"


def test_一覧表はExcelで開けるようBOM付き(conn, config, records, tmp_path):
    s = _run(conn, config, records, tmp_path)
    with open(s["manifest"], "rb") as fh:
        assert fh.read(3) == b"\xef\xbb\xbf"


def test_ZIPには一覧表も含まれる(conn, config, records, tmp_path):
    s = _run(conn, config, records, tmp_path, zip=True)
    with zipfile.ZipFile(s["zip"]) as zf:
        names = zf.namelist()
    assert any(n.endswith(".csv") for n in names)
    assert sum(1 for n in names if n.endswith((".pdf", ".xlsx"))) == 3


# --- 添付形式の切り替え -----------------------------------------------------
def test_既定はSalesforceFiles経由で取得する(conn, config, records, tmp_path):
    _run(conn, config, records, tmp_path)
    assert all(sobj == "ContentVersion" for sobj, _, _ in conn.downloads)
    assert all(field == "VersionData" for _, _, field in conn.downloads)


def test_attachment指定なら旧Attachment経由で取得する(config, records, tmp_path):
    """設定を変えたのに無視される、という状態にしないための回帰テスト。"""
    conn = FakeConn({"a001": [("旧請求書", "pdf")]}, legacy=True)
    config["ringi"]["attachment_type"] = "attachment"
    s = _run(conn, config, records[:1], tmp_path)
    assert s["count"] == 1
    assert conn.downloads == [("Attachment", "00Pa0010", "Body")]
    assert any("FROM Attachment" in q for q in conn.queries)
    assert not any("ContentDocumentLink" in q for q in conn.queries)


def test_旧Attachmentは名前から拡張子を分解する(config, tmp_path):
    """Attachment.Name は拡張子込みなので、二重に付かないこと。"""
    conn = FakeConn({"a001": [("請求書", "pdf")]}, legacy=True)
    config["ringi"]["attachment_type"] = "attachment"
    s = _run(conn, config, [{"Id": "a001", "Name": "稟議"}], tmp_path)
    assert os.path.basename(s["files"][0]) == "請求書.pdf"


# --- 設定変更に対する頑健さ -------------------------------------------------
def test_件名がクエリ結果に無くてもダウンロードは通る(conn, config, tmp_path):
    """columns から件名を外した場合。フォルダ名はIDになるが失敗はしない。"""
    recs = [{"Id": "a001", "Status__c": "Done"}]
    s = _run(conn, config, recs, tmp_path)
    assert s["count"] == 1
    assert os.path.basename(os.path.dirname(s["files"][0])) == "001_a001_a001"


def test_ファイル名に使えない文字は置き換えられる(config, tmp_path):
    conn = FakeConn({"a001": [("請求書:2026/08", "pdf")]})
    s = _run(conn, config, [{"Id": "a001", "Name": "テスト"}], tmp_path)
    name = os.path.basename(s["files"][0])
    assert not any(ch in name for ch in ':/\\*?"<>|')
