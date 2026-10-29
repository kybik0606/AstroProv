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

import hashlib
from pathlib import Path
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric import ed25519
from cryptography.hazmat.primitives import serialization


def generate_keys(keys_dir: Path | str) -> None:
    """Generates a key pair (private and public) if they don't exist yet."""
    keys_dir = Path(keys_dir).resolve()
    keys_dir.mkdir(parents=True, exist_ok=True)
    
    priv_path = keys_dir / "private_key.pem"
    pub_path = keys_dir / "public_key.pem"

    if priv_path.exists() and pub_path.exists():
        return  # Keys already exist

    # Generate keys
    private_key = ed25519.Ed25519PrivateKey.generate()
    public_key = private_key.public_key()

    # Save the private key (owner only)
    with open(priv_path, "wb") as f:
        f.write(private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption()
        ))
    
    # SECURITY: Restrict file permissions on Unix-like systems (no-op on Windows)
    priv_path.chmod(0o600)

    # Save the public key
    with open(pub_path, "wb") as f:
        f.write(public_key.public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo
        ))
    
    print(f"  Generated new Ed25519 keys in {keys_dir}")


def get_public_key_fingerprint(keys_dir: Path | str) -> str:
    """Returns a short SHA-256 fingerprint of the public key."""
    keys_dir = Path(keys_dir).resolve()
    pub_path = keys_dir / "public_key.pem"
    
    if not pub_path.exists():
        return "NO_KEY"
    
    with open(pub_path, "rb") as f:
        pub_bytes = f.read()
    return hashlib.sha256(pub_bytes).hexdigest()[:16]


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