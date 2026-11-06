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

"""Unit tests for astroprov.cli.seal (unified standalone and collection modes)."""

import json
from pathlib import Path

import pytest
from astropy.io import fits

from astroprov import signature, verify
from astroprov.cli import seal
from astroprov.utils import safe_int, safe_float


# ---------------------------------------------------------------- helpers ---

def make_fits(path: Path, lights: int = 10, exptime: float = 120.0) -> Path:
    """Create a dummy FITS file with specific headers."""
    hdu = fits.PrimaryHDU()
    hdu.header["STACKCNT"] = lights
    hdu.header["EXPTIME"] = exptime
    hdu.writeto(path, overwrite=True)
    return path


def make_raw_dir(path: Path) -> Path:
    """Create a dummy raw directory with a lights subfolder."""
    path.mkdir()
    lights_dir = path / "lights"
    lights_dir.mkdir()
    make_fits(lights_dir / "light1.fits")
    return path


# ------------------------------------------------------------ utils tests ---

def test_safe_int_handles_valid_and_invalid_values():
    assert safe_int("123") == 123
    assert safe_int(123.9) == 123
    assert safe_int("unknown") is None
    assert safe_int(None) is None


def test_safe_float_handles_valid_and_invalid_values():
    assert safe_float("120.5") == 120.5
    assert safe_float(120) == 120.0
    assert safe_float("unknown") is None
    assert safe_float(None) is None


# ---------------------------------------------------------- core function ---

@pytest.fixture
def mocked_environment(monkeypatch, tmp_path):
    """Mock external dependencies to avoid crypto and network calls."""
    # Mock keys
    monkeypatch.setattr(seal.signature, "generate_keys", lambda d: None)
    monkeypatch.setattr(seal.signature, "get_public_key_fingerprint", lambda d: "abcdef1234567890")
    monkeypatch.setattr(seal.signature, "sign_data", lambda b, d: "fake_signature_hex")
    
    # Mock timestamping (avoid network)
    monkeypatch.setattr(seal.timestamping, "stamp_data", lambda b, p: None)
    
    # Mock write_provenance to capture the passport data
    captured_prov = {}
    def fake_write_provenance(fits_path, prov_data):
        captured_prov.update(prov_data)
    monkeypatch.setattr(seal.fits_prov, "write_provenance", fake_write_provenance)
    
    return captured_prov


def test_seal_standalone_mode_embeds_correct_schema(tmp_path, mocked_environment):
    fits_path = make_fits(tmp_path / "test.fit", lights=15, exptime=300.0)
    
    # Run seal in standalone mode
    seal.seal_fits(fits_path, keys_dir=tmp_path, object_name="M31")
    
    # Check the captured prov_data
    prov = mocked_environment
    assert prov["schema"] == "astroprov-standalone-v1"
    assert prov["tool"] == "AstroProv-Standalone"
    assert prov["lights_count"] == 15
    assert prov["exptime"] == 300.0


def test_seal_manual_overrides_replace_header_values(tmp_path, mocked_environment):
    fits_path = make_fits(tmp_path / "test.fit", lights=15, exptime=300.0)
    
    # Override with manual values
    seal.seal_fits(fits_path, keys_dir=tmp_path, object_name="M31", manual_lights=99, manual_exptime=600.0)
    
    prov = mocked_environment
    assert prov["lights_count"] == 99
    assert prov["exptime"] == 600.0


def test_seal_collection_mode_uses_lights_merkle_root(
    tmp_path, monkeypatch, mocked_environment
):
    fits_path = make_fits(tmp_path / "test.fit", lights=10, exptime=120.0)
    raw_dir = make_raw_dir(tmp_path / "raw")

    lights_root = "a" * 64
    fake_categories = {
        "lights": {"file_count": 10, "merkle_root": lights_root},
        "darks": {"file_count": 4, "merkle_root": "b" * 64},
    }
    monkeypatch.setattr(seal.snapshot, "snapshot_directory", lambda d: fake_categories)

    seal.seal_fits(
        fits_path, keys_dir=tmp_path, object_name="M31", raw_dir=raw_dir
    )

    prov = mocked_environment
    assert prov["merkle_root"] == lights_root

    report_path = next(
        (tmp_path / ".astroprov" / "runs").glob("processing-collection-*.json")
    )
    report_data = json.loads(report_path.read_text(encoding="utf-8"))
    assert report_data["output_fits"]["sha256"] == (
        seal.fits_prov.calculate_fits_data_hash(fits_path)
    )


def test_seal_collection_mode_requires_lights(tmp_path, monkeypatch, mocked_environment):
    fits_path = make_fits(tmp_path / "test.fit", lights=10, exptime=120.0)
    raw_dir = make_raw_dir(tmp_path / "raw")

    monkeypatch.setattr(
        seal.snapshot,
        "snapshot_directory",
        lambda d: {"darks": {"file_count": 4, "merkle_root": "b" * 64}},
    )

    with pytest.raises(ValueError, match="no 'lights' category"):
        seal.seal_fits(
            fits_path, keys_dir=tmp_path, object_name="M31", raw_dir=raw_dir
        )


def test_seal_collection_mode_embeds_source_data(tmp_path, monkeypatch, mocked_environment):
    fits_path = make_fits(tmp_path / "test.fit", lights=10, exptime=120.0)
    raw_dir = make_raw_dir(tmp_path / "raw")
    
    # Mock snapshot to return a fake categories dict without reading real files
    fake_categories = {
        "lights": {"file_count": 10, "merkle_root": "1111111111111111"},
    }
    monkeypatch.setattr(seal.snapshot, "snapshot_directory", lambda d: fake_categories)
    
    # Run seal in collection mode
    seal.seal_fits(fits_path, keys_dir=tmp_path, object_name="M31", raw_dir=raw_dir)
    
    prov = mocked_environment
    assert prov["schema"] == "astroprov-collection-v1"
    assert prov["tool"] == "AstroProv-Universal"
    assert prov["lights_count"] == 10
    
    # Verify the JSON report contains the source_data
    runs_dir = tmp_path / ".astroprov" / "runs"
    report_files = list(runs_dir.glob("processing-collection-*.json"))
    assert len(report_files) == 1
    
    report_data = json.loads(report_files[0].read_text())
    assert "source_data" in report_data
    assert report_data["source_data"]["lights"]["merkle_root"] == "1111111111111111"
    assert report_data["passport_id"].startswith("collection-")
    assert report_data["cross_check"]["fits_lights"] == 10
    assert report_data["cross_check"]["raw_lights"] == 10


# ------------------------------------------------ negative scenarios ---

def test_seal_fits_missing_file(tmp_path):
    """Should raise FileNotFoundError if the FITS file does not exist."""
    missing = tmp_path / "ghost.fits"
    with pytest.raises(FileNotFoundError, match="FITS file not found"):
        seal.seal_fits(missing, keys_dir=None, object_name="Test")


def test_seal_fits_missing_raw_dir(tmp_path, monkeypatch):
    """Should raise NotADirectoryError if --raw points to a nonexistent folder."""
    fits_path = make_fits(tmp_path / "test.fit")
    missing_raw = tmp_path / "ghost_dir"
    
    monkeypatch.setattr(seal.signature, "generate_keys", lambda d: None)
    
    with pytest.raises(NotADirectoryError, match="Raw directory not found"):
        seal.seal_fits(fits_path, keys_dir=None, object_name="Test", raw_dir=missing_raw)


def test_seal_fits_raw_is_file_not_dir(tmp_path, monkeypatch):
    """Should raise NotADirectoryError if --raw points to a file, not a folder."""
    fits_path = make_fits(tmp_path / "test.fit")
    fake_raw = tmp_path / "not_a_dir.txt"
    fake_raw.write_text("hello")
    
    monkeypatch.setattr(seal.signature, "generate_keys", lambda d: None)
    
    with pytest.raises(NotADirectoryError, match="Raw directory not found"):
        seal.seal_fits(fits_path, keys_dir=None, object_name="Test", raw_dir=fake_raw)


def test_seal_fits_corrupt_fits(tmp_path, monkeypatch):
    """Should fail gracefully if the FITS file is corrupted."""
    bad_fits = tmp_path / "corrupt.fits"
    bad_fits.write_text("this is not a FITS file")
    
    monkeypatch.setattr(seal.signature, "generate_keys", lambda d: None)
    monkeypatch.setattr(seal.signature, "get_public_key_fingerprint", lambda d: "fakefp")
    
    with pytest.raises(OSError):
        seal.seal_fits(bad_fits, keys_dir=tmp_path, object_name="Test")


# ------------------------------------------------ key fingerprint checks ---

def test_public_key_fingerprint_is_der_based_and_key_specific(tmp_path):
    """Fingerprint must ignore PEM formatting and change with the key."""
    keys_dir = tmp_path / "keys"
    signature.generate_keys(keys_dir)

    pem_path = keys_dir / "public_key.pem"
    original_pem = pem_path.read_bytes()
    fingerprint = signature.get_public_key_fingerprint(keys_dir)

    assert len(fingerprint) == 16

    # Re-encoding the same key with CRLF line endings must not change the
    # fingerprint (the old implementation hashed raw PEM bytes).
    pem_path.write_bytes(original_pem.replace(b"\n", b"\r\n"))
    assert signature.get_public_key_fingerprint(keys_dir) == fingerprint

    # A different public key must produce a different fingerprint.
    other_keys = tmp_path / "other_keys"
    signature.generate_keys(other_keys)
    assert signature.get_public_key_fingerprint(other_keys) != fingerprint


def test_verify_fails_when_public_key_differs_from_passport(
    tmp_path, monkeypatch, capsys
):
    """Replacing the verification key must not make a sealed file pass."""
    monkeypatch.setattr(seal.timestamping, "stamp_data", lambda data, path: None)

    fits_path = make_fits(tmp_path / "sealed.fit", lights=5, exptime=60.0)
    seal.seal_fits(fits_path, keys_dir=None, object_name="M31")

    keys_dir = tmp_path / ".astroprov" / "keys"
    attacker_keys = tmp_path / "attacker_keys"
    signature.generate_keys(attacker_keys)
    (keys_dir / "public_key.pem").write_bytes(
        (attacker_keys / "public_key.pem").read_bytes()
    )

    assert verify.verify_output(str(fits_path)) == 1
    assert "Passport fingerprint does not match" in capsys.readouterr().out


def test_verify_honors_trusted_key(tmp_path, monkeypatch, capsys):
    """--trusted-key must pin verification to the expected fingerprint."""
    monkeypatch.setattr(seal.timestamping, "stamp_data", lambda data, path: None)

    fits_path = make_fits(tmp_path / "sealed.fit", lights=5, exptime=60.0)
    seal.seal_fits(fits_path, keys_dir=None, object_name="M31")

    keys_dir = tmp_path / ".astroprov" / "keys"
    fingerprint = signature.get_public_key_fingerprint(keys_dir)

    assert verify.verify_output(str(fits_path), trusted_key=fingerprint) == 0
    assert verify.verify_output(str(fits_path), trusted_key="0" * 16) == 1
    assert "The key fingerprint is not trusted!" in capsys.readouterr().out
