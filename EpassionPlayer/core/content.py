# core/content.py
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

SECURE_DIRNAME = "SecureContent"
CONTENT_DIRNAME = "content"
MANIFEST_NAME = "manifest.enc"

# Defensive limits
MAX_ROOTS = 32
MAX_FILES = 10000
MAX_STR_LEN = 1024
MAX_FILENAME_LEN = 255

def _decrypt_manifest(enc_path: Path, drive_key: bytes) -> dict:
    data = enc_path.read_bytes()
    if len(data) < 12 + 16:
        raise ValueError("manifest too small or corrupt")
    nonce = data[:12]
    ciphertext = data[12:]
    aes = AESGCM(drive_key)
    raw = aes.decrypt(nonce, ciphertext, None)
    try:
        j = json.loads(raw.decode("utf-8"))
    except Exception as e:
        raise ValueError("invalid manifest JSON") from e
    return j

def find_secure_root(start_path: Path) -> Optional[Path]:
    p = Path(start_path).resolve()
    if p.name == SECURE_DIRNAME and (p / MANIFEST_NAME).exists():
        return p
    candidate = p / SECURE_DIRNAME
    if candidate.exists() and (candidate / MANIFEST_NAME).exists():
        return candidate
    parent_candidate = p.parent / SECURE_DIRNAME
    if parent_candidate.exists() and (parent_candidate / MANIFEST_NAME).exists():
        return parent_candidate
    return None

def _is_safe_filename(name: str) -> bool:
    if not name or len(name) > MAX_FILENAME_LEN:
        return False
    if "/" in name or "\\" in name or ".." in name:
        return False
    # allow letters, digits, dash, underscore, dot
    return all((c.isalnum() or c in "-_." ) for c in name)

@dataclass
class FileEntry:
    root_index: int
    root_name: str
    relpath: str
    kind: str
    desc: Dict[str, Any]
    quiz: Optional[List[Dict[str, Any]]] = None

class PackageView:
    def __init__(self, root_path: Path, manifest: dict):
        self.root_path = root_path
        self.package_id: str = manifest.get("package_id", "")[:MAX_STR_LEN]
        self.drive_id: str = manifest.get("drive_id", "UNKNOWN")[:MAX_STR_LEN]
        roots = manifest.get("roots") or []
        if not isinstance(roots, list):
            raise ValueError("bad manifest: roots")
        if len(roots) > MAX_ROOTS:
            raise ValueError("manifest: too many roots")
        self.roots: List[dict] = []
        for r in roots:
            name = str(r.get("name",""))[:MAX_STR_LEN] if isinstance(r, dict) else ""
            idx = int(r.get("index", 0)) if isinstance(r, dict) else 0
            self.roots.append({"index": idx, "name": name, "path_hint": r.get("path_hint","") if isinstance(r, dict) else ""})

        files = manifest.get("files") or []
        if not isinstance(files, list):
            raise ValueError("bad manifest: files")
        if len(files) > MAX_FILES:
            raise ValueError("manifest: too many files")

        self.files: List[FileEntry] = []
        for f in files:
            if not isinstance(f, dict):
                continue
            root_index = int(f.get("root_index", 0))
            root_name = str(f.get("root_name", ""))[:MAX_STR_LEN]
            relpath = str(f.get("relpath", ""))[:MAX_STR_LEN]
            kind = str(f.get("kind", "file"))[:32]
            desc = f.get("desc") or {}
            if not isinstance(desc, dict):
                continue
            enc_name = str(desc.get("enc_name",""))
            wrapped_key = desc.get("wrapped_key_b64") or desc.get("wrapped_key_b64")
            # validate enc_name is safe
            if not _is_safe_filename(enc_name):
                raise ValueError("manifest contains unsafe enc_name")
            # ensure nonce/tag exist
            if not desc.get("nonce_b64") or not desc.get("tag_b64") or not wrapped_key:
                raise ValueError("manifest desc missing crypto fields")
            entry = FileEntry(root_index=root_index, root_name=root_name, relpath=relpath, kind=kind, desc=desc, quiz=f.get("quiz"))
            self.files.append(entry)

def load_package(secure_root: Path, drive_key: bytes) -> PackageView:
    enc_manifest = Path(secure_root) / MANIFEST_NAME
    if not enc_manifest.exists():
        raise FileNotFoundError(f"manifest not found at {enc_manifest}")
    manifest = _decrypt_manifest(enc_manifest, drive_key)
    # manifest must be a dict
    if not isinstance(manifest, dict):
        raise ValueError("manifest not an object")
    # basic fields
    pv = PackageView(Path(secure_root), manifest)
    return pv
