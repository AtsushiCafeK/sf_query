"""添付ファイル（Salesforce Files）の取得と一括ダウンロード。

稟議レコードId → ContentDocumentLink → 最新 ContentVersion → VersionData(本体) と辿る。
"""

from __future__ import annotations

import csv
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


def _seq_width(total: int) -> int:
    """連番の桁数。件数が増えても名前順が崩れないようゼロ埋めする。"""
    return max(3, len(str(total)))


def _trim(name: str, limit: int = 40) -> str:
    """パス長超過を避けるため、ファイル名に使う表示名を切り詰める。"""
    s = _safe_name(name)
    return s if len(s) <= limit else s[:limit].rstrip()


def download_for_records(conn, config: dict, records: list[dict]) -> dict:
    """records（Id と title 項目を含む）の添付を全件DL。結果サマリを返す。

    records は画面の一覧と同じ並び順で渡される前提。その順に 001, 002... と
    連番を振るため、**印刷したときの紙の順番が一覧と一致する**。
    """
    dl_cfg = config.get("download", {})
    out_dir = dl_cfg.get("output_dir", "./downloads")
    group = dl_cfg.get("group_by_record", True)
    make_zip = dl_cfg.get("zip", True)
    make_manifest = dl_cfg.get("manifest", True)

    fields = config["ringi"]["fields"]
    title_field = fields["title"]
    status_field = fields.get("status")
    date_field = fields.get("application_date")

    record_ids = [r["Id"] for r in records]
    links = fetch_links(conn, record_ids)
    all_doc_ids = {doc for docs in links.values() for doc in docs}
    versions = fetch_latest_versions(conn, all_doc_ids)

    os.makedirs(out_dir, exist_ok=True)
    width = _seq_width(len(records))
    downloaded: list[str] = []
    manifest_rows: list[dict] = []
    skipped_no_attachment = 0

    for idx, rec in enumerate(records, start=1):
        rid = rec["Id"]
        seq = str(idx).zfill(width)
        title = rec.get(title_field) or rid
        doc_ids = links.get(rid, [])

        row_base = {
            "連番": seq,
            "件名": rec.get(title_field) or "",
            "申請日": (rec.get(date_field) or "") if date_field else "",
            "ステータス": (rec.get(status_field) or "") if status_field else "",
            "レコードID": rid,
        }

        if not doc_ids:
            skipped_no_attachment += 1
            manifest_rows.append({**row_base, "枝番": "", "ファイル名": "",
                                  "保存パス": "", "備考": "添付なし"})
            continue

        if group:
            # レコードごとのフォルダ。フォルダ名の先頭が連番なので一覧順に並ぶ
            target_dir = os.path.join(out_dir, f"{seq}_{_trim(title)}_{rid}")
            os.makedirs(target_dir, exist_ok=True)
        else:
            target_dir = out_dir  # 平置き（全ファイルを一括選択して印刷しやすい）

        used_names: dict[str, int] = {}
        branch = 0
        for doc_id in doc_ids:
            ver = versions.get(doc_id)
            if not ver:
                continue
            branch += 1
            base = _safe_name(ver["title"])
            ext = ver["ext"]
            # 平置きのときはファイル名自体に連番-枝番を付けて順序を保証する
            stem = base if group else f"{seq}-{branch}_{_trim(title)}_{_trim(base)}"
            fname = f"{stem}.{ext}" if ext else stem

            key = fname.lower()  # 同名衝突は連番で回避
            if key in used_names:
                used_names[key] += 1
                dup = f"{stem}({used_names[key]})"
                fname = f"{dup}.{ext}" if ext else dup
            else:
                used_names[key] = 0

            dest = os.path.join(target_dir, fname)
            conn.download_blob(ver["id"], dest)
            downloaded.append(dest)
            manifest_rows.append({**row_base, "枝番": str(branch), "ファイル名": fname,
                                  "保存パス": os.path.relpath(dest, out_dir), "備考": ""})

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")

    # 印刷物と突き合わせるための一覧表（一覧と同じ並び順）
    manifest_path = None
    if make_manifest and manifest_rows:
        manifest_path = os.path.join(out_dir, f"一覧表_{ts}.csv")
        # Excel で開いたときに日本語が化けないよう BOM 付き UTF-8 で書く
        with open(manifest_path, "w", encoding="utf-8-sig", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=[
                "連番", "枝番", "件名", "申請日", "ステータス",
                "レコードID", "ファイル名", "保存パス", "備考",
            ])
            writer.writeheader()
            writer.writerows(manifest_rows)

    zip_path = None
    if make_zip and downloaded:
        zip_path = os.path.join(out_dir, f"ringi_attachments_{ts}.zip")
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for path in downloaded:
                zf.write(path, os.path.relpath(path, out_dir))
            if manifest_path:
                zf.write(manifest_path, os.path.relpath(manifest_path, out_dir))

    return {
        "count": len(downloaded),
        "files": downloaded,
        "zip": zip_path,
        "manifest": manifest_path,
        "records_total": len(records),
        "records_with_attachment": len(records) - skipped_no_attachment,
        "output_dir": os.path.abspath(out_dir),
    }
