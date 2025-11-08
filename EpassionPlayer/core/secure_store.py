# core/secure_store.py
from __future__ import annotations
import os
import sys
import json
from pathlib import Path
from typing import Optional

# Cross-platform store directory
if sys.platform.startswith("win"):
    DEFAULT_DIR = Path(os.getenv("APPDATA", Path.home())) / "Epassion" / "store"
else:
    DEFAULT_DIR = Path.home() / ".epassion_store"

DEFAULT_DIR.mkdir(parents=True, exist_ok=True)

# --- Windows DPAPI via ctypes ---
if sys.platform.startswith("win"):
    import ctypes
    from ctypes import wintypes

    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32

    class DATA_BLOB(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_byte))]

    def _protect_bytes(data: bytes) -> bytes:
        blob_in = DATA_BLOB(len(data), ctypes.cast(ctypes.create_string_buffer(data), ctypes.POINTER(ctypes.c_byte)))
        blob_out = DATA_BLOB()
        if not crypt32.CryptProtectData(ctypes.byref(blob_in), None, None, None, None, 0x01, ctypes.byref(blob_out)):
            raise OSError("CryptProtectData failed")
        try:
            out = ctypes.string_at(blob_out.pbData, blob_out.cbData)
            return out
        finally:
            kernel32.LocalFree(blob_out.pbData)

    def _unprotect_bytes(data: bytes) -> bytes:
        blob_in = DATA_BLOB(len(data), ctypes.cast(ctypes.create_string_buffer(data), ctypes.POINTER(ctypes.c_byte)))
        blob_out = DATA_BLOB()
        if not crypt32.CryptUnprotectData(ctypes.byref(blob_in), None, None, None, None, 0x01, ctypes.byref(blob_out)):
            raise OSError("CryptUnprotectData failed")
        try:
            out = ctypes.string_at(blob_out.pbData, blob_out.cbData)
            return out
        finally:
            kernel32.LocalFree(blob_out.pbData)
else:
    def _protect_bytes(data: bytes) -> bytes:
        # Non-Windows fallback: no encryption; rely on filesystem permissions.
        return data

    def _unprotect_bytes(data: bytes) -> bytes:
        return data

# --- helpers ---
def _safe_path_for(name: str) -> Path:
    safe = "".join(c for c in name if c.isalnum() or c in "-_.")
    return DEFAULT_DIR / f"{safe}.bin"

def save_bytes(name: str, data: bytes) -> None:
    """
    Save arbitrary bytes to secure store.
    On Windows uses DPAPI; on other OSes writes bytes and sets 0600 mode.
    """
    p = _safe_path_for(name)
    enc = _protect_bytes(data)
    # write atomically
    tmp = p.with_suffix(".tmp")
    with open(tmp, "wb") as f:
        f.write(enc)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, p)
    try:
        if not sys.platform.startswith("win"):
            p.chmod(0o600)
    except Exception:
        pass

def load_bytes(name: str) -> Optional[bytes]:
    p = _safe_path_for(name)
    if not p.exists():
        return None
    try:
        d = p.read_bytes()
        return _unprotect_bytes(d)
    except Exception:
        return None

# JSON helpers
def save_json(name: str, obj) -> None:
    save_bytes(name, json.dumps(obj, separators=(",", ":"), ensure_ascii=False).encode("utf-8"))

def load_json(name: str):
    b = load_bytes(name)
    if b is None:
        return None
    try:
        return json.loads(b.decode("utf-8"))
    except Exception:
        return None

def save_text(name: str, text: str) -> None:
    save_bytes(name, text.encode("utf-8"))

def load_text(name: str) -> Optional[str]:
    b = load_bytes(name)
    if b is None:
        return None
    return b.decode("utf-8")
