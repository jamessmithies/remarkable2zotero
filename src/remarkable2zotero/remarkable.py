from __future__ import annotations

import json
import logging
import socket
from pathlib import Path

import paramiko

from remarkable2zotero.models import RemarkableDocument

log = logging.getLogger(__name__)

RM_DATA_DIR = "/home/root/.local/share/remarkable/xochitl/"


def connect(host: str, user: str = "root", password: str = "") -> paramiko.SSHClient:
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        client.connect(host, username=user, password=password, timeout=10)
    except paramiko.AuthenticationException:
        raise ConnectionError(
            "Authentication failed. Check the SSH password in "
            "Settings > General > Help > Copyrights and licenses on your reMarkable."
        )
    except (socket.timeout, paramiko.SSHException, OSError) as e:
        raise ConnectionError(
            f"Cannot reach reMarkable at {host}: {e}\n"
            "Ensure the device is on and connected via USB or WiFi."
        )
    return client


def discover_documents(
    sftp: paramiko.SFTPClient, data_dir: str = RM_DATA_DIR
) -> list[RemarkableDocument]:
    documents = []
    try:
        entries = sftp.listdir(data_dir)
    except FileNotFoundError:
        raise FileNotFoundError(
            f"Data directory not found: {data_dir}\n"
            "This may indicate an incompatible firmware version."
        )

    metadata_files = [e for e in entries if e.endswith(".metadata")]

    for meta_file in metadata_files:
        uuid = meta_file.removesuffix(".metadata")
        try:
            doc = _parse_document(sftp, data_dir, uuid)
            if doc:
                documents.append(doc)
        except Exception as e:
            log.warning("Skipping %s: %s", uuid, e)

    documents.sort(key=lambda d: d.last_modified, reverse=True)
    return documents


def _parse_document(
    sftp: paramiko.SFTPClient, data_dir: str, uuid: str
) -> RemarkableDocument | None:
    meta_path = f"{data_dir}{uuid}.metadata"
    with sftp.open(meta_path) as f:
        metadata = json.loads(f.read())

    if metadata.get("type") != "DocumentType":
        return None
    if metadata.get("parent") == "trash":
        return None

    content_path = f"{data_dir}{uuid}.content"
    try:
        with sftp.open(content_path) as f:
            content = json.loads(f.read())
    except FileNotFoundError:
        return None

    file_type = content.get("fileType", "")
    if file_type not in ("pdf", "epub"):
        return None

    page_uuids = content.get("cPages", {}).get("pages", [])
    if isinstance(page_uuids, list) and page_uuids and isinstance(page_uuids[0], dict):
        page_uuids = [p.get("id", "") for p in page_uuids]

    # Check for .rm annotation files
    rm_dir = f"{data_dir}{uuid}/"
    try:
        rm_files = [f for f in sftp.listdir(rm_dir) if f.endswith(".rm")]
    except FileNotFoundError:
        rm_files = []

    if not rm_files:
        return None

    return RemarkableDocument(
        uuid=uuid,
        visible_name=metadata.get("visibleName", uuid),
        file_type=file_type,
        last_modified=metadata.get("lastModified", 0),
        page_uuids=page_uuids,
    )


def download_document(
    sftp: paramiko.SFTPClient,
    doc: RemarkableDocument,
    cache_dir: Path,
    data_dir: str = RM_DATA_DIR,
) -> RemarkableDocument:
    doc_dir = cache_dir / doc.uuid
    rm_dir = doc_dir / "rm"
    doc_dir.mkdir(parents=True, exist_ok=True)
    rm_dir.mkdir(parents=True, exist_ok=True)

    # Download source file (PDF or EPUB)
    ext = doc.file_type  # "pdf" or "epub"
    remote_file = f"{data_dir}{doc.uuid}.{ext}"
    local_file = doc_dir / f"{doc.visible_name}.{ext}"
    log.info("Downloading %s", doc.visible_name)
    sftp.get(remote_file, str(local_file))

    # Download .rm files
    remote_rm_dir = f"{data_dir}{doc.uuid}/"
    for entry in sftp.listdir(remote_rm_dir):
        if entry.endswith(".rm"):
            sftp.get(f"{remote_rm_dir}{entry}", str(rm_dir / entry))

    doc.local_pdf_path = local_file
    doc.local_rm_dir = rm_dir
    return doc
