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

"""Fully automated pipeline: calibration, selection, stacking and verification."""

import argparse
import sys
import shutil
import json
import hashlib
from datetime import datetime, timezone
from pathlib import Path

from astropy.io import fits

from astroprov import mapping, processing, snapshot, verify, fits_prov, signature, timestamping
from astroprov.siril_wrapper import analyze_script_calibration, find_siril_cli, run_siril_cli


def clean_process_dir(object_dir: Path) -> None:
    """Clean the process directory so Siril starts from scratch."""
    process_dir = object_dir / "process"
    if process_dir.exists():
        print("[*] Cleaning process directory from old files...")
        for item in process_dir.iterdir():
            if item.is_file():
                item.unlink()
            elif item.is_dir():
                shutil.rmtree(item)
        print("  Process directory cleaned.\n")


def _safe_exptime(fits_path: Path) -> float:
    """Safely extract EXPTIME from a FITS header."""
    try:
        with fits.open(fits_path, mode="readonly") as h:
            val = h[0].header.get("EXPTIME", 0.0)
            return float(val)
    except (OSError, ValueError, TypeError):
        return 0.0


def run_auto_pipeline(object_dir: Path, siril_cli_exe: Path, siril_script: Path) -> int:
    object_dir = object_dir.resolve()
    print(f"\nASTROPROV AUTO PIPELINE: {object_dir.name}\n")

    print("[*] Analyzing Siril script...")
    script_calib = analyze_script_calibration(siril_script)
    print(f"  Darks in script: {'Yes' if script_calib['uses_darks'] else 'No'}")
    print(f"  Biases in script: {'Yes' if script_calib['uses_biases'] else 'No'}")
    print(f"  Flats in script: {'Yes' if script_calib['uses_flats'] else 'No'}\n")

    clean_process_dir(object_dir)
    run_siril_cli(siril_cli_exe, siril_script, object_dir, "Calibration and Alignment")
    print("  Calibration completed.\n")

    print("[2/7] Hashing source data (snapshot)...")
    manifest_path = snapshot.create_manifest(object_dir)
    print()
    
    manifest_data = json.loads(manifest_path.read_text(encoding="utf-8"))
    passport_id = manifest_data["passport_id"]
    merkle_root = manifest_data["categories"]["lights"]["merkle_root"]
    
    cats = manifest_data.get("categories", {})
    lights_cnt = cats.get("lights", {}).get("file_count", 0)
    
    darks_cnt = cats.get("darks", {}).get("file_count", 0) if script_calib["uses_darks"] else 0
    biases_cnt = (cats.get("bias", {}).get("file_count", 0) + cats.get("biases", {}).get("file_count", 0)) if script_calib["uses_biases"] else 0
    flats_cnt = cats.get("flats", {}).get("file_count", 0) if script_calib["uses_flats"] else 0
    
    exptime = 0.0
    lights_dir = object_dir / "lights"
    if lights_dir.is_dir():
        first_lights = sorted(lights_dir.glob("*.fits"))
        if first_lights:
            exptime = _safe_exptime(first_lights[0])

    print("[3/7] Creating frame mapping...")
    mapping_path = mapping.build_mapping(object_dir)
    print()

    print("STEP 1: MANUAL FRAME SELECTION")
    print("1. Open Siril (GUI) manually.")
    print(f"2. Navigate to folder: {object_dir.name}/process")
    print("3. Open sequence r_pp_light_.seq.")
    print("4. Select good frames (remove clouds, defects).")
    print("5. Save changes and CLOSE SIRIL (important!).")
    
    input("\n>>> When Siril is closed, press ENTER here...")
    print("\nContinuing...\n")

    print("[4/7] Capturing sequence state...")
    process_dir = object_dir / "process"
    
    seq_files = list(process_dir.glob("r_*.seq"))
    if not seq_files:
        seq_files = list(process_dir.glob("*.seq"))
        
    if not seq_files:
        raise FileNotFoundError("No .seq files found in process directory.")
        
    seq_path = max(seq_files, key=lambda p: p.stat().st_mtime)
    seq_name = seq_path.stem 
    print(f"  Found .seq: {seq_path.name}\n")

    print("[5/7] Creating pre-stack report...")
    object_dir_runs = object_dir / ".astroprov" / "runs"
    object_dir_runs.mkdir(parents=True, exist_ok=True)

    try:
        report = processing.build_pre_stack_report(
            sequence_path=seq_path,
            mapping_path=mapping_path,
            manifest_path=manifest_path
        )
        report_path = object_dir_runs / f"pre-stack-{processing.timestamp_for_filename()}.json"
        processing.save_report(report, report_path)
        print(f"  Pre-stack report: {report_path.name}")
        
        if report["status"] == "validation_failed":
            print("  WARNING: Pre-stack report has validation_failed status.")
    except (OSError, ValueError) as exc:
        raise RuntimeError(f"Failed to create pre-stack report: {exc}") from exc

    print("\nSTEP 2: AUTOMATED STACKING")
    
    stack_script_path = process_dir / "_stack_temp.ssf"
    output_fits_name = f"{seq_name}_stacked"
    
    stack_cmd = f"stack {seq_name} rej 3 3 -nonorm -filter-included -out={output_fits_name}"
    print(f"  Stacking parameters: {stack_cmd}")

    stack_script_content = f"""requires 1.2.0
cd process
{stack_cmd}
cd ..
"""
    stack_script_path.write_text(stack_script_content, encoding="utf-8")

    run_siril_cli(
        siril_cli_exe, stack_script_path, object_dir,
        "Stacking", indent="  ", prefix="",
    )
    print("  Stacking completed.\n")

    if stack_script_path.exists():
        stack_script_path.unlink()

    print("[6/7] Analyzing stacking results...")
    fits_files = list(process_dir.glob("*.fit"))
    stack_files = [f for f in fits_files if "stacked" in f.stem.lower() or "result" in f.stem.lower()]
    
    if not stack_files:
        raise FileNotFoundError("Final stacked FITS file not found in process directory.")
    
    output_fits = max(stack_files, key=lambda p: p.stat().st_mtime)
    print(f"  Found final FITS: {output_fits.name}\n")

    print("[7/7] Ed25519 signing, embedding passport and creating post-stack report...")
    try:
        keys_dir = object_dir / ".astroprov" / "keys"
        
        signature.generate_keys(keys_dir)
        pubkey_fp = signature.get_public_key_fingerprint(keys_dir)

        proc_report = processing.build_report(
            sequence_path=seq_path,
            output_path=output_fits,
            mapping_path=mapping_path,
            manifest_path=manifest_path,
            pre_stack_report_path=report_path
        )
        proc_report_path = object_dir_runs / f"processing-{processing.timestamp_for_filename()}.json"
        processing.save_report(proc_report, proc_report_path)

        report_bytes = proc_report_path.read_bytes()
        report_hash = hashlib.sha256(report_bytes).hexdigest()

        # --- FreeTSA Timestamp ---
        tsr_path = proc_report_path.with_suffix(".tsr")
        print("  Requesting trusted timestamp from FreeTSA...")
        try:
            # ИСПРАВЛЕНО: передаем байты, а не хэш
            timestamping.stamp_data(report_bytes, tsr_path)
            print(f"  Timestamp saved: {tsr_path.name}")
        except Exception as exc:
            print(f"  [WARNING] Timestamping failed: {exc}")
        # -------------------------

        sig_payload = {
            "report_hash": report_hash,
            "id": passport_id,
            "merkle_root": merkle_root,
            "lights_count": int(lights_cnt),
            "darks_count": int(darks_cnt),
            "biases_count": int(biases_cnt),
            "flats_count": int(flats_cnt),
            "exptime": float(exptime),
        }
        sig_bytes = json.dumps(
            sig_payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode("utf-8")
        sig_hex = signature.sign_data(sig_bytes, keys_dir)

        prov_data = {
            "version": "1.0",
            "id": passport_id,
            "merkle_root": merkle_root,
            "date": datetime.now(timezone.utc).isoformat(),
            "tool": "AstroProv",
            "lights_count": lights_cnt,
            "darks_count": darks_cnt,
            "biases_count": biases_cnt,
            "flats_count": flats_cnt,
            "exptime": exptime,
            "pubkey_fingerprint": pubkey_fp,
            "report_hash": report_hash,
            "signature": sig_hex
        }
        
        fits_prov.write_provenance(output_fits, prov_data)
        
        print(f"  Post-stack report: {proc_report_path.name}")
        print(f"  Report signed with Ed25519 (FP: {pubkey_fp})")
    except (OSError, ValueError) as exc:
        raise RuntimeError(f"Failed to create post-stack report: {exc}") from exc

    print("\nPassport embedded. Starting verification...")
    exit_code = verify.verify_output(str(output_fits), str(proc_report_path))
    
    if exit_code == 0:
        print("\nAUTO PIPELINE COMPLETED SUCCESSFULLY. All checks passed.")
    else:
        print("\nAUTO PIPELINE COMPLETED WITH ERRORS. Check logs above.")
    
    return exit_code


def main() -> int:
    parser = argparse.ArgumentParser(description="AstroProv Auto Pipeline.")
    parser.add_argument("object_dir", type=Path, help="Object directory (e.g., NGC5906)")
    parser.add_argument("--script", type=Path, required=True, help="Path to Siril .ssf calibration script")
    parser.add_argument(
        "--siril-cli", 
        type=Path, 
        default=None,
        help="Path to siril-cli. If not provided, uses SIRIL_CLI_PATH or system PATH."
    )
    
    args = parser.parse_args()

    if not args.object_dir.is_dir():
        print(f"Error: {args.object_dir} is not a directory.")
        return 1
        
    if not args.script.is_file():
        print(f"Error: Script {args.script} not found.")
        return 1

    try:
        siril_exe = find_siril_cli(args.siril_cli)
        exit_code = run_auto_pipeline(args.object_dir, siril_exe, args.script)
        return exit_code
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"\nPIPELINE FAILED: {exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main())