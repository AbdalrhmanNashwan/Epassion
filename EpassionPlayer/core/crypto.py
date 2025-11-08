# core/crypto.py
from __future__ import annotations

import base64
import os
import sys
import subprocess
import tempfile
from pathlib import Path
from typing import Dict

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.keywrap import aes_key_unwrap

def b64d(s: str) -> bytes:
    return base64.b64decode(s.encode("utf-8"))

def unwrap_content_key(drive_key: bytes, wrapped_b64: str) -> bytes:
    wrapped = b64d(wrapped_b64)
    return aes_key_unwrap(drive_key, wrapped)

def decrypt_file_to_temp(root_path: str | Path, desc: Dict, drive_key: bytes) -> str:
    """
    Safely write decrypted file to a temporary file and return its path.

    Steps:
      - unwrap per-file key
      - read SecureContent/content/<enc_name> (nonce||ciphertext+tag)
      - decrypt with AES-256-GCM
      - write to secure temp file (0600) and return path
    """
    root = Path(root_path)
    enc_name = desc["enc_name"]
    enc_path = root / "content" / enc_name

    # basic checks
    if not enc_path.exists() or not enc_path.is_file():
        raise FileNotFoundError("encrypted content not found")

    content_key = unwrap_content_key(drive_key, desc["wrapped_key_b64"])

    # read in streaming-friendly way (file might be large)
    blob = enc_path.read_bytes()
    if len(blob) < 12 + 16:
        raise ValueError("file too small or corrupt")

    nonce = blob[:12]
    ciphertext = blob[12:]

    aes = AESGCM(content_key)
    plaintext = aes.decrypt(nonce, ciphertext, None)

    # safety: avoid creating insanely large temp files unexpectedly
    MAX_TEMP_BYTES = 8 * 1024 * 1024 * 1024  # 8 GiB (tune if necessary)
    if len(plaintext) > MAX_TEMP_BYTES:
        raise ValueError("file too large")

    # choose extension from mime hint
    mime = (desc.get("mime") or "").lower()
    ext = {
        "video": ".mp4",
        "pdf": ".pdf",
        "image": ".png",
        "file": "",
    }.get(mime, "")

    # create secure temp file atomically
    fd, tmp_path = tempfile.mkstemp(prefix="epassion_", suffix=ext)
    try:
        # set restrictive permissions immediately
        try:
            if os.name != "nt":
                os.fchmod(fd, 0o600)
        except Exception:
            pass

        # write and flush
        with os.fdopen(fd, "wb") as f:
            f.write(plaintext)
            f.flush()
            try:
                os.fsync(f.fileno())
            except Exception:
                pass
    except Exception:
        try:
            os.unlink(tmp_path)
        except Exception:
            pass
        raise

    return tmp_path

def open_with_default_app(path: str) -> None:
    p = Path(path)
    try:
        if os.name == "nt":
            os.startfile(str(p))  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(p)])
        else:
            subprocess.Popen(["xdg-open", str(p)])
    except Exception:
        pass

def safe_delete(path: str) -> None:
    try:
        if not path:
            return
        p = Path(path)
        if p.is_file():
            try:
                size = p.stat().st_size
                # overwrite a few MBs at most to avoid long delays
                to_write = min(size, 4 * 1024 * 1024)
                with open(p, "r+b", buffering=0) as f:
                    f.seek(0)
                    f.write(os.urandom(to_write))
                p.unlink(missing_ok=True)
            except Exception:
                try:
                    p.unlink(missing_ok=True)
                except Exception:
                    pass
    except Exception:
        pass
