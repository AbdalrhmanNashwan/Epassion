# core/server_api.py
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Tuple
import requests


def _url(base: str, path: str) -> str:
    return base.rstrip("/") + path


def activate_v2(server_base: str, code: str, package_id: str, fingerprint: str) -> Tuple[bool, str]:
    try:
        r = requests.post(
            _url(server_base, "/api/activate_v2"),
            json={"code": code, "package_id": package_id, "fingerprint": fingerprint},
            timeout=25,
        )
        data = r.json()
        if r.status_code == 200 and data.get("status") in ("bound",):
            return True, data.get("message", "bound")
        return False, data.get("message", f"HTTP {r.status_code}")
    except Exception as e:
        return False, str(e)


def license_v2(server_base: str, code: str, package_id: str, fingerprint: str, client_pub_pem: str):
    try:
        r = requests.post(
            _url(server_base, "/api/license_v2"),
            json={
                "code": code,
                "package_id": package_id,
                "fingerprint": fingerprint,
                "client_rsa_pub_pem": client_pub_pem,
            },
            timeout=30,
        )
        data = r.json()
        if r.status_code == 200 and data.get("status") == "ok":
            return True, data, ""
        return False, None, data.get("message", f"HTTP {r.status_code}")
    except Exception as e:
        return False, None, str(e)


def save_license_response(secure_root: str | Path, payload: dict[str, Any]) -> None:
    p = Path(secure_root) / "license_response.json"
    try:
        p.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    except Exception:
        pass
