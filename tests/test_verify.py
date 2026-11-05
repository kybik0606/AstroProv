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

"""Integration tests for astroprov.verify with signed pipeline artifacts."""

import json
from pathlib import Path

from astropy.io import fits

from astroprov import fits_prov, signature, snapshot, verify
from astroprov.utils import sha256_file


# ---------------------------------------------------------------- helpers ---

def _write_fits(path: Path, **headers) -> Path:
    """Create a small FITS file with the given header values."""
    hdu = fits.PrimaryHDU()
    for key, value in headers.items():
        hdu.header[key] = value
    path.parent.mkdir(parents=True, exist_ok=True)
    hdu.writeto(path, overwrite=True)
    return path


def _build_pipeline_artifact(root: Path) -> dict:
    """Create a minimal but fully consistent signed pipeline artifact."""
    object_dir = root / "M31"
    (object_dir / "lights").mkdir(parents=True)
    (object_dir / "darks").mkdir()
    (object_dir / "process").mkdir()

    light = _write_fits(object_dir / "lights" / "light1.fit", EXPTIME=10.0)
    _write_fits(object_dir / "darks" / "dark1.fit", EXPTIME=10.0)
    processed = _write_fits(
        object_dir / "process" / "r_light1.fit", EXPTIME=10.0
    )
    output = _write_fits(
        object_dir / "process" / "r_pp_light__stacked.fit",
        EXPTIME=10.0,
        STACKCNT=1,
    )

    sequence = object_dir / "process" / "r_pp_light_.seq"
    sequence.write_text(
        "S 'r_pp_light_'\nI 1 1\nI 2 0\n", encoding="utf-8"
    )

    manifest_path = snapshot.create_manifest(object_dir)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    passport_id = manifest["passport_id"]
    categories = manifest["categories"]

    mapping_path = object_dir / "astroprov_frame_mapping.json"
    mapping = {
        "schema": "astroprov-frame-mapping-v1",
        "mappings": [
            {
                "original": {
                    "path": "lights/light1.fit",
                    "sha256": sha256_file(light),
                },
                "processed": {
                    "path": "process/r_light1.fit",
                    "sha256": sha256_file(processed),
                },
            }
        ],
        "matching_count": 1,
    }
    mapping_path.write_text(
        json.dumps(mapping) + "\n", encoding="utf-8"
    )

    sequence_sha = sha256_file(sequence)
    mapping_sha = sha256_file(mapping_path)

    pre_stack_path = (
        object_dir / ".astroprov" / "runs" / "pre-stack-test.json"
    )
    pre_stack = {
        "schema": "astroprov-pre-stack-v1",
        "status": "ready",
        "sequence": {"sha256": sequence_sha, "selected_indices": [1]},
        "mapping_file": {"sha256": mapping_sha},
    }
    pre_stack_path.parent.mkdir(parents=True, exist_ok=True)
    pre_stack_path.write_text(
        json.dumps(pre_stack) + "\n", encoding="utf-8"
    )

    source_data = {
        name: {
            "file_count": category.get("file_count", 0),
            "merkle_root": category.get("merkle_root"),
        }
        for name, category in categories.items()
    }

    report = {
        "schema": "astroprov-processing-v2",
        "stage": "post-stack",
        "created_utc": "2026-01-01T00:00:00+00:00",
        "passport_id": passport_id,
        "merkle_algorithm": manifest["merkle_algorithm"],
        "sequence": {
            "path": str(sequence),
            "sha256": sequence_sha,
            "size_bytes": sequence.stat().st_size,
            "name": "r_pp_light_",
            "total_frames": 2,
            "selected_count": 1,
            "excluded_count": 1,
            "selected_indices": [1],
            "excluded_indices": [2],
        },
        "output_fits": {
            "path": str(output),
            "sha256": fits_prov.calculate_fits_data_hash(output),
            "size_bytes": output.stat().st_size,
        },
        "cross_check": {
            "selected_count": 1,
            "fits_count_field": "STACKCNT",
            "fits_recorded_count": 1,
            "consistent": True,
        },
        "mapping_file": {
            "path": str(mapping_path),
            "sha256": mapping_sha,
            "size_bytes": mapping_path.stat().st_size,
        },
        "mapping_validation": {
            "consistent": True,
            "errors": [],
            "matched_frame_count": 2,
            "selected_mapping_count": 1,
            "excluded_mapping_count": 1,
            "missing_sequence_indices": [],
            "extra_mapping_paths": [],
        },
        "source_manifest": {
            "path": str(manifest_path),
            "sha256": sha256_file(manifest_path),
            "size_bytes": manifest_path.stat().st_size,
        },
        "source_data": source_data,
        "pre_stack_report": {
            "path": str(pre_stack_path),
            "sha256": sha256_file(pre_stack_path),
            "schema": "astroprov-pre-stack-v1",
            "verified": True,
        },
        "stack_command": "stack r_pp_light_ rej 3 3 -nonorm",
        "warnings": [],
        "limitations": [],
    }

    report_path = (
        object_dir / ".astroprov" / "runs" / "processing-test.json"
    )
    report_path.write_text(
        json.dumps(report, indent=2) + "\n", encoding="utf-8"
    )

    keys_dir = object_dir / ".astroprov" / "keys"
    signature.generate_keys(keys_dir)
    pubkey_fp = signature.get_public_key_fingerprint(keys_dir)

    prov_data = {
        "version": "1.2",
        "id": passport_id,
        "merkle_root": categories["lights"]["merkle_root"],
        "merkle_algorithm": manifest["merkle_algorithm"],
        "date": report["created_utc"],
        "tool": "AstroProv",
        "schema": "astroprov-processing-v2",
        "lights_count": categories["lights"]["file_count"],
        "darks_count": categories["darks"]["file_count"],
        "biases_count": 0,
        "flats_count": 0,
        "exptime": 10.0,
        "pubkey_fingerprint": pubkey_fp,
        "report_hash": sha256_file(report_path),
    }
    prov_data["signature"] = signature.sign_data(
        signature.canonical_payload(prov_data), keys_dir
    )
    fits_prov.write_provenance(output, prov_data)

    return {
        "object_dir": object_dir,
        "output": output,
        "report_path": report_path,
        "manifest_path": manifest_path,
        "light": light,
    }


# ------------------------------------------------------------------ tests ---

def test_pipeline_artifact_verifies(tmp_path):
    artifact = _build_pipeline_artifact(tmp_path)

    assert verify.verify_output(
        str(artifact["output"]), str(artifact["report_path"])
    ) == 0


def test_verify_fails_on_manifest_mismatch(tmp_path, capsys):
    artifact = _build_pipeline_artifact(tmp_path)

    manifest_path = artifact["manifest_path"]
    manifest_path.write_text(
        manifest_path.read_text(encoding="utf-8") + " ",
        encoding="utf-8",
    )

    assert verify.verify_output(
        str(artifact["output"]), str(artifact["report_path"])
    ) == 1
    assert "Source manifest" in capsys.readouterr().out


def test_verify_fails_if_raw_dir_modified(tmp_path, capsys):
    artifact = _build_pipeline_artifact(tmp_path)

    assert verify.verify_output(
        str(artifact["output"]),
        str(artifact["report_path"]),
        str(artifact["object_dir"]),
    ) == 0

    with artifact["light"].open("ab") as stream:
        stream.write(b"\0")

    assert verify.verify_output(
        str(artifact["output"]),
        str(artifact["report_path"]),
        str(artifact["object_dir"]),
    ) == 1
    assert "[lights] Merkle root mismatch" in capsys.readouterr().out


def test_verify_fails_if_raw_dir_is_missing(tmp_path, capsys):
    artifact = _build_pipeline_artifact(tmp_path)

    assert verify.verify_output(
        str(artifact["output"]),
        str(artifact["report_path"]),
        str(tmp_path / "does-not-exist"),
    ) == 1
    assert "Raw directory not found" in capsys.readouterr().out


def test_passport_schema_mismatch_is_reported():
    verifier = verify.Verification()

    verify._verify_passport_schema(
        {"schema": "astroprov-standalone-v1", "version": "1.2"},
        "astroprov-processing-v2",
        verifier,
    )

    assert verifier.errors


def test_collection_cross_check_rejects_inconsistent_counts():
    verifier = verify.Verification()

    verify._verify_collection_cross_check(
        {
            "cross_check": {
                "fits_lights": 5,
                "raw_lights": 6,
                "rejected_lights": 0,
            }
        },
        verifier,
    )

    assert verifier.errors
