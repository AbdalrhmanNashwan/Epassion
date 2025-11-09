# AdminEncryptor/crypto_utils.py
from __future__ import annotations
import os
import base64
from typing import Tuple, Optional, Callable
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.keywrap import aes_key_wrap

# --------------------
# Base64 helpers
# --------------------
def b64e(x: bytes) -> str:
    return base64.b64encode(x).decode("utf-8")

def b64d(s: str) -> bytes:
    return base64.b64decode(s.encode("utf-8"))

# --------------------
# Keys
# --------------------
def generate_key() -> bytes:
    """Generate a fresh 32-byte AES-256 key."""
    return os.urandom(32)

# --------------------
# Small (in-memory) helpers - unchanged behavior
# --------------------
def encrypt_bytes_aesgcm(key: bytes, data: bytes) -> bytes:
    """
    Encrypt small byte arrays entirely in memory using AES-256-GCM.
    Returns: nonce||ciphertext (ciphertext includes the 16-byte tag at the end).
    """
    aes = AESGCM(key)
    nonce = os.urandom(12)
    return nonce + aes.encrypt(nonce, data, None)

def aes_kw_wrap(kek: bytes, key_b64: str) -> str:
    """
    Wrap a per-file AES content key (base64) with AES Key Wrap (RFC 3394) using the KEK (kek).
    Returns wrapped key as base64 string.
    """
    key = b64d(key_b64)
    return b64e(aes_key_wrap(kek, key))

# --------------------
# Streaming encryption / decryption for HUGE files (constant memory)
# Layout we produce/consume:
#   [nonce(12)] + [ciphertext ...] + [tag(16)]
# --------------------
def encrypt_file_aesgcm_stream(
    src_path: str,
    dst_path: str,
    *,
    chunk_size: int = 8 * 1024 * 1024,
    on_progress: Optional[Callable[[int], None]] = None,  # progress 0..100
) -> Tuple[str, str, str, int]:
    """
    Stream-encrypt a file with AES-256-GCM.
    Writes output as: [nonce(12)] + [ciphertext...] + [tag(16)].
    Returns (content_key_b64, nonce_b64, tag_b64, size_bytes).
    """
    size = os.path.getsize(src_path)
    content_key = generate_key()
    nonce = os.urandom(12)

    encryptor = Cipher(algorithms.AES(content_key), modes.GCM(nonce)).encryptor()

    done = 0
    with open(src_path, "rb") as fin, open(dst_path, "wb") as fout:
        # Write nonce first
        fout.write(nonce)

        while True:
            buf = fin.read(chunk_size)
            if not buf:
                break
            ct = encryptor.update(buf)
            if ct:
                fout.write(ct)
            done += len(buf)
            if on_progress and size:
                on_progress(int(done * 100 / size))

        encryptor.finalize()
        # Append tag at the very end
        fout.write(encryptor.tag)

    return b64e(content_key), b64e(nonce), b64e(encryptor.tag), size

def decrypt_file_aesgcm_stream(
    src_path: str,
    dst_path: str,
    content_key_b64: str,
    *,
    chunk_size: int = 8 * 1024 * 1024,
    on_progress: Optional[Callable[[int], None]] = None,  # progress 0..100
) -> None:
    """
    Stream-decrypt a file produced by encrypt_file_aesgcm_stream().
    Expects input layout: [nonce(12)] + [ciphertext...] + [tag(16)].
    """
    key = b64d(content_key_b64)
    total = os.path.getsize(src_path)
    if total < 12 + 16:
        raise ValueError("Ciphertext too small")

    with open(src_path, "rb") as fin:
        # Read header nonce
        nonce = fin.read(12)

        # Read tag by seeking to the end
        fin.seek(total - 16, os.SEEK_SET)
        tag = fin.read(16)

        # Now set stream window for ciphertext
        data_len = total - 12 - 16
        fin.seek(12, os.SEEK_SET)

        decryptor = Cipher(algorithms.AES(key), modes.GCM(nonce, tag)).decryptor()

        remaining = data_len
        done = 0
        with open(dst_path, "wb") as fout:
            while remaining > 0:
                n = min(chunk_size, remaining)
                buf = fin.read(n)
                if not buf:
                    raise IOError("Unexpected EOF while reading ciphertext")
                pt = decryptor.update(buf)
                if pt:
                    fout.write(pt)
                remaining -= n
                done += n
                if on_progress and data_len:
                    on_progress(int(done * 100 / data_len))

            # finalize (auth check)
            decryptor.finalize()

# --------------------
# Backwards-compatible alias with the old name/signature
# --------------------
def encrypt_file_aesgcm(src_path: str, dst_path: str) -> Tuple[str, str, str, int]:
    """
    Backwards-compatible wrapper. Now streams under the hood.
    """
    return encrypt_file_aesgcm_stream(src_path, dst_path)
