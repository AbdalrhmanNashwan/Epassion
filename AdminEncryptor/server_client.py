import requests

def import_package_to_server(server_base: str, admin_user: str, admin_pass: str, payload: dict):
    url = server_base.rstrip('/') + '/admin/import_package'
    try:
        r = requests.post(url, json=payload, auth=(admin_user, admin_pass), timeout=20)
        if r.status_code == 200:
            j = r.json()
            if j.get("ok"): return True, j.get("message", "uploaded")
            return False, j.get("message", "server said ok=false")
        return False, f"HTTP {r.status_code}: {r.text[:200]}"
    except Exception as e:
        return False, str(e)
