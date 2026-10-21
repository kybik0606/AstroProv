"""Fully automated pipeline: calibration, selection, stacking and verification."""

import argparse
import subprocess
import sys
import shutil
import json
import hashlib
from datetime import datetime, timezone
from pathlib import Path
from astropy.io import fits

from astroprov import mapping, processing, snapshot, verify, fits_prov, signature


def clean_process_dir(object_dir: Path) -> None:
    """Cleans the process directory so Siril starts from scratch."""
    process_dir = object_dir / "process"
    if process_dir.exists():
        print(f"[*] Cleaning process directory from old files...")
        for item in process_dir.iterdir():
            if item.is_file():
                item.unlink()
            elif item.is_dir():
                shutil.rmtree(item)
        print("    Process directory cleaned.\n")


def analyze_script_calibration(script_path: Path) -> dict:
    """
    Reads the .ssf script and determines which calibration frames are used.
    This is needed to avoid recording unnecessary data in the passport.
    """
    text = script_path.read_text(encoding="utf-8", errors="replace")
    
    uses_darks = False
    uses_biases = False
    uses_flats = False
    
    for line in text.splitlines():
        line = line.strip()
        # Skip empty lines and comments
        if line.startswith("#") or not line:
            continue
            
        # Look for the calibration command
        if line.startswith("calibrate"):
            if "-dark=" in line:
                uses_darks = True
            if "-bias=" in line:
                uses_biases = True
            if "-flat=" in line:
                uses_flats = True
                
    return {
        "uses_darks": uses_darks,
        "uses_biases": uses_biases,
        "uses_flats": uses_flats,
    }


def run_siril_cli(siril_exe: Path, script_path: Path, work_dir: Path, action_name: str) -> None:
    """Runs Siril CLI with the given script and prints information about it."""
    print(f"[*] Running Siril CLI: {action_name}")
    print(f"    Script: {script_path.name}")
    
    cmd = [
        str(siril_exe),
        "-d", str(work_dir.resolve()),
        "-s", str(script_path.resolve())
    ]
    
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=False)
        if result.returncode != 0:
            print(f"\n[-] SIRIL CLI ERROR during '{action_name}':")
            print(result.stdout)
            print(result.stderr)
            sys.exit(1)
    except FileNotFoundError:
        print(f"\n[-] ERROR: siril-cli.exe not found at: {siril_exe}")
        sys.exit(1)


def run_auto_pipeline(object_dir: Path, siril_cli_exe: Path, siril_script: Path):
    object_dir = object_dir.resolve()
    print(f"\n{'='*60}")
    print(f"ASTROPROV AUTO PIPELINE: {object_dir.name}")
    print(f"{'='*60}\n")

    # Analyze the script BEFORE starting
    print("[*] Analyzing Siril script...")
    script_calib = analyze_script_calibration(siril_script)
    print(f"    Darks in script: {'Yes' if script_calib['uses_darks'] else 'No'}")
    print(f"    Biases in script: {'Yes' if script_calib['uses_biases'] else 'No'}")
    print(f"    Flats in script: {'Yes' if script_calib['uses_flats'] else 'No'}\n")

    clean_process_dir(object_dir)
    run_siril_cli(siril_cli_exe, siril_script, object_dir, "Calibration and Alignment")
    print("  Calibration completed.\n")

    print("[2/6] Hashing input data (snapshot)...")
    manifest_path = snapshot.create_manifest(object_dir)
    print(f"  Manifest created: {manifest_path.name}\n")
    
    # Extract statistics from the manifest
    manifest_data = json.loads(manifest_path.read_text(encoding="utf-8"))
    passport_id = manifest_data["passport_id"]
    merkle_root = manifest_data["categories"]["lights"]["merkle_root"]
    
    cats = manifest_data.get("categories", {})
    lights_cnt = cats.get("lights", {}).get("file_count", 0)
    
    # Smart counting: take files from disk, but zero out those not in the script
    darks_cnt = cats.get("darks", {}).get("file_count", 0) if script_calib["uses_darks"] else 0
    biases_cnt = (cats.get("bias", {}).get("file_count", 0) + cats.get("biases", {}).get("file_count", 0)) if script_calib["uses_biases"] else 0
    flats_cnt = cats.get("flats", {}).get("file_count", 0) if script_calib["uses_flats"] else 0
    
    exptime = 0.0
    lights_dir = object_dir / "lights"
    if lights_dir.is_dir():
        first_lights = sorted(lights_dir.glob("*.fits"))
        if first_lights:
            with fits.open(first_lights[0], mode="readonly") as h:
                exptime = h[0].header.get("EXPTIME", 0.0)

    print("[3/6] Creating frame mapping...")
    mapping_path = mapping.build_mapping(object_dir)
    print(f"  Mapping created: {mapping_path.name}\n")

    print("="*60)
    print("STEP 1: MANUAL FRAME SELECTION")
    print("1. Open Siril (GUI) manually.")
    print(f"2. Navigate to folder: {object_dir.name}")
    print("3. Open sequence r_pp_light_.seq.")
    print("4. Select good frames (remove clouds, defects).")
    print("5. Save changes and CLOSE SIRIL (important!).")
    print("="*60)
    
    input("\n>>> When Siril is closed, press ENTER here...")
    print("\nContinuing...\n")

    print("[4/6] Capturing sequence state...")
    process_dir = object_dir / "process"
    
    seq_files = list(process_dir.glob("r_*.seq"))
    if not seq_files:
        seq_files = list(process_dir.glob("*.seq"))
        
    if not seq_files:
        print("  ERROR: .seq files not found in process directory.")
        sys.exit(1)
        
    seq_path = max(seq_files, key=lambda p: p.stat().st_mtime)
    seq_name = seq_path.stem 
    print(f"  Found .seq: {seq_path.name}\n")

    print("[5/6] Creating pre-stack report...")
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
    except Exception as exc:
        print(f"  ERROR creating pre-stack report: {exc}")
        sys.exit(1)

    print("\n" + "="*60)
    print("STEP 2: AUTOMATED STACKING")
    print("="*60)
    
    stack_script_path = process_dir / "_stack_temp.ssf"
    output_fits_name = f"{seq_name}_stacked"
    
    stack_cmd = f"stack {seq_name} rej 3 3 -nonorm -filter-included -out={output_fits_name}"
    print(f"[*] Stacking parameters: {stack_cmd}")

    stack_script_content = f"""requires 1.2.0
cd process
{stack_cmd}
cd ..
"""
    stack_script_path.write_text(stack_script_content, encoding="utf-8")

    run_siril_cli(siril_cli_exe, stack_script_path, object_dir, "Stacking")
    print("  Stacking completed.\n")

    if stack_script_path.exists():
        stack_script_path.unlink()

    print("[6/6] Analyzing stacking results...")
    fits_files = list(process_dir.glob("*.fit"))
    stack_files = [f for f in fits_files if "stacked" in f.stem.lower() or "result" in f.stem.lower()]
    
    if not stack_files:
        print("  ERROR: Final FITS file not found.")
        sys.exit(1)
    
    output_fits = max(stack_files, key=lambda p: p.stat().st_mtime)
    print(f"  Found final FITS: {output_fits.name}\n")

    # --- STEP 7: SIGNING AND EMBEDDING PASSPORT ---
    print("[7/7] Ed25519 signing, embedding passport and creating post-stack report...")
    try:
        keys_dir = object_dir / ".astroprov" / "keys"
        
        # 1. Generate/read keys
        signature.generate_keys(keys_dir)
        pubkey_fp = signature.get_public_key_fingerprint(keys_dir)

        # 2. Build the final JSON report (it computes the data hash WITHOUT the passport)
        proc_report = processing.build_report(
            sequence_path=seq_path,
            output_path=output_fits,
            mapping_path=mapping_path,
            manifest_path=manifest_path,
            pre_stack_report_path=report_path
        )
        proc_report_path = object_dir_runs / f"processing-{processing.timestamp_for_filename()}.json"
        processing.save_report(proc_report, proc_report_path)

        # 3. Sign the SHA-256 hash of that JSON report
        report_bytes = proc_report_path.read_bytes()
        report_hash = hashlib.sha256(report_bytes).hexdigest()
        sig_hex = signature.sign_data(report_hash.encode('utf-8'), keys_dir)

        # 4. Build passport with signature
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
        
        # 5. Embed passport into FITS
        fits_prov.write_provenance(output_fits, prov_data)
        
        print(f"  Post-stack report: {proc_report_path.name}")
        print(f"  ✓ Report signed with Ed25519 (FP: {pubkey_fp})")
    except Exception as exc:
        print(f"  ERROR creating post-stack report: {exc}")
        sys.exit(1)

    print("\n" + "="*60)
    print("FINAL INTEGRITY VERIFICATION")
    print("="*60 + "\n")
    
    exit_code = verify.verify_output(str(output_fits), str(proc_report_path))
    
    if exit_code == 0:
        print("\nAUTO PIPELINE COMPLETED SUCCESSFULLY. All checks passed.")
    else:
        print("\nAUTO PIPELINE COMPLETED WITH ERRORS. Check logs above.")
    
    sys.exit(exit_code)


def main():
    parser = argparse.ArgumentParser(description="AstroProv Auto Pipeline.")
    parser.add_argument("object_dir", type=Path, help="Object directory (e.g., NGC5906)")
    parser.add_argument("--script", type=Path, required=True, help="Path to Siril .ssf calibration script")
    parser.add_argument(
        "--siril-cli", 
        type=Path, 
        default=Path(r"c:\program files\siril\bin\siril-cli.exe"),
        help="Path to siril-cli.exe"
    )
    
    args = parser.parse_args()

    if not args.object_dir.is_dir():
        print(f"Error: {args.object_dir} is not a directory.")
        sys.exit(1)
        
    if not args.script.is_file():
        print(f"Error: Script {args.script} not found.")
        sys.exit(1)

    run_auto_pipeline(args.object_dir, args.siril_cli, args.script)


if __name__ == "__main__":
    main()