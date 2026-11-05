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

"""Unified AstroProv pipeline with automatic or manual frame selection."""

import argparse
import hashlib
import json
import re
import shutil
import sys
from pathlib import Path

from astropy.io import fits

from astroprov import mapping, processing, snapshot, verify, fits_prov, signature, timestamping
from astroprov.siril_wrapper import analyze_script_calibration, find_siril_cli, run_siril_cli


SEQ_NAME_RE = re.compile(r"[A-Za-z0-9_.-]+")
SCHEMA_PROCESSING = "astroprov-processing-v2"
TOTAL_STEPS = 7


def clean_process_dir(object_dir: Path) -> None:
    """Remove everything in <object>/process so Siril starts from scratch."""
    process_dir = object_dir / "process"
    if not process_dir.exists():
        return
    for item in process_dir.iterdir():
        if item.is_file():
            item.unlink()
        elif item.is_dir():
            shutil.rmtree(item)


def _safe_exptime(fits_path: Path) -> float:
    """Return EXPTIME from the primary header, or 0.0 on failure."""
    try:
        with fits.open(fits_path, mode="readonly") as hdul:
            return float(hdul[0].header.get("EXPTIME", 0.0))
    except (OSError, ValueError, TypeError):
        return 0.0


def _find_sequence(process_dir: Path) -> Path:
    """Return the newest .seq file in process/, preferring r_*."""
    candidates = list(process_dir.glob("r_*.seq"))
    if not candidates:
        candidates = list(process_dir.glob("*.seq"))
    if not candidates:
        raise FileNotFoundError(f"No .seq file found in {process_dir}")
    return max(candidates, key=lambda p: p.stat().st_mtime)


def _extract_source_stats(manifest_data: dict, script_calib: dict) -> dict:
    """Pull counts and merkle_root from the manifest, failing clearly if lights are missing."""
    cats = manifest_data.get("categories", {})
    lights = cats.get("lights")
    if not lights:
        raise ValueError(
            "Manifest has no 'lights' category. "
            "Ensure the object directory contains a lights/ subfolder with FITS frames."
        )
    return {
        "merkle_root": lights["merkle_root"],
        "lights_count": lights.get("file_count", 0),
        "darks_count": cats.get("darks", {}).get("file_count", 0)
            if script_calib["uses_darks"] else 0,
        "biases_count": (
            cats.get("bias", {}).get("file_count", 0)
            + cats.get("biases", {}).get("file_count", 0)
        ) if script_calib["uses_biases"] else 0,
        "flats_count": cats.get("flats", {}).get("file_count", 0)
            if script_calib["uses_flats"] else 0,
    }


def _find_final_fits(process_dir: Path, expected_stem: str) -> Path:
    """Find the stacked FITS file produced by Siril."""
    if not process_dir.is_dir():
        raise FileNotFoundError(f"Directory not found: {process_dir}")

    # Collect all FITS files regardless of extension case (.fit, .fits, .FIT, .Fits)
    fits_files = [
        f for f in process_dir.iterdir()
        if f.is_file() and f.suffix.lower() in (".fit", ".fits")
    ]
    
    expected_names = {f"{expected_stem}.fit", f"{expected_stem}.fits"}

    exact = [f for f in fits_files if f.name.lower() in expected_names]
    if exact:
        return max(exact, key=lambda p: p.stat().st_mtime)

    fallback = [
        f for f in fits_files
        if "stacked" in f.stem.lower() or "result" in f.stem.lower()
    ]
    if not fallback:
        raise FileNotFoundError(f"Final stacked FITS not found in {process_dir}")
        
    return max(fallback, key=lambda p: p.stat().st_mtime)


def run_pipeline(
    object_dir: Path,
    siril_cli_exe: Path,
    siril_script: Path,
    *,
    manual: bool,
    rej_sigma: float = 3,
    rej_iters: int = 3,
    normalize: bool = False,
    input_func=input,
) -> int:
    object_dir = object_dir.resolve()
    mode = "MANUAL" if manual else "AUTO"
    print(f"\nASTROPROV PIPELINE [{mode}]: {object_dir.name}\n")

    # --- Step 1: Analyze Siril script ---
    print(f"[1/{TOTAL_STEPS}] Analyzing Siril script...")
    script_calib = analyze_script_calibration(siril_script)
    print(f"  - Darks: {'Yes' if script_calib['uses_darks'] else 'No'}")
    print(f"  - Biases: {'Yes' if script_calib['uses_biases'] else 'No'}")
    print(f"  - Flats: {'Yes' if script_calib['uses_flats'] else 'No'}\n")

    # --- Step 2: Calibration and Alignment ---
    print(f"[2/{TOTAL_STEPS}] Calibration and Alignment (Siril)...")
    print("  [*] Cleaning process directory...")
    clean_process_dir(object_dir)
    print("  [OK] Process directory cleaned.")

    run_siril_cli(siril_cli_exe, siril_script, object_dir, "Calibration and Alignment")

    process_dir = object_dir / "process"
    seq_path = _find_sequence(process_dir)
    seq_name = seq_path.stem
    if not SEQ_NAME_RE.fullmatch(seq_name):
        raise ValueError(f"Unsafe sequence name: {seq_name!r}")
    print(f"  [OK] Found .seq: {seq_path.name}\n")

    # --- Step 3: Snapshot ---
    print(f"[3/{TOTAL_STEPS}] Hashing source data (snapshot)...")
    manifest_path = snapshot.create_manifest(object_dir)
    manifest_data = json.loads(manifest_path.read_text(encoding="utf-8"))
    passport_id = manifest_data["passport_id"]
    stats = _extract_source_stats(manifest_data, script_calib)
    merkle_root = stats["merkle_root"]

    exptime = 0.0
    lights_dir = object_dir / "lights"
    if lights_dir.is_dir():
        first_lights = sorted(lights_dir.glob("*.fits"))
        if first_lights:
            exptime = _safe_exptime(first_lights[0])
    print("")

    # --- Step 4: Mapping ---
    print(f"[4/{TOTAL_STEPS}] Creating frame mapping...")
    mapping_path = mapping.build_mapping(object_dir)
    print("")

    # --- Step 5: Frame selection ---
    print(f"[5/{TOTAL_STEPS}] Frame selection...")
    if manual:
        print("  [*] Please open Siril (GUI) manually.")
        print(f"    - Navigate to: {object_dir.name}/process")
        print(f"    - Open sequence: {seq_path.name}")
        print("    - Select good frames (remove clouds, defects).")
        print("    - Save changes and CLOSE Siril.")
        input_func("\n  >>> When Siril is closed, press ENTER here... ")
        print("\n  [OK] Continuing...")
    else:
        print("  [INFO] Auto mode: Siril selects frames automatically.")
    print("")

    # --- Step 6: Pre-stack report ---
    print(f"[6/{TOTAL_STEPS}] Capturing sequence state (pre-stack)...")
    runs_dir = object_dir / ".astroprov" / "runs"
    runs_dir.mkdir(parents=True, exist_ok=True)

    try:
        pre_report = processing.build_pre_stack_report(
            sequence_path=seq_path,
            mapping_path=mapping_path,
            manifest_path=manifest_path,
        )
        pre_report_path = runs_dir / f"pre-stack-{processing.timestamp_for_filename()}.json"
        processing.save_report(pre_report, pre_report_path)
        print(f"  [OK] Pre-stack report saved: {pre_report_path.name}")
        if pre_report["status"] == "validation_failed":
            print("  [WARN] Pre-stack report has validation_failed status.")
    except (OSError, ValueError) as exc:
        raise RuntimeError(f"Failed to create pre-stack report: {exc}") from exc
    print("")

    # --- Step 7: Stacking and signing ---
    print(f"[7/{TOTAL_STEPS}] Stacking and signing...")

    stack_script_path = process_dir / "_stack_pipeline.ssf"
    output_stem = f"{seq_name}_stacked"

    norm_flag = "" if normalize else "-nonorm"
    stack_cmd = (
        f"stack {seq_name} rej {rej_sigma} {rej_iters} {norm_flag} "
        f"-filter-included -out={output_stem}"
    )
    print("  [*] Preparing stacking sequence...")
    print(f"    - Parameters: {stack_cmd}")

    stack_script_content = (
        "requires 1.2.0\n"
        "cd process\n"
        f"{stack_cmd}\n"
        "cd ..\n"
    )

    try:
        stack_script_path.write_text(stack_script_content, encoding="utf-8")
        run_siril_cli(siril_cli_exe, stack_script_path, object_dir, "Stacking")
    finally:
        if stack_script_path.exists():
            stack_script_path.unlink()

    output_fits = _find_final_fits(process_dir, output_stem)
    print(f"  [OK] Found final FITS: {output_fits.name}")

    try:
        keys_dir = object_dir / ".astroprov" / "keys"
        signature.generate_keys(keys_dir)
        pubkey_fp = signature.get_public_key_fingerprint(keys_dir)

        proc_report = processing.build_report(
            sequence_path=seq_path,
            output_path=output_fits,
            mapping_path=mapping_path,
            manifest_path=manifest_path,
            pre_stack_report_path=pre_report_path,
            stack_command=stack_cmd,
        )
        proc_report_path = runs_dir / f"processing-{processing.timestamp_for_filename()}.json"
        processing.save_report(proc_report, proc_report_path)

        report_bytes = proc_report_path.read_bytes()
        report_hash = hashlib.sha256(report_bytes).hexdigest()

        # --- FreeTSA Timestamp ---
        tsr_path = proc_report_path.with_suffix(".tsr")
        print("  [*] Requesting trusted timestamp from FreeTSA...")
        try:
            timestamping.stamp_data(report_bytes, tsr_path)
            print(f"    - Timestamp saved: {tsr_path.name}")
        except Exception as exc:
            print(f"  [WARN] Timestamping failed: {exc}")

        # Create prov_data dict first, WITHOUT the signature
        prov_data = {
            "version": "1.1",
            "id": passport_id,
            "schema": SCHEMA_PROCESSING,
            "merkle_root": merkle_root,
            "date": proc_report["created_utc"],
            "tool": "AstroProv",
            "lights_count": stats["lights_count"],
            "darks_count": stats["darks_count"],
            "biases_count": stats["biases_count"],
            "flats_count": stats["flats_count"],
            "exptime": exptime,
            "pubkey_fingerprint": pubkey_fp,
            "report_hash": report_hash,
        }

        # Generate canonical payload and sign
        sig_bytes = signature.canonical_payload(prov_data)
        sig_hex = signature.sign_data(sig_bytes, keys_dir)

        # Add signature to the dict and write to FITS
        prov_data["signature"] = sig_hex

        fits_prov.write_provenance(output_fits, prov_data)
        print("  [*] Signing post-stack report...")
        print(f"    - Report: {proc_report_path.name}")
        print(f"    - Passport FP: {pubkey_fp}")
        print("  [OK] Passport embedded successfully.")
    except (OSError, ValueError) as exc:
        raise RuntimeError(f"Failed to create post-stack report: {exc}") from exc

    print("\n[*] Starting verification...")
    exit_code = verify.verify_output(str(output_fits), str(proc_report_path))

    if exit_code == 0:
        print("\n[OK] PIPELINE COMPLETED SUCCESSFULLY. All checks passed.")
    else:
        print("\n[FAIL] PIPELINE COMPLETED WITH ERRORS. Check logs above.")

    return exit_code


def main() -> int:
    parser = argparse.ArgumentParser(
        description="AstroProv pipeline with automatic or manual frame selection.",
        formatter_class=lambda prog: argparse.HelpFormatter(prog, max_help_position=28),
    )
    parser.add_argument("object_dir", type=Path, help="Object directory (e.g., NGC5906)")
    parser.add_argument("--script", type=Path, required=True, help="Path to Siril .ssf script")
    parser.add_argument("--siril-cli", type=Path, metavar="PATH", help="Path to siril-cli")
    parser.add_argument(
        "--manual",
        action="store_true",
        help="Pause after calibration so frames can be selected in Siril GUI",
    )
    parser.add_argument("--rej-sigma", type=float, default=3, metavar="N",
                        help="Sigma rejection (default: 3)")
    parser.add_argument("--rej-iters", type=int, default=3, metavar="N",
                        help="Rejection iterations (default: 3)")
    parser.add_argument("--normalize", action="store_true", help="Enable normalization")

    args = parser.parse_args()

    if not args.object_dir.is_dir():
        print(f"[FAIL] {args.object_dir} is not a directory.")
        return 1
    if not args.script.is_file():
        print(f"[FAIL] Script {args.script} not found.")
        return 1

    try:
        siril_exe = find_siril_cli(args.siril_cli)
        return run_pipeline(
            args.object_dir,
            siril_exe,
            args.script,
            manual=args.manual,
            rej_sigma=args.rej_sigma,
            rej_iters=args.rej_iters,
            normalize=args.normalize,
        )
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"\n[FAIL] PIPELINE FAILED: {exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main())