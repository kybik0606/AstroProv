from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

from astropy.io import fits
from tqdm import tqdm

from astroprov import signature, fits_prov


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_json(path: Path) -> dict:
    with path.open("r", encoding="utf-8-sig") as stream:
        return json.load(stream)


def resolve_recorded_path(value: str, base: Path) -> Path:
    path = Path(value)
    if not path.is_absolute():
        path = base / path
    return path.resolve()


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
        path: Path,
        expected: str,
        label: str,
        quiet: bool = False,
    ) -> bool:
        if not path.is_file():
            self.fail(f"{label}: file not found: {path}")
            return False

        actual = sha256_file(path)
        if actual.lower() != str(expected).lower():
            self.fail(f"{label}: SHA-256 does NOT match: {path}")
            return False

        if not quiet:
            self.ok(f"{label}: SHA-256 matches")
        return True


def find_processing_report(output: Path, root: Path) -> Path:
    reports_dir = root / ".astroprov" / "runs"
    if not reports_dir.is_dir():
        raise FileNotFoundError(f"Reports directory not found: {reports_dir}")

    matches = []
    for candidate in reports_dir.glob("processing-*.json"):
        try:
            report = read_json(candidate)
            recorded = report.get("output_fits", {}).get("path")
            if recorded and Path(recorded).resolve() == output.resolve():
                matches.append(candidate)
        except (OSError, ValueError, TypeError):
            continue

    if not matches:
        raise FileNotFoundError(f"No processing report found for {output}")

    return max(matches, key=lambda p: p.stat().st_mtime)


def verify_output(output_arg: str, report_arg: str | None = None) -> int:
    verifier = Verification()
    output = Path(output_arg).resolve()

    print("\nAstroProv — FITS provenance verification\n")
    print(f"Output file: {output.name}")

    if not output.is_file():
        print(f"\nFAIL: output FITS not found: {output}")
        return 1

    try:
        if report_arg:
            report_path = Path(report_arg).resolve()
        else:
            root = output.parent.parent
            report_path = find_processing_report(output, root)

        report = read_json(report_path)

        # --- READ PASSPORT FROM FITS (HDU PROVENANCE) ---
        fits_prov_id = None
        fits_report_hash = None
        fits_sig = None
        fits_pubkey_fp = None
        fits_merkle = None
        fits_lights = None
        fits_darks = None
        fits_biases = None
        fits_flats = None
        fits_exptime = None

        try:
            with fits.open(output, mode="readonly") as hdul:
                if "PROVENANCE" not in hdul:
                    verifier.fail(
                        "FITS is missing the passport HDU (PROVENANCE)!"
                    )
                else:
                    hdr = hdul["PROVENANCE"].header
                    fits_prov_id = hdr.get("PROVID")
                    fits_report_hash = hdr.get("PROVHASH")
                    fits_pubkey_fp = hdr.get("PROVPUBK")
                    fits_merkle = hdr.get("PROVMRKL")
                    fits_lights = hdr.get("PROVLIT")
                    fits_darks = hdr.get("PROVDRK")
                    fits_biases = hdr.get("PROVBIA")
                    fits_flats = hdr.get("PROVFLT")
                    fits_exptime = hdr.get("PROVEXP")

                    # Reassemble the signature from 3 parts
                    sig1 = str(hdr.get("PROVSIG1", ""))
                    sig2 = str(hdr.get("PROVSIG2", ""))
                    sig3 = str(hdr.get("PROVSIG3", ""))
                    fits_sig = (sig1 + sig2 + sig3).strip()

                    verifier.ok(
                        f"Passport found in FITS. ID: {fits_prov_id}"
                    )
        except Exception as e:
            verifier.fail(f"Error reading FITS passport: {e}")

        # --- SCHEMA AND ID CHECK ---
        if report.get("schema") != "astroprov-processing-v2":
            verifier.fail("Unknown processing report schema")
        elif report.get("stage") != "post-stack":
            verifier.fail("Report is not for the post-stack stage")
        else:
            verifier.ok("Report type and stage verified")

            # Compare passport ID
            report_prov_id = report.get("passport_id")
            if not report_prov_id:
                verifier.fail(
                    "JSON report is missing passport_id for comparison."
                )
            elif fits_prov_id != report_prov_id:
                verifier.fail(
                    f"Passport ID mismatch! "
                    f"FITS: {fits_prov_id}, JSON: {report_prov_id}"
                )
            else:
                verifier.ok(
                    "Passport ID in FITS matches ID in JSON report"
                )

        # --- DIGITAL SIGNATURE (ED25519) CHECK ---
        if fits_sig and fits_report_hash:
            # 1. Verify that the JSON report has not been modified
            current_report_bytes = report_path.read_bytes()
            current_report_hash = hashlib.sha256(
                current_report_bytes
            ).hexdigest()

            if current_report_hash != fits_report_hash:
                verifier.fail(
                    "JSON report hash does not match the one recorded "
                    "in FITS (report was modified)!"
                )
            else:
                verifier.ok(
                    "JSON report hash matches the one recorded in FITS"
                )

            # 2. Verify the signature itself over the canonical payload
            keys_dir = output.parent.parent / ".astroprov" / "keys"
            
            sig_payload = {
                "report_hash": fits_report_hash,
                "id": fits_prov_id,
                "merkle_root": fits_merkle,
                "lights_count": fits_lights,
                "darks_count": fits_darks,
                "biases_count": fits_biases,
                "flats_count": fits_flats,
                "exptime": fits_exptime,
            }
            sig_bytes = json.dumps(
                sig_payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
            ).encode("utf-8")

            is_valid_sig = signature.verify_signature(
                sig_bytes,
                fits_sig,
                keys_dir
            )

            if is_valid_sig:
                verifier.ok(
                    f"Ed25519 signature is valid "
                    f"(FP: {fits_pubkey_fp})"
                )
            else:
                verifier.fail(
                    "Digital signature is INVALID! Signature file "
                    "does not match the key or data was tampered with."
                )
        else:
            verifier.fail(
                "Passport is missing digital signature "
                "(PROVSIG or PROVHASH)."
            )

        # --- HASHES OF OTHER ARTIFACTS ---
        recorded_output = report.get("output_fits", {})
        report_output_path = recorded_output.get("path")

        if not report_output_path:
            verifier.fail("Report is missing the output FITS path")
        elif Path(report_output_path).resolve() != output:
            verifier.fail("Report refers to a different output FITS")

        # IMPORTANT: use calculate_fits_data_hash, not sha256_file!
        if recorded_output.get("sha256"):
            actual_data_hash = fits_prov.calculate_fits_data_hash(output)
            if actual_data_hash == recorded_output["sha256"]:
                verifier.ok(
                    "Output FITS (data without passport): "
                    "SHA-256 matches"
                )
            else:
                verifier.fail(
                    "Output FITS: data SHA-256 does NOT match!"
                )
        else:
            verifier.fail(
                "Report is missing the output FITS SHA-256"
            )

        sequence = report.get("sequence", {})
        sequence_path = Path(sequence.get("path", ""))
        if not sequence_path.is_absolute():
            sequence_path = output.parent.parent / sequence_path

        if sequence.get("sha256"):
            verifier.check_hash(
                sequence_path,
                sequence["sha256"],
                "Sequence .seq",
            )
        else:
            verifier.fail("Report is missing .seq SHA-256")

        mapping_info = report.get("mapping_file", {})
        mapping_path_value = mapping_info.get("path")

        if not mapping_path_value:
            verifier.fail("Report is missing the mapping path")
            mapping_path = None
        else:
            mapping_path = Path(mapping_path_value)
            if not mapping_path.is_absolute():
                mapping_path = output.parent.parent / mapping_path
            mapping_path = mapping_path.resolve()
            verifier.check_hash(
                mapping_path,
                mapping_info.get("sha256", ""),
                "Mapping",
            )

        pre_info = report.get("pre_stack_report", {})
        pre_path_value = pre_info.get("path")

        if not pre_path_value:
            verifier.fail("Pre-stack report is not specified")
            pre_path = None
            pre_report = None
        else:
            pre_path = Path(pre_path_value)
            if not pre_path.is_absolute():
                pre_path = output.parent.parent / pre_path
            pre_path = pre_path.resolve()
            verifier.check_hash(
                pre_path,
                pre_info.get("sha256", ""),
                "Pre-stack report",
            )

            try:
                pre_report = read_json(pre_path)
                if pre_report.get("schema") != "astroprov-pre-stack-v1":
                    verifier.fail(
                        "Unknown pre-stack report schema"
                    )

                pre_status = pre_report.get("status")
                if pre_status not in ("ready", "validation_failed"):
                    verifier.fail(
                        "Pre-stack report has "
                        f"unknown status: {pre_status}"
                    )
                elif pre_status == "validation_failed":
                    verifier.ok(
                        "Pre-stack report has status "
                        "validation_failed (expected when frames are rejected)"
                    )

                pre_seq = pre_report.get("sequence", {})
                curr_seq = sequence

                if pre_seq.get("sha256") != curr_seq.get("sha256"):
                    if (
                        pre_seq.get("selected_indices")
                        == curr_seq.get("selected_indices")
                    ):
                        verifier.ok(
                            ".seq hash changed (Siril stacking), "
                            "but frame list is the same"
                        )
                    else:
                        verifier.fail(
                            "Frame list in .seq has changed!"
                        )

                if (
                    pre_report.get("mapping_file", {}).get("sha256")
                    != mapping_info.get("sha256")
                ):
                    verifier.fail(
                        "Mapping hash in reports does not match"
                    )

                if pre_info.get("verified") is not True:
                    verifier.fail(
                        "Pre-stack report is not marked "
                        "as verified"
                    )
                else:
                    verifier.ok(
                        "Pre-stack report link verified"
                    )

            except (OSError, ValueError, TypeError) as exc:
                verifier.fail(
                    f"Could not read pre-stack report: {exc}"
                )
                pre_report = None

        mapping_data = None
        if mapping_path is not None and mapping_path.is_file():
            try:
                mapping_data = read_json(mapping_path)
            except (OSError, ValueError, TypeError) as exc:
                verifier.fail(f"Could not read mapping: {exc}")

        mapping_root = (
            mapping_path.parent if mapping_path
            else output.parent.parent
        )

        if mapping_data is not None:
            if mapping_data.get("schema") != "astroprov-frame-mapping-v1":
                verifier.fail("Unknown mapping schema")
            else:
                records = mapping_data.get("mappings", [])
                if not isinstance(records, list) or not records:
                    verifier.fail("Mapping contains no records")
                else:
                    frame_errors_before = len(verifier.errors)
                    checked_pairs = 0

                    records_with_processed = [
                        r for r in records
                        if r.get("processed", {}).get("path")
                    ]
                    total_pairs = len(records_with_processed)

                    pbar = tqdm(
                        total=total_pairs,
                        bar_format="{desc}",
                        dynamic_ncols=True,
                        disable=None,
                    )

                    for idx, record in enumerate(
                        records_with_processed, 1
                    ):
                        pbar.set_description_str(
                            f"Frame check: {idx}/{total_pairs}",
                            refresh=True,
                        )

                        for key, label in (
                            ("original", "Original frame"),
                            ("processed", "Processed frame"),
                        ):
                            item = record.get(key, {})
                            relative_path = item.get("path")
                            expected_hash = item.get("sha256")

                            if not relative_path or not expected_hash:
                                verifier.fail(
                                    "Mapping is missing path or "
                                    f"SHA-256: {key}"
                                )
                                continue

                            frame_path = resolve_recorded_path(
                                relative_path, mapping_root
                            )
                            verifier.check_hash(
                                frame_path,
                                expected_hash,
                                label,
                                quiet=True,
                            )

                        checked_pairs += 1
                        pbar.update(1)

                    if total_pairs > 0:
                        pbar.set_description_str(
                            f"Frame check: "
                            f"{total_pairs}/{total_pairs} done",
                            refresh=True,
                        )
                    pbar.close()

                    if len(verifier.errors) == frame_errors_before:
                        verifier.ok(
                            "Hashes of all available original and "
                            "processed frames verified: "
                            f"{checked_pairs} pairs "
                            "(missing skipped)"
                        )

                    if mapping_data.get("matching_count") != len(records):
                        verifier.fail(
                            "matching_count does not match "
                            "mapping record count"
                        )

        cross = report.get("cross_check", {})
        selected_count = sequence.get("selected_count")
        excluded_count = sequence.get("excluded_count")
        total_count = sequence.get("total_frames")

        if selected_count + excluded_count != total_count:
            verifier.fail(
                "Selected and excluded frame counts do not add up"
            )
        else:
            verifier.ok(
                f"Frame selection is consistent: {selected_count} selected, "
                f"{excluded_count} excluded"
            )

        if cross.get("consistent") is not True:
            verifier.fail("FITS frame count check failed")
        elif cross.get("selected_count") != selected_count:
            verifier.fail(
                "Selected count in cross_check does not match"
            )
        elif cross.get("fits_recorded_count") != selected_count:
            verifier.fail(
                "FITS frame count does not match the selection"
            )
        else:
            verifier.ok(
                f"FITS frame count consistent: {selected_count}"
            )

        validation = report.get("mapping_validation", {})

        if validation.get("consistent") is not True:
            real_errors = [
                e for e in validation.get("errors", [])
                if "no mapping entry" not in e
            ]
            if real_errors:
                verifier.fail(
                    f"Mapping validation failed: {real_errors}"
                )
            else:
                verifier.ok(
                    "Mapping validation: only missing "
                    "frames (expected)"
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

        if report.get("warnings"):
            print("\n[INFO] Processing report has warnings:")
            for w in report["warnings"]:
                print(f"       - {w}")

        print(f"\nReport: {report_path}")
        print("\nResults:")

        for message in verifier.checks:
            print(f"[OK] {message}")

        for message in verifier.errors:
            print(f"[FAIL] {message}")

        if verifier.errors:
            print("\nVERDICT: FAIL — chain verification failed.")
            print(
                "One or more integrity, consistency, "
                "or signature checks failed."
            )
            return 1

        print("\nVERDICT: PASS — chain verified.")
        print(
            "Integrity, consistency, and Ed25519 signature "
            "are all valid."
        )
        return 0

    except (OSError, ValueError, TypeError, KeyError) as exc:
        print(f"\nFAIL: error reading or verifying data: {exc}")
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
    args = parser.parse_args()
    return verify_output(args.output_fits, args.report)


if __name__ == "__main__":
    sys.exit(main())