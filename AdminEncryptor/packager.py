import json, hashlib, uuid, os
from pathlib import Path
from datetime import datetime
from secrets import token_hex
from typing import Dict, List, Optional, Literal, Tuple
from crypto_utils import generate_key, encrypt_file_aesgcm, aes_kw_wrap, encrypt_bytes_aesgcm
from server_client import import_package_to_server

SECURE_ROOT = "SecureContent"
CONTENT_DIR = "content"

SupportedKind = Literal["video", "pdf", "image", "other"]

class SupportedExts:
    VIDEOS = {".mp4", ".mkv", ".mov", ".avi", ".m4v"}
    PDFS   = {".pdf"}
    IMAGES = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp"}

def detect_kind(p: Path) -> SupportedKind:
    ext = p.suffix.lower()
    if ext in SupportedExts.VIDEOS: return "video"
    if ext in SupportedExts.PDFS:   return "pdf"
    if ext in SupportedExts.IMAGES: return "image"
    return "other"

def _mime(kind: SupportedKind) -> str:
    return {"video":"video","pdf":"pdf","image":"image"}.get(kind, "file")

class PackageBuilder:
    """
    Multi-root build:
      - Walk all files under each selected root, preserve structure.
      - Encrypt every file independently.
      - If a file is a video and appears in quiz_map, embed its quiz in manifest.
    """
    def __init__(self, target_root: Path, drive_id: str):
        self.target_root = Path(target_root)
        self.drive_id = drive_id
        self.package_id = str(uuid.uuid4())
        self.drive_key = generate_key()  # KEK
        self.roots: List[Path] = []
        # quiz_map: ABSOLUTE posix path -> list[dict]
        self.quiz_map: Dict[str, List[dict]] = {}

    def set_roots_and_quizzes(self, roots: List[Path], quiz_map: Dict[str, List[dict]]):
        self.roots = [Path(r) for r in roots]
        # normalize quiz_map keys to absolute posix
        qm: Dict[str, List[dict]] = {}
        for k, v in quiz_map.items():
            key = Path(k).resolve().as_posix()
            qm[key] = list(v or [])
        self.quiz_map = qm

    # ===== helpers =====
    def _random_name(self) -> str:
        return f"{token_hex(6)}_{token_hex(2)}.bin"

    def _export_drive_key(self) -> Path:
        keys_folder = Path(__file__).parent / "admin_keys"
        keys_folder.mkdir(exist_ok=True)
        data = {
            "package_id": self.package_id,
            "drive_id": self.drive_id,
            "drive_key_hex": self.drive_key.hex(),
            "generated_at": datetime.utcnow().isoformat() + "Z",
            "fingerprint": hashlib.sha256(self.drive_key).hexdigest()
        }
        filename = f"{datetime.utcnow():%Y%m%d}_{self.package_id}.json"
        path = keys_folder / filename
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        return path

    def _encrypt_and_describe(self, src: Path) -> Tuple[Dict, SupportedKind]:
        secure_root = self.target_root / SECURE_ROOT
        content_dir = secure_root / CONTENT_DIR
        content_dir.mkdir(parents=True, exist_ok=True)

        enc_name = self._random_name()
        enc_path = content_dir / enc_name
        ck_b64, nonce_b64, tag_b64, size = encrypt_file_aesgcm(src, enc_path)

        kind = detect_kind(src)
        desc = {
            "enc_name": enc_name,
            "size": size,
            "nonce_b64": nonce_b64,
            "tag_b64": tag_b64,
            "wrapped_key_b64": aes_kw_wrap(self.drive_key, ck_b64),
            "mime": _mime(kind)
        }
        return desc, kind

    def _walk_all_files(self) -> List[Tuple[int, Path]]:
        """
        Return list of (root_index, absolute_file_path) for every file
        under every selected root (hidden files excluded).
        """
        out: List[Tuple[int, Path]] = []
        for i, root in enumerate(self.roots):
            if not root.exists(): continue
            for dirpath, _, filenames in os.walk(root):
                for name in filenames:
                    p = Path(dirpath) / name
                    if p.name.startswith("."):  # ignore hidden
                        continue
                    out.append((i, p))
        return out

    def build(self, write_package_id_txt: bool = True):
        if not self.roots:
            raise RuntimeError("No roots set. Call set_roots_and_quizzes().")

        secure_root = self.target_root / SECURE_ROOT
        (secure_root / CONTENT_DIR).mkdir(parents=True, exist_ok=True)

        files_desc: List[Dict] = []

        for root_idx, src in self._walk_all_files():
            desc, kind = self._encrypt_and_describe(src)
            root = self.roots[root_idx]
            relpath = src.relative_to(root).as_posix()
            abs_key = src.resolve().as_posix()

            entry = {
                "root_index": root_idx,         # which selected root
                "root_name": root.name,         # for convenience
                "relpath": relpath,             # path inside that root
                "kind": kind,                   # video/pdf/image/other
                "desc": desc
            }

            # attach quiz if exists for this absolute path (videos only)
            if kind == "video":
                normalized = []
                for q in (self.quiz_map.get(abs_key) or [])[:2]:
                    text = (q.get("q") or "").strip()
                    options = q.get("options") or []
                    correct = q.get("correct_index")
                    if not text or not isinstance(options, list) or len(options) != 4:
                        continue
                    opts = [(o or "").strip() for o in options]
                    if any(not o for o in opts):
                        continue
                    if len(set(opts)) < 4:
                        continue
                    if not isinstance(correct, int) or not (0 <= correct <= 3):
                        continue
                    normalized.append({"q": text, "options": opts, "correct_index": correct})
                if normalized:
                    entry["quiz"] = normalized

            files_desc.append(entry)

        # manifest (encrypted)
        manifest = {
            "version": 4,
            "package_id": self.package_id,
            "drive_id": self.drive_id,
            "roots": [
                {"index": i, "name": r.name, "path_hint": r.name} for i, r in enumerate(self.roots)
            ],
            "files": files_desc
        }
        enc_manifest = encrypt_bytes_aesgcm(
            self.drive_key,
            json.dumps(manifest, separators=(",", ":")).encode("utf-8")
        )
        (secure_root / "manifest.enc").write_bytes(enc_manifest)

        if write_package_id_txt:
            (secure_root / "package_id.txt").write_text(self.package_id, encoding="utf-8")

        key_path = self._export_drive_key()
        return secure_root, str(key_path), self.package_id

    def upload_to_server(self, server_base: str, admin_user: str, admin_pass: str):
        payload = {
            "package_id": self.package_id,
            "drive_id": self.drive_id,
            "drive_key_hex": self.drive_key.hex(),
            "generated_at": datetime.utcnow().isoformat() + "Z",
            "fingerprint": hashlib.sha256(self.drive_key).hexdigest()
        }
        return import_package_to_server(server_base, admin_user, admin_pass, payload)
