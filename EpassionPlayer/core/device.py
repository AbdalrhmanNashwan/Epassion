# core/device.py
from __future__ import annotations

import platform
import uuid
from pathlib import Path


def _mac() -> str:
    try:
        mac_int = uuid.getnode()
        return f"{mac_int:012x}"
    except Exception:
        return "00"*6


def simple_fingerprint() -> str:
    parts = [
        f"SYS:{platform.system()}",
        f"REL:{platform.release()}",
        f"NODE:{platform.node()}",
        f"CPU:{platform.machine()}",
        f"PROC:{platform.processor() or 'NA'}",
        f"MAC:{_mac()}",
    ]
    return ";".join(parts)


def read_package_id_from_usb(secure_root: Path) -> str | None:
    path = Path(secure_root) / "package_id.txt"
    try:
        if path.exists():
            return path.read_text(encoding="utf-8").strip()
    except Exception:
        pass
    return None
