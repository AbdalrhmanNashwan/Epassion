import os, base64
from typing import Tuple
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.keywrap import aes_key_wrap

def b64e(x: bytes) -> str: return base64.b64encode(x).decode("utf-8")
def b64d(s: str) -> bytes: return base64.b64decode(s.encode("utf-8"))
def generate_key() -> bytes: return os.urandom(32)  # AES-256

def encrypt_file_aesgcm(src_path, dst_path) -> Tuple[str, str, str, int]:
    """
    Encrypt entire file using a fresh AES-256-GCM content key.
    Output layout: [nonce(12)] + [ciphertext||tag(16)] saved to dst_path.
    Returns (content_key_b64, nonce_b64, tag_b64, size).
    """
    content_key = generate_key()
    aes = AESGCM(content_key)
    nonce = os.urandom(12)
    with open(src_path, "rb") as f:
        plaintext = f.read()
    size = len(plaintext)
    ciphertext = aes.encrypt(nonce, plaintext, None)
    with open(dst_path, "wb") as f:
        f.write(nonce); f.write(ciphertext)
    tag = ciphertext[-16:]
    return b64e(content_key), b64e(nonce), b64e(tag), size

def encrypt_bytes_aesgcm(key: bytes, data: bytes) -> bytes:
    aes = AESGCM(key)
    nonce = os.urandom(12)
    return nonce + aes.encrypt(nonce, data, None)

def aes_kw_wrap(kek: bytes, key_b64: str) -> str:
    """Wrap a per-file content key with the package KEK (drive_key) using AES key-wrap (RFC3394)."""
    key = b64d(key_b64)
    return b64e(aes_key_wrap(kek, key))
