"""Trusted timestamping via RFC 3161 (FreeTSA)."""

from pathlib import Path

import requests
from cryptography import x509
from rfc3161_client import (
    TimestampRequestBuilder,
    decode_timestamp_response,
    VerifierBuilder,
)

CERTS_DIR = Path(__file__).parent / "certs"
CA_CERT_PATH = CERTS_DIR / "cacert.pem"
TSA_CERT_PATH = CERTS_DIR / "tsa.crt"
TSA_URL = "https://freetsa.org/tsr"


def _load_certs() -> tuple[list[x509.Certificate], x509.Certificate]:
    if not CA_CERT_PATH.is_file() or not TSA_CERT_PATH.is_file():
        raise FileNotFoundError(
            "FreeTSA certificates not found in astroprov/certs/."
        )
    ca_certs = x509.load_pem_x509_certificates(CA_CERT_PATH.read_bytes())
    tsa_cert = x509.load_pem_x509_certificate(TSA_CERT_PATH.read_bytes())
    return ca_certs, tsa_cert


def stamp_data(data: bytes, output_path: Path) -> Path:
    """Send raw bytes to FreeTSA and save the TimeStampToken (.tsr)."""
    _, tsa_cert = _load_certs()

    # Передаём исходные данные, библиотека сама вычислит SHA-256
    request = TimestampRequestBuilder().data(data).build()

    response = requests.post(
        TSA_URL,
        data=request.as_bytes(),
        headers={"Content-Type": "application/timestamp-query"},
        timeout=15,
    )
    response.raise_for_status()

    timestamp_response = decode_timestamp_response(response.content)
    output_path.write_bytes(timestamp_response.as_bytes())

    return output_path


def verify_data(data: bytes, tsr_path: Path) -> bool:
    """Verify a TimeStampToken offline against FreeTSA certificates."""
    if not tsr_path.is_file():
        return False

    ca_certs, tsa_cert = _load_certs()
    timestamp_response = decode_timestamp_response(tsr_path.read_bytes())

    verifier = VerifierBuilder()
    for cert in ca_certs:
        verifier = verifier.add_root_certificate(cert)
    verifier = verifier.add_intermediate_certificate(tsa_cert)
    verifier = verifier.build()

    try:
        # verify_message сам вычислит хэш от data и сравнит с тем, что в токене
        return verifier.verify_message(timestamp_response, data)
    except Exception as exc:
        print(f"  [DEBUG] Timestamp verification error: {exc}")
        return False