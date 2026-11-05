# Copyright (C) 2026 Aliaksei Chernik
#
# This file is part of AstroProv.
#
# AstroProv is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# AstroProv is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.

"""Module for Ed25519 key generation, signing and verification."""

import os
import json
import hashlib
import tempfile
from pathlib import Path
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric import ed25519
from cryptography.hazmat.primitives import serialization


def _atomic_write(path: Path, data: bytes, mode: int = 0o644) -> None:
    """Helper to write bytes to a file atomically and securely.
    
    This prevents partial writes, fixes symlink vulnerabilities, 
    and ensures file permissions are set before the file is moved to its final path.
    """
    # Create a temporary file in the same directory to ensure os.replace works
    # within the same file system.
    fd, tmp_path_str = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    tmp_path = Path(tmp_path_str)
    
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())  # Force OS to flush data to disk immediately
            
        # Set permissions BEFORE the file is accessible at the final path
        tmp_path.chmod(mode)
        
        # Atomically rename the file (replaces any existing symlinks)
        os.replace(tmp_path, path)
    except Exception:
        # Clean up the temporary file if anything goes wrong
        if tmp_path.exists():
            tmp_path.unlink()
        raise


def generate_keys(keys_dir: Path | str) -> None:
    """Generates a key pair (private and public) safely if they don't exist yet."""
    keys_dir = Path(keys_dir).resolve()
    keys_dir.mkdir(parents=True, exist_ok=True)
    
    priv_path = keys_dir / "private_key.pem"
    pub_path = keys_dir / "public_key.pem"

    priv_exists = priv_path.is_file()
    pub_exists = pub_path.is_file()

    # Scenario 1: Both keys exist (normal operation)
    if priv_exists and pub_exists:
        return

    # Scenario 2: Only the public key exists. Cannot sign without a private key.
    if pub_exists and not priv_exists:
        raise FileNotFoundError(
            f"Public key found at {pub_path}, but private key {priv_path} is missing. "
            "Cannot sign without a private key. Please restore it from backup."
        )

    # Scenario 3: Only the private key exists. Recover the public key.
    if priv_exists and not pub_exists:
        print(f"  [WARN] Public key missing. Recovering it from private key...")
        with open(priv_path, "rb") as f:
            private_key = serialization.load_pem_private_key(
                f.read(), password=None
            )
        public_key = private_key.public_key()
        
        pub_data = public_key.public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo
        )
        _atomic_write(pub_path, pub_data, mode=0o644)
        print(f"  [OK] Public key recovered.")
        return

    # Scenario 4: Neither key exists. Generate a new pair from scratch.
    private_key = ed25519.Ed25519PrivateKey.generate()
    public_key = private_key.public_key()

    priv_data = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption()
    )
    # Private key gets strict 0600 permissions
    _atomic_write(priv_path, priv_data, mode=0o600)

    pub_data = public_key.public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo
    )
    # Public key gets standard 0644 permissions
    _atomic_write(pub_path, pub_data, mode=0o644)
    
    print(f"  Generated new Ed25519 keys in {keys_dir}")


def get_public_key_fingerprint(keys_dir: Path | str) -> str:
    """Returns a short SHA-256 fingerprint of the public key.

    The hash is computed over the DER SubjectPublicKeyInfo encoding, so the
    result is independent of the PEM text formatting (line endings, headers,
    etc.).
    """
    keys_dir = Path(keys_dir).resolve()
    pub_path = keys_dir / "public_key.pem"

    if not pub_path.exists():
        return "NO_KEY"

    with open(pub_path, "rb") as f:
        public_key = serialization.load_pem_public_key(f.read())

    der_bytes = public_key.public_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return hashlib.sha256(der_bytes).hexdigest()[:16]


def get_legacy_public_key_fingerprint(keys_dir: Path | str) -> str:
    """Returns the pre-1.4.4 fingerprint (SHA-256 of raw PEM bytes).

    Kept for verifying passports created by older AstroProv versions. New
    passports always use the DER-based fingerprint.
    """
    keys_dir = Path(keys_dir).resolve()
    pub_path = keys_dir / "public_key.pem"
    
    if not pub_path.exists():
        return "NO_KEY"
    
    with open(pub_path, "rb") as f:
        pub_bytes = f.read()
    return hashlib.sha256(pub_bytes).hexdigest()[:16]


def canonical_payload(passport: dict) -> bytes:
    """Creates a deterministic byte representation of the passport for signing.
    
    It includes all fields except the signature itself and uses compact 
    JSON formatting to ensure byte-for-byte consistency across platforms.
    """
    # Exclude the signature to avoid circular dependencies during verification
    payload = {k: v for k, v in passport.items() if k != "signature"}
    
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False
    ).encode("utf-8")


def sign_data(data_bytes: bytes, keys_dir: Path | str) -> str:
    """Signs data with the private key. Returns the signature as a hex string."""
    keys_dir = Path(keys_dir).resolve()
    priv_path = keys_dir / "private_key.pem"
    
    if not priv_path.exists():
        raise FileNotFoundError(f"Private key not found: {priv_path}")

    with open(priv_path, "rb") as f:
        private_key = serialization.load_pem_private_key(
            f.read(), password=None
        )

    signature = private_key.sign(data_bytes)
    return signature.hex()


def verify_signature(
    data_bytes: bytes, 
    signature_hex: str, 
    keys_dir: Path | str
) -> bool:
    """Verifies the signature using the public key."""
    keys_dir = Path(keys_dir).resolve()
    pub_path = keys_dir / "public_key.pem"
    
    if not pub_path.exists():
        return False

    with open(pub_path, "rb") as f:
        public_key = serialization.load_pem_public_key(f.read())

    try:
        public_key.verify(bytes.fromhex(signature_hex), data_bytes)
        return True
    except (InvalidSignature, ValueError, TypeError):
        # InvalidSignature: signature doesn't match
        # ValueError: signature_hex is not valid hex
        return False