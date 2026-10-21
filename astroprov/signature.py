"""Модуль для генерации ключей Ed25519, подписи и проверки данных."""

import hashlib
from pathlib import Path
from cryptography.hazmat.primitives.asymmetric import ed25519
from cryptography.hazmat.primitives import serialization


def generate_keys(keys_dir: Path) -> None:
    """Генерирует пару ключей (приватный и публичный), если их еще нет."""
    keys_dir.mkdir(parents=True, exist_ok=True)
    
    priv_path = keys_dir / "private_key.pem"
    pub_path = keys_dir / "public_key.pem"

    if priv_path.exists() and pub_path.exists():
        return  # Ключи уже существуют

    # Генерируем ключи
    private_key = ed25519.Ed25519PrivateKey.generate()
    public_key = private_key.public_key()

    # Сохраняем приватный ключ (только для владельца)
    with open(priv_path, "wb") as f:
        f.write(private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption()
        ))

    # Сохраняем публичный ключ
    with open(pub_path, "wb") as f:
        f.write(public_key.public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo
        ))
    
    print(f"  ✓ Сгенерированы новые ключи Ed25519 в {keys_dir}")


def get_public_key_fingerprint(keys_dir: Path) -> str:
    """Возвращает короткий SHA-256 отпечаток публичного ключа."""
    pub_path = keys_dir / "public_key.pem"
    if not pub_path.exists():
        return "NO_KEY"
    
    with open(pub_path, "rb") as f:
        pub_bytes = f.read()
    return hashlib.sha256(pub_bytes).hexdigest()[:16]


def sign_data(data_bytes: bytes, keys_dir: Path) -> str:
    """Подписывает данные приватным ключом. Возвращает hex-строку подписи."""
    priv_path = keys_dir / "private_key.pem"
    
    if not priv_path.exists():
        raise FileNotFoundError(f"Приватный ключ не найден: {priv_path}")

    with open(priv_path, "rb") as f:
        private_key = serialization.load_pem_private_key(
            f.read(), password=None
        )

    signature = private_key.sign(data_bytes)
    return signature.hex()


def verify_signature(data_bytes: bytes, signature_hex: str, keys_dir: Path) -> bool:
    """Проверяет подпись с помощью публичного ключа."""
    pub_path = keys_dir / "public_key.pem"
    
    if not pub_path.exists():
        return False

    with open(pub_path, "rb") as f:
        public_key = serialization.load_pem_public_key(f.read())

    try:
        public_key.verify(bytes.fromhex(signature_hex), data_bytes)
        return True
    except Exception:
        return False