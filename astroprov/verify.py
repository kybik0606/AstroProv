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

"""Verify the AstroProv provenance chain for a stacked FITS output."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

from astropy.io import fits
from tqdm import tqdm

from astroprov import signature, fits_prov, timestamping, snapshot
from astroprov.utils import sha256_file


def read_json(path: Path | str) -> dict:
    """Read a JSON file, tolerating a UTF-8 BOM."""
    path = Path(path).resolve()
    with path.open("r", encoding="utf-8-sig") as stream:
        return json.load(stream)


def resolve_recorded_path(value: str, base: Path | str) -> Path:
    """Resolve a recorded relative path against base, rejecting traversal."""
    base = Path(base).resolve()
    path = Path(value)
    if not path.is_absolute():
        path = base / path
    resolved = path.resolve()
    try:
        resolved.relative_to(base)
    except ValueError as exc:
        raise ValueError(
            f"Recorded path escapes the base directory: {value!r}"
        ) from exc
    return resolved


class Verification:
    def __init__(self) -> None:
        self.errors: list[str] = []
        self.checks: list[str] = []

    def ok(self, message: str) -> None:
        self.checks.append(message)

    def fail(self, message: str) -> None:
        self.errors.append(message)

    def check_hash(
        self,
        path: Path | str,
        expected: str,
        label: str,
        quiet: bool = False,
    ) -> bool:
        if not isinstance(expected, str) or not expected:
            self.fail(f"{label}: missing or invalid expected SHA-256")
            return False

        path = Path(path).resolve()
        if not path.is_file():
            self.fail(f"{label}: file not found: {path}")
            return False

        actual = sha256_file(path)
        if actual.lower() != expected.lower():
            self.fail(f"{label}: SHA-256 does NOT match: {path}")
            return False

        if not quiet:
            self.ok(f"{label}: SHA-256 matches")
        return True


def _read_fits_passport(output: Path, verifier: Verification) -> dict:
    passport: dict = {
        "version": None,
        "id": None,
        "report_hash": None,
        "signature": None,
        "pubkey_fp": None,
        "merkle_root": None,
        "merkle_algorithm": None,
        "date": None,
        "tool": None,
        "schema": None,
        "lights": None,
        "darks": None,
        "biases": None,
        "flats": None,
        "exptime": None,
    }

    try:
        with fits.open(output, mode="readonly") as hdul:
            if "PROVENANCE" not in hdul:
                verifier.fail(
                    "FITS is missing the passport HDU (PROVENANCE)!"
                )
                return passport

            hdr = hdul["PROVENANCE"].header
            passport["version"] = hdr.get("PROVVER")
            passport["id"] = hdr.get("PROVID")
            passport["schema"] = hdr.get("PROVTYPE")
            passport["report_hash"] = hdr.get("PROVHASH")
            passport["pubkey_fp"] = hdr.get("PROVPUBK")
            passport["merkle_root"] = hdr.get("PROVMRKL")
            passport["merkle_algorithm"] = hdr.get("PROVMALG")
            passport["date"] = hdr.get("PROVDATE")
            passport["tool"] = hdr.get("PROVTOOL")
            passport["lights"] = hdr.get("PROVLIT")
            passport["darks"] = hdr.get("PROVDRK")
            passport["biases"] = hdr.get("PROVBIA")
            passport["flats"] = hdr.get("PROVFLT")
            passport["exptime"] = hdr.get("PROVEXP")

            sig1 = str(hdr.get("PROVSIG1", ""))
            sig2 = str(hdr.get("PROVSIG2", ""))
            sig3 = str(hdr.get("PROVSIG3", ""))
            passport["signature"] = (sig1 + sig2 + sig3).strip()

            verifier.ok(f"Passport found in FITS. ID: {passport['id']}")
    except (OSError, ValueError, TypeError) as exc:
        verifier.fail(f"Error reading FITS passport: {exc}")

    return passport


def _find_report(output: Path) -> Path:
    matches: list[Path] = []
    
    # Search for reports in the project root (.astroprov) and next to the file (process/.astroprov)
    for root in (output.parent.parent, output.parent):
        runs_dir = root / ".astroprov" / "runs"
        if not runs_dir.is_dir():
            continue

        for candidate in runs_dir.glob("processing-*.json"):
            try:
                report = read_json(candidate)
                recorded = report.get("output_fits", {}).get("path")
                if recorded and Path(recorded).resolve() == output:
                    matches.append(candidate)
            except (OSError, ValueError, TypeError):
                continue

    if not matches:
        raise FileNotFoundError(f"No processing report found for {output}")

    # Return the NEWEST report (by file modification time)
    return max(matches, key=lambda p: p.stat().st_mtime)


def _verify_signature(
    output: Path,
    report_path: Path,
    passport: dict,
    verifier: Verification,
) -> None:
    fits_sig = passport["signature"]
    fits_report_hash = passport["report_hash"]

    if not (fits_sig and fits_report_hash):
        verifier.fail(
            "Passport is missing digital signature (PROVSIG or PROVHASH)."
        )
        return

    current_report_hash = hashlib.sha256(report_path.read_bytes()).hexdigest()
    if current_report_hash != fits_report_hash:
        verifier.fail(
            "JSON report hash does not match the one recorded in FITS "
            "(report was modified)!"
        )
    else:
        verifier.ok("JSON report hash matches the one recorded in FITS")

    keys_dir = output.parent / ".astroprov" / "keys"
    if not keys_dir.is_dir():
        keys_dir = output.parent.parent / ".astroprov" / "keys"

    actual_fp = signature.get_public_key_fingerprint(keys_dir)
    legacy_fp = signature.get_legacy_public_key_fingerprint(keys_dir)
    passport_fp = str(passport.get("pubkey_fp") or "").strip().lower()

    if not passport_fp:
        verifier.fail(
            "Passport is missing the public key fingerprint (PROVPUBK)."
        )
        return

    # Accept the legacy PEM-based fingerprint only so passports created
    # before v1.4.4 remain verifiable. New passports use the DER fingerprint.
    if passport_fp not in {actual_fp.lower(), legacy_fp.lower()}:
        verifier.fail(
            "Passport fingerprint does not match the actual public key "
            "used to verify!"
        )
        return

    version = passport.get("version")

    if version == "1.2":
        # v1.2 binds the Merkle algorithm to the signature as well.
        canonical_dict = {
            "version": passport["version"],
            "id": passport["id"],
            "merkle_root": passport["merkle_root"],
            "merkle_algorithm": passport["merkle_algorithm"],
            "date": passport["date"],
            "tool": passport["tool"],
            "schema": passport["schema"],
            "lights_count": passport["lights"],
            "darks_count": passport["darks"],
            "biases_count": passport["biases"],
            "flats_count": passport["flats"],
            "exptime": passport["exptime"],
            "pubkey_fingerprint": passport["pubkey_fp"],
            "report_hash": passport["report_hash"],
        }
        sig_bytes = signature.canonical_payload(canonical_dict)
    elif version == "1.1":
        # v1.1: full passport signing without the Merkle algorithm field.
        canonical_dict = {
            "version": passport["version"],
            "id": passport["id"],
            "merkle_root": passport["merkle_root"],
            "date": passport["date"],
            "tool": passport["tool"],
            "schema": passport["schema"],
            "lights_count": passport["lights"],
            "darks_count": passport["darks"],
            "biases_count": passport["biases"],
            "flats_count": passport["flats"],
            "exptime": passport["exptime"],
            "pubkey_fingerprint": passport["pubkey_fp"],
            "report_hash": passport["report_hash"],
        }
        sig_bytes = signature.canonical_payload(canonical_dict)
    else:
        # OLD VERSION (1.0): Backward compatibility for legacy files
        sig_payload = {
            "report_hash": fits_report_hash,
            "id": passport["id"],
            "merkle_root": passport["merkle_root"],
            "lights_count": passport["lights"],
            "darks_count": passport["darks"],
            "biases_count": passport["biases"],
            "flats_count": passport["flats"],
            "exptime": passport["exptime"],
        }
        sig_bytes = json.dumps(
            sig_payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")

    if signature.verify_signature(sig_bytes, fits_sig, keys_dir):
        verifier.ok(
            f"Ed25519 signature is valid (FP: {passport['pubkey_fp']})"
        )
    else:
        verifier.fail(
            "Digital signature is INVALID! Signature file does not match "
            "the key or data was tampered with."
        )


def _verify_timestamp(
    report_path: Path,
    verifier: Verification,
) -> None:
    """Verify the FreeTSA trusted timestamp if present."""
    tsr_path = report_path.with_suffix(".tsr")
    if not tsr_path.is_file():
        verifier.ok("No trusted timestamp found (optional)")
        return

    try:
        report_bytes = report_path.read_bytes()
        if timestamping.verify_data(report_bytes, tsr_path):
            verifier.ok("Trusted timestamp (RFC 3161) is valid")
        else:
            verifier.fail("Trusted timestamp (RFC 3161) is INVALID!")
    except Exception as exc:
        verifier.fail(f"Error verifying timestamp: {exc}")


LEGACY_PASSPORT_VERSIONS = {"1.0", "1.1"}


def _verify_passport_schema(
    passport: dict,
    report_schema: str,
    verifier: Verification,
) -> None:
    """Ensure PROVTYPE in the FITS passport matches the JSON report schema."""
    passport_schema = passport.get("schema")
    version = str(passport.get("version") or "")

    if not passport_schema:
        if version in LEGACY_PASSPORT_VERSIONS:
            verifier.ok("Passport has no PROVTYPE (legacy passport)")
        else:
            verifier.fail("Passport is missing the PROVTYPE field")
        return

    if passport_schema == report_schema:
        verifier.ok(f"Passport schema matches the report ({report_schema})")
        return

    if (
        version in LEGACY_PASSPORT_VERSIONS
        and passport_schema == "astroprov-standalone-v1"
        and report_schema == "astroprov-processing-v2"
    ):
        # Old pipeline builds left PROVTYPE at its standalone default.
        verifier.ok("Passport schema is the legacy pipeline default")
        return

    verifier.fail(
        f"Passport schema mismatch! FITS: {passport_schema}, "
        f"report: {report_schema}"
    )


def _manifest_source_data(manifest: dict) -> dict:
    """Extract category counts and Merkle roots from a manifest."""
    categories = manifest.get("categories")
    if not isinstance(categories, dict):
        return {}

    source_data = {}
    for name, category in categories.items():
        if isinstance(category, dict):
            source_data[name] = {
                "file_count": category.get("file_count", 0),
                "merkle_root": category.get("merkle_root"),
            }
    return source_data


def _verify_source_manifest(
    report: dict,
    output: Path,
    passport: dict,
    verifier: Verification,
) -> dict | None:
    """Verify the hash and passport ID of the recorded source manifest."""
    info = report.get("source_manifest")

    if info is None:
        verifier.ok("Source manifest is not recorded (legacy report)")
        return None

    if not isinstance(info, dict):
        verifier.fail("Report has invalid source_manifest section")
        return None

    path_value = info.get("path")
    if not path_value:
        verifier.fail("Report is missing the source manifest path")
        return None

    manifest_path = Path(path_value)
    if not manifest_path.is_absolute():
        manifest_path = output.parent.parent / manifest_path
    manifest_path = manifest_path.resolve()

    if not verifier.check_hash(
        manifest_path, info.get("sha256", ""), "Source manifest"
    ):
        return None

    try:
        manifest = read_json(manifest_path)
    except (OSError, ValueError, TypeError) as exc:
        verifier.fail(f"Could not read source manifest: {exc}")
        return None

    if not isinstance(manifest, dict):
        verifier.fail("Source manifest must be a JSON object")
        return None

    manifest_id = manifest.get("passport_id")
    if manifest_id != passport.get("id"):
        verifier.fail(
            f"Manifest passport ID mismatch! FITS: {passport.get('id')}, "
            f"manifest: {manifest_id}"
        )
    else:
        verifier.ok("Source manifest passport ID matches the FITS passport")

    return manifest


def _manifest_from_report(report: dict, output: Path) -> dict | None:
    """Load the source manifest referenced by a report, if available."""
    info = report.get("source_manifest")
    if not isinstance(info, dict) or not info.get("path"):
        return None

    manifest_path = Path(info["path"])
    if not manifest_path.is_absolute():
        manifest_path = output.parent.parent / manifest_path

    try:
        manifest = read_json(manifest_path.resolve())
    except (OSError, ValueError, TypeError):
        return None

    return manifest if isinstance(manifest, dict) else None


def _expected_source_data(
    report: dict,
    output: Path,
) -> tuple[dict, str]:
    """Return the expected category roots and the Merkle algorithm to use."""
    algorithm = report.get("merkle_algorithm")
    source_data = report.get("source_data")

    if (
        not isinstance(source_data, dict)
        or not source_data
        or not algorithm
    ):
        # Backward compatibility: older pipeline reports only stored the
        # manifest, and new reports should always name the algorithm.
        manifest = _manifest_from_report(report, output)
        if manifest is not None:
            if not algorithm:
                algorithm = manifest.get("merkle_algorithm")
            if not isinstance(source_data, dict) or not source_data:
                source_data = _manifest_source_data(manifest)

    if not isinstance(source_data, dict):
        source_data = {}

    return source_data, algorithm or snapshot.MERKLE_V1


def _verify_source_data(
    report: dict,
    raw_dir: Path,
    output: Path,
    verifier: Verification,
) -> None:
    """Verify raw source categories against the Merkle roots in the report."""
    print(f"\n  [*] Verifying raw source data in {raw_dir.name}...")

    source_data, algorithm = _expected_source_data(report, output)

    if not source_data:
        verifier.fail("Report has no source_data to verify")
        return

    if algorithm not in (snapshot.MERKLE_V1, snapshot.MERKLE_V2):
        verifier.fail(f"Unsupported Merkle algorithm in report: {algorithm}")
        return

    if not raw_dir.is_dir():
        verifier.fail(f"Raw directory not found: {raw_dir}")
        return

    try:
        categories = snapshot.snapshot_directory(
            raw_dir, merkle_algorithm=algorithm
        )
    except (OSError, ValueError) as exc:
        verifier.fail(f"Failed to scan raw directory: {exc}")
        return

    for cat_name, expected_data in source_data.items():
        if not isinstance(expected_data, dict):
            verifier.fail(f"[{cat_name}] Invalid source_data entry")
            continue

        expected_root = expected_data.get("merkle_root")
        if not isinstance(expected_root, str) or not expected_root:
            verifier.fail(f"[{cat_name}] Missing Merkle root in the report")
            continue

        actual = categories.get(cat_name)
        if not actual:
            verifier.fail(f"Category '{cat_name}' missing from raw directory")
            continue

        if actual["merkle_root"] != expected_root:
            verifier.fail(
                f"[{cat_name}] Merkle root mismatch! "
                "Source files were modified."
            )
        else:
            verifier.ok(
                f"[{cat_name}] Source data matches "
                f"({actual['file_count']} files)"
            )


def _verify_collection_cross_check(
    report: dict,
    passport: dict,
    verifier: Verification,
) -> None:
    """Verify the collection lights count against the signed passport.

    The report is authenticated by its hash, so ``rejected_lights`` can no
    longer be used as a loophole: the count recorded in ``cross_check`` must
    match the ``PROVLIT`` value that is covered by the Ed25519 signature.
    """
    cross = report.get("cross_check")

    if cross is None:
        verifier.ok("Collection report has no cross_check (legacy report)")
        return

    if not isinstance(cross, dict):
        verifier.fail("Report has invalid cross_check section")
        return

    fits_lights = cross.get("fits_lights")
    raw_lights = cross.get("raw_lights")

    if not isinstance(fits_lights, int) or not isinstance(raw_lights, int):
        verifier.fail("cross_check is missing integer lights counts")
        return

    if fits_lights < 0 or raw_lights < 0:
        verifier.fail("cross_check contains negative lights counts")
        return

    passport_lights = passport.get("lights")

    if not isinstance(passport_lights, int):
        verifier.fail("Passport is missing the PROVLIT lights count")
        return

    if fits_lights != passport_lights:
        verifier.fail(
            f"cross_check lights mismatch! "
            f"FITS PROVLIT: {passport_lights}, "
            f"report fits_lights: {fits_lights}"
        )
    else:
        verifier.ok(
            f"cross_check lights count matches PROVLIT ({passport_lights})"
        )


def _verify_passport_merkle_root(
    report: dict,
    passport: dict,
    verifier: Verification,
) -> None:
    """Verify that PROVMRKL matches the lights root from the report."""
    source_data = report.get("source_data")

    if not isinstance(source_data, dict) or not source_data:
        verifier.ok(
            "Passport Merkle root is not cross-checked "
            "(report has no source_data)"
        )
        return

    lights = source_data.get("lights")

    if not isinstance(lights, dict):
        verifier.fail("Report source_data has no lights category")
        return

    expected_root = lights.get("merkle_root")
    passport_root = passport.get("merkle_root")

    if not isinstance(expected_root, str) or not expected_root:
        verifier.fail("Report source_data lights has no Merkle root")
        return

    if passport_root != expected_root:
        verifier.fail(
            f"Passport Merkle root mismatch! "
            f"FITS: {passport_root}, report: {expected_root}"
        )
    else:
        verifier.ok("Passport Merkle root matches the report source_data")


def _verify_passport_id(
    report: dict,
    passport: dict,
    is_collection: bool,
    is_standalone: bool,
    verifier: Verification,
) -> None:
    """Verify that PROVID matches the report passport_id when available."""
    if is_standalone:
        verifier.ok("Standalone passport verified")
        return

    report_prov_id = report.get("passport_id")

    if not report_prov_id:
        if is_collection:
            # Legacy collection reports did not record passport_id.
            verifier.ok(
                "Passport ID verification skipped for collection schema "
                "(report has no passport_id)"
            )
        else:
            verifier.fail(
                "JSON report is missing passport_id for comparison."
            )
        return

    if passport.get("id") != report_prov_id:
        verifier.fail(
            f"Passport ID mismatch! FITS: {passport.get('id')}, "
            f"JSON: {report_prov_id}"
        )
    else:
        verifier.ok("Passport ID in FITS matches ID in JSON report")


def _verify_mapping_frames(
    mapping_data: dict,
    mapping_root: Path,
    verifier: Verification,
) -> None:
    records = mapping_data.get("mappings", [])
    if not isinstance(records, list) or not records:
        verifier.fail("Mapping contains no records")
        return

    frame_errors_before = len(verifier.errors)
    checked_pairs = 0

    records_with_processed = [
        r for r in records
        if isinstance(r, dict) and (r.get("processed") or {}).get("path")
    ]
    total_pairs = len(records_with_processed)

    pbar = tqdm(
        total=total_pairs,
        bar_format="  {desc}",
        dynamic_ncols=True,
        disable=None,
    )

    for idx, record in enumerate(records_with_processed, 1):
        pbar.set_description_str(
            f"Frame check: {idx}/{total_pairs}", refresh=True
        )

        for key, label in (
            ("original", "Original frame"),
            ("processed", "Processed frame"),
        ):
            item = record.get(key) or {}
            if not isinstance(item, dict):
                verifier.fail(f"{label}: invalid entry")
                continue

            relative_path = item.get("path")
            expected_hash = item.get("sha256")

            if not relative_path or not expected_hash:
                verifier.fail(
                    f"Mapping is missing path or SHA-256: {key}"
                )
                continue

            try:
                frame_path = resolve_recorded_path(
                    relative_path, mapping_root
                )
            except ValueError as exc:
                verifier.fail(f"{label}: {exc}")
                continue

            verifier.check_hash(
                frame_path, expected_hash, label, quiet=True
            )

        checked_pairs += 1
        pbar.update(1)

    if total_pairs > 0:
        pbar.set_description_str(
            f"Frame check: {total_pairs}/{total_pairs} done", refresh=True
        )
    pbar.close()

    if len(verifier.errors) == frame_errors_before:
        verifier.ok(
            f"Hashes of all {checked_pairs} original/processed "
            "frame pairs verified"
        )

    recorded_matching = mapping_data.get("matching_count")
    if recorded_matching != len(records):
        verifier.fail(
            f"matching_count ({recorded_matching!r}) does not match "
            f"mapping record count ({len(records)})"
        )


def _verify_pipeline_artifacts(
    report: dict,
    output: Path,
    passport: dict,
    verifier: Verification,
) -> None:
    _verify_source_manifest(report, output, passport, verifier)

    sequence = report.get("sequence") or {}
    if not isinstance(sequence, dict):
        verifier.fail("Report has invalid sequence section")
        sequence = {}

    sequence_path = Path(sequence.get("path", ""))
    if not sequence_path.is_absolute():
        sequence_path = output.parent.parent / sequence_path

    if sequence.get("sha256"):
        verifier.check_hash(
            sequence_path, sequence["sha256"], "Sequence .seq"
        )
    else:
        verifier.fail("Report is missing .seq SHA-256")

    mapping_info = report.get("mapping_file") or {}
    if not isinstance(mapping_info, dict):
        verifier.fail("Report has invalid mapping_file section")
        mapping_info = {}

    mapping_path: Path | None = None
    mapping_path_value = mapping_info.get("path")
    if not mapping_path_value:
        verifier.fail("Report is missing the mapping path")
    else:
        mapping_path = Path(mapping_path_value)
        if not mapping_path.is_absolute():
            mapping_path = output.parent.parent / mapping_path
        mapping_path = mapping_path.resolve()
        verifier.check_hash(
            mapping_path, mapping_info.get("sha256", ""), "Mapping"
        )

    pre_info = report.get("pre_stack_report") or {}
    if not isinstance(pre_info, dict):
        verifier.fail("Report has invalid pre_stack_report section")
        pre_info = {}

    pre_path_value = pre_info.get("path")
    if not pre_path_value:
        verifier.fail("Pre-stack report is not specified")
    else:
        pre_path = Path(pre_path_value)
        if not pre_path.is_absolute():
            pre_path = output.parent.parent / pre_path
        pre_path = pre_path.resolve()
        verifier.check_hash(
            pre_path, pre_info.get("sha256", ""), "Pre-stack report"
        )

        try:
            pre_report = read_json(pre_path)
            if pre_report.get("schema") != "astroprov-pre-stack-v1":
                verifier.fail("Unknown pre-stack report schema")

            pre_status = pre_report.get("status")
            if pre_status not in ("ready", "validation_failed"):
                verifier.fail(
                    f"Pre-stack report has unknown status: {pre_status}"
                )
            elif pre_status == "validation_failed":
                verifier.ok(
                    "Pre-stack report has status validation_failed "
                    "(expected when frames are rejected)"
                )

            pre_seq = pre_report.get("sequence") or {}
            if pre_seq.get("sha256") != sequence.get("sha256"):
                if pre_seq.get("selected_indices") == sequence.get(
                    "selected_indices"
                ):
                    verifier.ok(
                        ".seq hash changed (Siril stacking), but frame "
                        "list is the same"
                    )
                else:
                    verifier.fail("Frame list in .seq has changed!")

            pre_mapping = pre_report.get("mapping_file") or {}
            if pre_mapping.get("sha256") != mapping_info.get("sha256"):
                verifier.fail("Mapping hash in reports does not match")

            if pre_info.get("verified") is not True:
                verifier.fail("Pre-stack report is not marked as verified")
            else:
                verifier.ok("Pre-stack report link verified")
        except (OSError, ValueError, TypeError) as exc:
            verifier.fail(f"Could not read pre-stack report: {exc}")

    mapping_data = None
    if mapping_path is not None and mapping_path.is_file():
        try:
            mapping_data = read_json(mapping_path)
        except (OSError, ValueError, TypeError) as exc:
            verifier.fail(f"Could not read mapping: {exc}")

    mapping_root = (
        mapping_path.parent if mapping_path else output.parent.parent
    )

    if mapping_data is not None:
        if mapping_data.get("schema") != "astroprov-frame-mapping-v1":
            verifier.fail("Unknown mapping schema")
        else:
            _verify_mapping_frames(mapping_data, mapping_root, verifier)

    cross = report.get("cross_check") or {}
    if not isinstance(cross, dict):
        verifier.fail("Report has invalid cross_check section")
        cross = {}

    selected_count = sequence.get("selected_count")
    excluded_count = sequence.get("excluded_count")
    total_count = sequence.get("total_frames")

    if not all(
        isinstance(v, int)
        for v in (selected_count, excluded_count, total_count)
    ):
        verifier.fail(
            "Frame counts in report are missing or not integers"
        )
    elif selected_count + excluded_count != total_count:
        verifier.fail("Selected and excluded frame counts do not add up")
    else:
        verifier.ok(
            f"Frame selection is consistent: {selected_count} selected, "
            f"{excluded_count} excluded"
        )

    if cross.get("consistent") is not True:
        verifier.fail("FITS frame count check failed")
    elif cross.get("selected_count") != selected_count:
        verifier.fail("Selected count in cross_check does not match")
    elif cross.get("fits_recorded_count") != selected_count:
        verifier.fail("FITS frame count does not match the selection")
    else:
        verifier.ok(f"FITS frame count consistent: {selected_count}")

    validation = report.get("mapping_validation") or {}
    if not isinstance(validation, dict):
        verifier.fail("Report has invalid mapping_validation section")
        validation = {}

    if validation.get("consistent") is not True:
        real_errors = [
            e for e in validation.get("errors", [])
            if "no mapping entry" not in e
        ]
        if real_errors:
            verifier.fail(f"Mapping validation failed: {real_errors}")
        else:
            verifier.ok(
                "Mapping validation: only missing frames (expected)"
            )
    elif validation.get("errors"):
        verifier.fail("Mapping validation contains errors")
    elif (
        validation.get("matched_frame_count") != total_count
        or validation.get("selected_mapping_count") != selected_count
        or validation.get("excluded_mapping_count") != excluded_count
        or validation.get("missing_sequence_indices")
        or validation.get("extra_mapping_paths")
    ):
        verifier.fail(
            "Mapping frame counts or composition do not match"
        )
    else:
        verifier.ok("Mapping and sequence are consistent")


def verify_output(
    output_arg: str | Path,
    report_arg: str | Path | None = None,
    raw_dir_arg: str | Path | None = None,
    trusted_key: str | None = None,
) -> int:
    """Verify the full provenance chain for a stacked output FITS.

    If ``trusted_key`` is provided, the passport fingerprint must match it.
    This pins verification to a known public key (trust anchor).
    """
    verifier = Verification()
    output = Path(output_arg).resolve()

    print("\nASTROPROV FITS PROVENANCE VERIFICATION\n")
    print(f"  [*] Output file: {output.name}")

    if not output.is_file():
        print(f"\n  [FAIL] Output FITS not found: {output.name}")
        return 1

    try:
        if report_arg:
            report_path = Path(report_arg).resolve()
        else:
            report_path = _find_report(output)

        report = read_json(report_path)

        if not isinstance(report, dict):
            verifier.fail("Processing report must be a JSON object")
            report = {}

        passport = _read_fits_passport(output, verifier)

        if trusted_key:
            trusted_fp = str(trusted_key).strip().lower()
            passport_fp = str(passport.get("pubkey_fp") or "").strip().lower()

            if passport_fp != trusted_fp:
                verifier.fail("The key fingerprint is not trusted!")
            else:
                verifier.ok(f"Passport fingerprint is trusted ({passport_fp})")

        schema = report.get("schema")
        is_standalone = schema == "astroprov-standalone-v1"
        is_collection = schema == "astroprov-collection-v1"

        if schema not in (
            "astroprov-processing-v2",
            "astroprov-standalone-v1",
            "astroprov-collection-v1",
        ):
            verifier.fail("Unknown processing report schema")
        elif not (is_standalone or is_collection) and report.get("stage") != "post-stack":
            verifier.fail("Report is not for the post-stack stage")
        else:
            verifier.ok("Report type and stage verified")

            if isinstance(schema, str):
                _verify_passport_schema(passport, schema, verifier)

            _verify_passport_id(
                report,
                passport,
                is_collection,
                is_standalone,
                verifier,
            )

        _verify_signature(output, report_path, passport, verifier)
        
        if passport["report_hash"]:
            _verify_timestamp(report_path, verifier)

        recorded_output = report.get("output_fits") or {}
        if not isinstance(recorded_output, dict):
            verifier.fail("Report has invalid output_fits section")
            recorded_output = {}

        report_output_path = recorded_output.get("path")
        if not report_output_path:
            verifier.fail("Report is missing the output FITS path")
        elif Path(report_output_path).resolve() != output:
            verifier.fail("Report refers to a different output FITS")

        expected_data_hash = recorded_output.get("sha256")
        if expected_data_hash:
            actual_data_hash = fits_prov.calculate_fits_data_hash(output)
            if actual_data_hash == expected_data_hash:
                verifier.ok(
                    "Output FITS (data without passport): SHA-256 matches"
                )
            else:
                verifier.fail(
                    "Output FITS: data SHA-256 does NOT match!"
                )
        else:
            verifier.fail("Report is missing the output FITS SHA-256")

        if not (is_standalone or is_collection):
            _verify_pipeline_artifacts(report, output, passport, verifier)

        if not is_standalone:
            _verify_passport_merkle_root(report, passport, verifier)

        if is_collection:
            _verify_collection_cross_check(report, passport, verifier)

        if raw_dir_arg:
            _verify_source_data(
                report,
                Path(raw_dir_arg).resolve(),
                output,
                verifier,
            )
        elif not is_standalone:
            print(
                "\n  [WARN] Raw sources not provided. "
                "Self-contained verification only."
            )

        if report.get("warnings"):
            print("\n  [WARN] Processing report has warnings:")
            for w in report["warnings"]:
                print(f"    - {w}")

        print(f"\n  [*] Report: {report_path.name}")
        print("\n  [*] Verification Results:\n")

        for message in verifier.checks:
            print(f"  [OK] {message}")

        for message in verifier.errors:
            print(f"  [FAIL] {message}")

        if verifier.errors:
            print("\n  [FAIL] VERDICT: FAIL — chain verification failed.")
            print(
                "         One or more integrity, consistency, or signature "
                "checks failed."
            )
            return 1

        print("\n  [OK] VERDICT: PASS — chain verified.")
        print(
            "       Integrity, consistency, and Ed25519 signature "
            "are all valid."
        )
        return 0

    except (OSError, ValueError, TypeError, KeyError, AttributeError, IndexError) as exc:
        print(f"\n  [FAIL] Error reading or verifying data: {exc}")
        return 1


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Verify an AstroProv FITS provenance chain."
    )
    parser.add_argument(
        "output_fits", help="Path to the stacked output FITS"
    )
    parser.add_argument(
        "--report",
        help="Explicit processing report path; otherwise auto-detected",
    )
    parser.add_argument(
        "--raw-dir",
        help="Optional path to raw source folder for deep verification",
    )
    parser.add_argument(
        "--trusted-key",
        help="Expected passport key fingerprint (16 hex chars) to pin",
    )
    args = parser.parse_args()
    return verify_output(
        args.output_fits,
        args.report,
        args.raw_dir,
        trusted_key=args.trusted_key,
    )


if __name__ == "__main__":
    sys.exit(main())