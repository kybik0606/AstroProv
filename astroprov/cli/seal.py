"""Standalone CLI to seal an existing FITS file with a cryptographic passport, without Siril."""

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from astropy.io import fits
from astroprov import fits_prov, signature


def seal_fits(
    fits_path: Path, 
    keys_dir: Path | None, 
    object_name: str,
    manual_lights: int | None = None,
    manual_exptime: float | None = None
):
    fits_path = fits_path.resolve()
    
    if not fits_path.is_file():
        print(f"ERROR: File not found: {fits_path}")
        sys.exit(1)

    print(f"\nSTANDALONE SEALING: {fits_path.name}\n")

    # 1. Resolve keys directory (default: next to the FITS file)
    if keys_dir is None:
        keys_dir = fits_path.parent / ".astroprov" / "keys"
    else:
        keys_dir = keys_dir.resolve()

    signature.generate_keys(keys_dir)
    pubkey_fp = signature.get_public_key_fingerprint(keys_dir)
    print(f"  Keys loaded/generated in {keys_dir} (FP: {pubkey_fp})")

    # 2. Compute canonical data hash BEFORE embedding the passport
    data_hash = fits_prov.calculate_fits_data_hash(fits_path)
    
    # 3. Extract basic stats from FITS header (Universal fallback logic)
    with fits.open(fits_path, mode="readonly") as hdul:
        hdr = hdul[0].header
        
        # --- LIGHTS COUNT (Check various standards) ---
        if manual_lights is not None:
            lights_cnt = manual_lights
            print(f"  Using manual lights count: {lights_cnt}")
        elif "STACKCNT" in hdr:     # Siril standard
            lights_cnt = int(hdr["STACKCNT"])
        elif "NCOMBINE" in hdr:     # Siril alternative
            lights_cnt = int(hdr["NCOMBINE"])
        elif "LUM_CNT" in hdr:      # N.I.N.A. standard
            lights_cnt = int(hdr["LUM_CNT"])
        elif "NFRAMES" in hdr:      # PixInsight / generic standard
            lights_cnt = int(hdr["NFRAMES"])
        else:
            lights_cnt = 0          # Unknown

        # --- EXPOSURE TIME ---
        if manual_exptime is not None:
            exptime = manual_exptime
            print(f"  Using manual exptime: {exptime}")
        elif "LUM_EXP" in hdr:      # N.I.N.A. single frame exp
            exptime = float(hdr["LUM_EXP"])
        elif "EXPTIME" in hdr:
            exptime = float(hdr["EXPTIME"])
        else:
            exptime = 0.0

        # --- CALIBRATION COUNTS (Check multiple standards) ---
        darks_cnt = int(hdr.get("LUM_DARK", hdr.get("DARKCNT", 0)))
        flats_cnt = int(hdr.get("LUM_FLAT", hdr.get("FLATCNT", 0)))
        biases_cnt = int(hdr.get("LUM_BIAS", hdr.get("BIASCNT", 0)))

        print(f"  Auto-detected stats from header:")
        print(f"    Lights: {lights_cnt}, Darks: {darks_cnt}, Flats: {flats_cnt}, Biases: {biases_cnt}")
        print(f"    Single Exptime: {exptime}s")

    # 4. Create a synthetic report
    passport_id = f"standalone-{data_hash[:8]}"
    report_data = {
        "schema": "astroprov-standalone-v1",
        "stage": "standalone",
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "object_name": object_name,
        "original_file": fits_path.name,
        "output_fits": {
            "path": str(fits_path),
            "sha256": data_hash,
        }
    }
    report_bytes = json.dumps(report_data, sort_keys=True, indent=2).encode("utf-8")
    report_hash = hashlib.sha256(report_bytes).hexdigest()

    # Save report to .astroprov/runs/ next to the file
    runs_dir = fits_path.parent / ".astroprov" / "runs"
    runs_dir.mkdir(parents=True, exist_ok=True)
    report_path = runs_dir / f"processing-{passport_id}.json"
    report_path.write_bytes(report_bytes)

    # 5. Build canonical payload and sign
    sig_payload = {
        "report_hash": report_hash,
        "id": passport_id,
        "merkle_root": data_hash,
        "lights_count": lights_cnt,
        "darks_count": darks_cnt,
        "biases_count": biases_cnt,
        "flats_count": flats_cnt,
        "exptime": exptime,
    }
    sig_bytes = json.dumps(
        sig_payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    
    sig_hex = signature.sign_data(sig_bytes, keys_dir)
    print(f"  Payload signed with Ed25519")

    # 6. Embed the passport
    prov_data = {
        "version": "1.0",
        "id": passport_id,
        "merkle_root": data_hash,
        "date": report_data["created_utc"],
        "tool": "AstroProv-Standalone",
        "lights_count": lights_cnt,
        "darks_count": darks_cnt,
        "biases_count": biases_cnt,
        "flats_count": flats_cnt,
        "exptime": exptime,
        "pubkey_fingerprint": pubkey_fp,
        "report_hash": report_hash,
        "signature": sig_hex
    }
    
    fits_prov.write_provenance(fits_path, prov_data)
    print(f"  Passport embedded successfully.\n")
    print(f"  File: {fits_path}")
    print(f"  ID:   {prov_data['id']}\n")
    print("You can verify this file using: python -m astroprov.verify <file>")


def main():
    parser = argparse.ArgumentParser(
        description="Seal an existing FITS file with AstroProv passport (no Siril required)."
    )
    parser.add_argument("fits", type=Path, help="Path to the FITS file to seal")
    parser.add_argument("--keys", type=Path, default=None, help="Directory for Ed25519 keys (defaults to .astroprov/keys next to the FITS file)")
    parser.add_argument("--object", type=str, default="Unknown", help="Object name for metadata")
    parser.add_argument("--lights", type=int, help="Manually override lights count")
    parser.add_argument("--exptime", type=float, help="Manually override exposure time in seconds")
    
    args = parser.parse_args()
    seal_fits(args.fits, args.keys, args.object, args.lights, args.exptime)


if __name__ == "__main__":
    main()