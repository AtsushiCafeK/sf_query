"""添付ファイル（Salesforce Files）の取得と一括ダウンロード。

稟議レコードId → ContentDocumentLink → 最新 ContentVersion → VersionData(本体) と辿る。
"""

from __future__ import annotations

import os
import re
import zipfile
from datetime import datetime

_IN_BATCH = 200  # SOQL IN 句に入れる Id のバッチサイズ
_UNSAFE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def _chunked(seq: list, n: int):
    for i in range(0, len(seq), n):
        yield seq[i:i + n]


def _id_in_list(ids) -> str:
    return ", ".join("'" + str(i) + "'" for i in ids)


def _safe_name(name: str) -> str:
    cleaned = _UNSAFE.sub("_", str(name)).strip().rstrip(".")
    return cleaned or "file"


def fetch_links(conn, record_ids: list[str]) -> dict[str, list[str]]:
    """record_id -> [ContentDocumentId, ...]"""
    result: dict[str, list[str]] = {}
    if not record_ids:  # IN () は不正なSOQLになるため、空なら問い合わせない
        return result
    for batch in _chunked(list(record_ids), _IN_BATCH):
        soql = (
            "SELECT LinkedEntityId, ContentDocumentId FROM ContentDocumentLink "
            f"WHERE LinkedEntityId IN ({_id_in_list(batch)})"
        )
        for row in conn.query(soql):
            result.setdefault(row["LinkedEntityId"], []).append(row["ContentDocumentId"])
    return result


def fetch_latest_versions(conn, content_document_ids) -> dict[str, dict]:
    """ContentDocumentId -> {id, title, ext}（最新版のみ）"""
    versions: dict[str, dict] = {}
    if not content_document_ids:
        return versions
    for batch in _chunked(list(content_document_ids), _IN_BATCH):
        soql = (
            "SELECT Id, Title, FileExtension, ContentDocumentId FROM ContentVersion "
            f"WHERE IsLatest = true AND ContentDocumentId IN ({_id_in_list(batch)})"
        )
        for row in conn.query(soql):
            versions[row["ContentDocumentId"]] = {
                "id": row["Id"],
                "title": row.get("Title") or row["Id"],
                "ext": row.get("FileExtension") or "",
            }
    return versions


def attachment_counts(conn, record_ids: list[str]) -> dict[str, int]:
    """一覧表示用: record_id -> 添付件数"""
    links = fetch_links(conn, record_ids)
    return {rid: len(docs) for rid, docs in links.items()}


def download_for_records(conn, config: dict, records: list[dict]) -> dict:
    """records（Id と title 項目を含む）の添付を全件DL。結果サマリを返す。"""
    dl_cfg = config.get("download", {})
    out_dir = dl_cfg.get("output_dir", "./downloads")
    group = dl_cfg.get("group_by_record", True)
    make_zip = dl_cfg.get("zip", True)
    title_field = config["ringi"]["fields"]["title"]

    record_ids = [r["Id"] for r in records]
    links = fetch_links(conn, record_ids)
    all_doc_ids = {doc for docs in links.values() for doc in docs}
    versions = fetch_latest_versions(conn, all_doc_ids)

    os.makedirs(out_dir, exist_ok=True)
    downloaded: list[str] = []
    skipped_no_attachment = 0

    for rec in records:
        rid = rec["Id"]
        doc_ids = links.get(rid, [])
        if not doc_ids:
            skipped_no_attachment += 1
            continue

        if group:
            folder = _safe_name(f"{rec.get(title_field) or rid}_{rid}")
            target_dir = os.path.join(out_dir, folder)
        else:
            target_dir = out_dir
        os.makedirs(target_dir, exist_ok=True)

        used_names: dict[str, int] = {}
        for doc_id in doc_ids:
            ver = versions.get(doc_id)
            if not ver:
                continue
            base = _safe_name(ver["title"])
            ext = ver["ext"]
            fname = f"{base}.{ext}" if ext else base
            # 同名衝突は連番で回避
            if fname in used_names:
                used_names[fname] += 1
                stem = f"{base}({used_names[fname]})"
                fname = f"{stem}.{ext}" if ext else stem
            else:
                used_names[fname] = 0
            dest = os.path.join(target_dir, fname)
            conn.download_blob(ver["id"], dest)
            downloaded.append(dest)

    zip_path = None
    if make_zip and downloaded:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        zip_path = os.path.join(out_dir, f"ringi_attachments_{ts}.zip")
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for path in downloaded:
                zf.write(path, os.path.relpath(path, out_dir))

    return {
        "count": len(downloaded),
        "files": downloaded,
        "zip": zip_path,
        "records_total": len(records),
        "records_with_attachment": len(records) - skipped_no_attachment,
        "output_dir": os.path.abspath(out_dir),
    }
