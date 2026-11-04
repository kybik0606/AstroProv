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

"""Unified CLI to seal a FITS file with optional raw source directories."""

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from astropy.io import fits
from astroprov import fits_prov, signature, timestamping, snapshot
from astroprov.utils import safe_int, safe_float


def seal_fits(
    fits_path: Path,
    keys_dir: Path | None,
    object_name: str,
    manual_lights: int | None = None,
    manual_exptime: float | None = None,
    raw_dir: Path | None = None,
) -> None:
    """Seal a FITS file, optionally linking it to a directory of raw frames."""
    fits_path = fits_path.resolve()
    
    if not fits_path.is_file():
        raise FileNotFoundError(f"FITS file not found: {fits_path}")

    mode = "UNIVERSAL" if raw_dir else "STANDALONE"
    print(f"\n[*] {mode} SEALING: {fits_path.name}\n")

    categories = None
    if raw_dir:
        raw_dir = raw_dir.resolve()
        if not raw_dir.is_dir():
            raise NotADirectoryError(f"Raw directory not found: {raw_dir}")
        print(f"  [*] Scanning raw data from: {raw_dir.name}")

    # 1. Resolve keys directory
    if keys_dir is None:
        keys_dir = fits_path.parent / ".astroprov" / "keys"
    else:
        keys_dir = keys_dir.resolve()

    signature.generate_keys(keys_dir)
    pubkey_fp = signature.get_public_key_fingerprint(keys_dir)
    print(f"  [OK] Keys loaded/generated (FP: {pubkey_fp})")

    # 2. Compute canonical data hash BEFORE embedding the passport
    data_hash = fits_prov.calculate_fits_data_hash(fits_path)

    # 3. Snapshot raw directory (if provided)
    if raw_dir:
        categories = snapshot.snapshot_directory(raw_dir)

    # 4. Extract stats from FITS header (and apply manual overrides)
    with fits.open(fits_path, mode="readonly") as hdul:
        hdr = hdul[0].header
        
        if manual_lights is not None:
            lights_cnt = manual_lights
            print(f"  [*] Using manual lights count: {lights_cnt}")
        else:
            lights_cnt = (
                safe_int(hdr.get("STACKCNT")) or
                safe_int(hdr.get("NCOMBINE")) or
                safe_int(hdr.get("LUM_CNT")) or
                safe_int(hdr.get("NFRAMES")) or 0
            )

        if manual_exptime is not None:
            exptime = manual_exptime
            print(f"  [*] Using manual exptime: {exptime}")
        else:
            exptime = (
                safe_float(hdr.get("LUM_EXP")) or
                safe_float(hdr.get("EXPTIME")) or 0.0
            )

        darks_cnt = safe_int(hdr.get("LUM_DARK")) or safe_int(hdr.get("DARKCNT")) or 0
        flats_cnt = safe_int(hdr.get("LUM_FLAT")) or safe_int(hdr.get("FLATCNT")) or 0
        biases_cnt = safe_int(hdr.get("LUM_BIAS")) or safe_int(hdr.get("BIASCNT")) or 0

        print("  [*] Auto-detected stats from header:")
        print(f"    - Lights: {lights_cnt}")
        print(f"    - Darks: {darks_cnt}")
        print(f"    - Flats: {flats_cnt}")
        print(f"    - Biases: {biases_cnt}")
        print(f"    - Single Exptime: {exptime}s")

    # 5. Prepare Report Data based on mode
    created_utc = datetime.now(timezone.utc).isoformat()
    report_data = {
        "created_utc": created_utc,
        "object_name": object_name,
        "output_fits": {
            "path": str(fits_path),
            "sha256": data_hash,
        }
    }

    if raw_dir:
        passport_id = f"collection-{data_hash[:8]}"
        report_data["schema"] = "astroprov-collection-v1"
        report_data["proof_level"] = "claimed"
        
        # Cross-check lights count
        raw_lights = categories.get("lights", {}).get("file_count", 0)
        if lights_cnt > 0 and raw_lights > 0 and lights_cnt != raw_lights:
            print(f"  [WARN] FITS header says {lights_cnt} lights, but {raw_lights} raw files found.")
            print("    Some frames may have been rejected by the stacking software.")

        report_data["cross_check"] = {
            "fits_lights": lights_cnt,
            "raw_lights": raw_lights
        }
        report_data["source_data"] = {
            cat_name: {
                "file_count": cat_data["file_count"],
                "merkle_root": cat_data["merkle_root"]
            } for cat_name, cat_data in categories.items()
        }
        tool_name = "AstroProv-Universal"
        schema_name = "astroprov-collection-v1"
    else:
        passport_id = f"standalone-{data_hash[:8]}"
        report_data["schema"] = "astroprov-standalone-v1"
        report_data["stage"] = "standalone"
        report_data["original_file"] = fits_path.name
        tool_name = "AstroProv-Standalone"
        schema_name = "astroprov-standalone-v1"

    report_bytes = json.dumps(report_data, sort_keys=True, indent=2).encode("utf-8")
    report_hash = hashlib.sha256(report_bytes).hexdigest()

    # Save report
    runs_dir = fits_path.parent / ".astroprov" / "runs"
    runs_dir.mkdir(parents=True, exist_ok=True)
    report_path = runs_dir / f"processing-{passport_id}.json"
    report_path.write_bytes(report_bytes)

    # 6. Timestamp
    tsr_path = report_path.with_suffix(".tsr")
    print("  [*] Requesting trusted timestamp from FreeTSA...")
    try:
        timestamping.stamp_data(report_bytes, tsr_path)
        print(f"    - Timestamp saved: {tsr_path.name}")
    except Exception as exc:
        print(f"  [WARN] Timestamping failed: {exc}")

    # 7. Build canonical payload and sign
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
    print("  [*] Signing report payload...")

    # 8. Embed passport
    prov_data = {
        "version": "1.0",
        "id": passport_id,
        "merkle_root": data_hash,
        "date": created_utc,
        "tool": tool_name,
        "schema": schema_name,
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
    print("  [OK] Passport embedded successfully.")
    print(f"    - Report: {report_path.name}")
    print(f"    - Passport FP: {pubkey_fp}")
    print(f"\n  [OK] File ready: {fits_path.name}")
    
    if raw_dir:
        print(f'  [INFO] Deep verify with: python -m astroprov.verify "{fits_path}" --raw-dir "{raw_dir}"')
    else:
        print(f'  [INFO] Verify with: python -m astroprov.verify "{fits_path}"')


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Seal a FITS file with AstroProv passport (standalone or with raw sources)."
    )
    parser.add_argument("fits", type=Path, help="Path to the FITS file to seal")
    parser.add_argument("--raw", type=Path, default=None, help="Optional directory containing raw frames (lights, darks, etc.)")
    parser.add_argument("--keys", type=Path, default=None, help="Directory for Ed25519 keys (defaults to .astroprov/keys)")
    parser.add_argument("--object", type=str, default="Unknown", help="Object name for metadata")
    parser.add_argument("--lights", type=int, help="Manually override lights count")
    parser.add_argument("--exptime", type=float, help="Manually override exposure time in seconds")
    
    args = parser.parse_args()
    
    try:
        seal_fits(
            fits_path=args.fits,
            keys_dir=args.keys,
            object_name=args.object,
            manual_lights=args.lights,
            manual_exptime=args.exptime,
            raw_dir=args.raw
        )
        return 0
    except (OSError, ValueError, TypeError) as exc:
        print(f"\n  [FAIL] {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())