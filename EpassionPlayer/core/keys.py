# core/keys.py
from __future__ import annotations

from pathlib import Path
from typing import Optional

from cryptography.hazmat.primitives import serialization, hashes
from cryptography.hazmat.primitives.asymmetric import rsa, padding as asy_padding

from core import secure_store

# Where we keep ONLY the public key (safe on disk)
CLIENT_KEYS_DIR = Path(".client_keys")
CLIENT_PUB_PEM = CLIENT_KEYS_DIR / "client_pub.pem"

# Names used inside secure_store (OS-protected):
STORE_PRIV_NAME = "client_rsa_priv_der"   # private key (PKCS8 DER)
STORE_PUB_NAME  = "client_rsa_pub_pem"    # convenience copy of PEM (for rebuilds)


def _ensure_dirs():
    CLIENT_KEYS_DIR.mkdir(parents=True, exist_ok=True)


def _load_private_key_from_store():
    priv_der = secure_store.load_bytes(STORE_PRIV_NAME)
    if not priv_der:
        return None
    try:
        return serialization.load_der_private_key(priv_der, password=None)
    except Exception:
        return None


def _load_public_pem_from_store() -> Optional[bytes]:
    return secure_store.load_bytes(STORE_PUB_NAME)


def _write_public_pem_to_disk(pub_pem: bytes) -> None:
    _ensure_dirs()
    try:
        CLIENT_PUB_PEM.write_bytes(pub_pem)
    except Exception:
        pass


def ensure_keypair() -> None:
    """
    Ensure we have a device RSA keypair:
      - Private key: stored ONLY in secure_store (DPAPI on Windows).
      - Public key:  written to .client_keys/client_pub.pem (harmless).
    If priv is missing: generate a new 2048-bit RSA keypair.
    If pub file is missing: regenerate it from store copy.
    """
    priv = _load_private_key_from_store()
    if priv is None:
        # First run — create brand new keypair
        priv = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        priv_der = priv.private_bytes(
            encoding=serialization.Encoding.DER,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
        secure_store.save_bytes(STORE_PRIV_NAME, priv_der)

        pub_pem = priv.public_key().public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        secure_store.save_bytes(STORE_PUB_NAME, pub_pem)
        _write_public_pem_to_disk(pub_pem)
        return

    # We have a private key; ensure public PEM exists (both store and disk)
    pub_pem = _load_public_pem_from_store()
    if not pub_pem:
        pub_pem = priv.public_key().public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        secure_store.save_bytes(STORE_PUB_NAME, pub_pem)

    # Make sure disk copy exists (safe to overwrite)
    if not CLIENT_PUB_PEM.exists():
        _write_public_pem_to_disk(pub_pem)


def get_public_pem_text() -> str:
    """
    Return the current public key PEM as text (ensures keypair first).
    """
    ensure_keypair()
    pub = _load_public_pem_from_store()
    if pub:
        return pub.decode("utf-8")
    # Fallback to disk (should exist after ensure_keypair)
    try:
        return CLIENT_PUB_PEM.read_text(encoding="utf-8")
    except Exception:
        return ""


def rsa_decrypt_oaep_sha256(cipher_bytes: bytes) -> bytes:
    """
    Decrypt bytes using the in-store private key (OAEP-SHA256).
    """
    priv = _load_private_key_from_store()
    if priv is None:
        raise RuntimeError("Device private key not available in secure store")
    return priv.decrypt(
        cipher_bytes,
        asy_padding.OAEP(
            mgf=asy_padding.MGF1(algorithm=hashes.SHA256()),
            algorithm=hashes.SHA256(),
            label=None,
        ),
    )
