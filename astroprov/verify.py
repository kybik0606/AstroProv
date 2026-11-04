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
        "id": None,
        "report_hash": None,
        "signature": None,
        "pubkey_fp": None,
        "merkle_root": None,
        "lights": None,
        "darks": None,
        "biases": None,
        "flats": None,
        "exptime": None,
        "schema": None,
    }

    try:
        with fits.open(output, mode="readonly") as hdul:
            if "PROVENANCE" not in hdul:
                verifier.fail(
                    "FITS is missing the passport HDU (PROVENANCE)!"
                )
                return passport

            hdr = hdul["PROVENANCE"].header
            passport["id"] = hdr.get("PROVID")
            passport["schema"] = hdr.get("PROVTYPE")
            passport["report_hash"] = hdr.get("PROVHASH")
            passport["pubkey_fp"] = hdr.get("PROVPUBK")
            passport["merkle_root"] = hdr.get("PROVMRKL")
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
    
    # Ищем отчеты в корне проекта (M101/.astroprov) и рядом с файлом (M101/process/.astroprov)
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

    # Возвращаем САМЫЙ СВЕЖИЙ отчет (по времени изменения файла)
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


def _verify_source_data(
    report: dict,
    raw_dir: Path,
    verifier: Verification,
) -> None:
    """Verify that raw source files match the Merkle roots in the report."""
    print(f"\n  [*] Verifying raw source data in {raw_dir.name}...")
    if not raw_dir.is_dir():
        verifier.fail(f"Raw directory not found: {raw_dir}")
        return

    try:
        categories = snapshot.snapshot_directory(raw_dir)
    except Exception as exc:
        verifier.fail(f"Failed to scan raw directory: {exc}")
        return

    source_data = report.get("source_data", {})
    
    for cat_name, expected_data in source_data.items():
        actual = categories.get(cat_name)
        if not actual:
            verifier.fail(f"Category '{cat_name}' missing from raw directory.")
            continue
        
        if actual["merkle_root"] != expected_data["merkle_root"]:
            verifier.fail(f"[{cat_name}] Merkle root mismatch! Source files were modified.")
        else:
            verifier.ok(f"[{cat_name}] Source data matches ({actual['file_count']} files)")


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
        if isinstance(r, dict) and r.get("processed", {}).get("path")
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
    verifier: Verification,
) -> None:
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
) -> int:
    """Verify the full provenance chain for a stacked output FITS."""
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

        passport = _read_fits_passport(output, verifier)

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

            if not is_standalone:
                report_prov_id = report.get("passport_id")
                if not report_prov_id and not is_collection:
                    verifier.fail(
                        "JSON report is missing passport_id for comparison."
                    )
                elif not is_collection and passport["id"] != report_prov_id:
                    verifier.fail(
                        f"Passport ID mismatch! FITS: {passport['id']}, "
                        f"JSON: {report_prov_id}"
                    )
                else:
                    verifier.ok(
                        "Passport ID in FITS matches ID in JSON report"
                    )
            else:
                verifier.ok("Standalone passport verified")

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
            _verify_pipeline_artifacts(report, output, verifier)
            
        if is_collection and raw_dir_arg:
            _verify_source_data(report, Path(raw_dir_arg).resolve(), verifier)

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

    except (OSError, ValueError, TypeError, KeyError) as exc:
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
    args = parser.parse_args()
    return verify_output(args.output_fits, args.report, args.raw_dir)


if __name__ == "__main__":
    sys.exit(main())