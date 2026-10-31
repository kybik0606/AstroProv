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

"""Universal CLI to seal a FITS file with provenance from raw source directories."""

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from astropy.io import fits
from astroprov import fits_prov, signature, timestamping, snapshot


def _safe_int(value: object) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _safe_float(value: object) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def seal_collection(
    fits_path: Path, 
    raw_dir: Path,
    keys_dir: Path | None, 
    object_name: str
) -> None:
    fits_path = fits_path.resolve()
    raw_dir = raw_dir.resolve()
    
    if not fits_path.is_file():
        raise FileNotFoundError(f"FITS file not found: {fits_path}")
    if not raw_dir.is_dir():
        raise NotADirectoryError(f"Raw directory not found: {raw_dir}")

    print(f"\nUNIVERSAL SEALING: {fits_path.name}\n")
    print(f"  Scanning raw data from: {raw_dir.name}")

    # 1. Resolve keys directory
    if keys_dir is None:
        keys_dir = fits_path.parent / ".astroprov" / "keys"
    else:
        keys_dir = keys_dir.resolve()

    signature.generate_keys(keys_dir)
    pubkey_fp = signature.get_public_key_fingerprint(keys_dir)
    print(f"  Keys loaded/generated in {keys_dir} (FP: {pubkey_fp})")

    # 2. Compute canonical data hash BEFORE embedding the passport
    data_hash = fits_prov.calculate_fits_data_hash(fits_path)
    
    # 3. Snapshot the raw directory
    categories = snapshot.snapshot_directory(raw_dir)
    
    # 4. Extract basic stats from FITS header
    with fits.open(fits_path, mode="readonly") as hdul:
        hdr = hdul[0].header
        fits_lights = (
            _safe_int(hdr.get("STACKCNT")) or
            _safe_int(hdr.get("NCOMBINE")) or
            _safe_int(hdr.get("LUM_CNT")) or
            _safe_int(hdr.get("NFRAMES")) or 0
        )
        exptime = (
            _safe_float(hdr.get("LUM_EXP")) or
            _safe_float(hdr.get("EXPTIME")) or 0.0
        )
        
        darks_cnt = _safe_int(hdr.get("LUM_DARK")) or _safe_int(hdr.get("DARKCNT")) or 0
        flats_cnt = _safe_int(hdr.get("LUM_FLAT")) or _safe_int(hdr.get("FLATCNT")) or 0
        biases_cnt = _safe_int(hdr.get("LUM_BIAS")) or _safe_int(hdr.get("BIASCNT")) or 0

    # Cross-check lights count
    raw_lights = categories.get("lights", {}).get("file_count", 0)
    if fits_lights > 0 and raw_lights > 0 and fits_lights != raw_lights:
        print(f"  [INFO] FITS header says {fits_lights} lights, but {raw_lights} raw files found.")
        print("         Some frames may have been rejected by the stacking software.")

    # 5. Create a collection report
    passport_id = f"collection-{data_hash[:8]}"
    report_data = {
        "schema": "astroprov-collection-v1",
        "proof_level": "claimed",
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "object_name": object_name,
        "output_fits": {
            "path": str(fits_path),
            "sha256": data_hash,
        },
        "cross_check": {
            "fits_lights": fits_lights,
            "raw_lights": raw_lights
        },
        "source_data": {
            cat_name: {
                "file_count": cat_data["file_count"],
                "merkle_root": cat_data["merkle_root"]
            } for cat_name, cat_data in categories.items()
        }
    }
    report_bytes = json.dumps(report_data, sort_keys=True, indent=2).encode("utf-8")
    report_hash = hashlib.sha256(report_bytes).hexdigest()

    # Save report
    runs_dir = fits_path.parent / ".astroprov" / "runs"
    runs_dir.mkdir(parents=True, exist_ok=True)
    report_path = runs_dir / f"processing-{passport_id}.json"
    report_path.write_bytes(report_bytes)

    # 6. Timestamp
    tsr_path = report_path.with_suffix(".tsr")
    print("  Requesting trusted timestamp from FreeTSA...")
    try:
        timestamping.stamp_data(report_bytes, tsr_path)
        print(f"  Timestamp saved: {tsr_path.name}")
    except Exception as exc:
        print(f"  [WARNING] Timestamping failed: {exc}")

    # 7. Build payload and sign
    sig_payload = {
        "report_hash": report_hash,
        "id": passport_id,
        "merkle_root": data_hash, 
        "lights_count": fits_lights,
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

    # 8. Embed passport
    prov_data = {
        "version": "1.0",
        "id": passport_id,
        "merkle_root": data_hash,
        "date": report_data["created_utc"],
        "tool": "AstroProv-Universal",
        "schema": "astroprov-collection-v1",
        "lights_count": fits_lights,
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
    print(f"To fully verify source data, run: python -m astroprov.verify <file> --raw-dir {raw_dir}")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Seal a FITS file and its raw source data into a provenance chain."
    )
    parser.add_argument("fits", type=Path, help="Path to the stacked FITS file")
    parser.add_argument("--raw", type=Path, required=True, help="Directory containing raw frames")
    parser.add_argument("--keys", type=Path, default=None, help="Directory for Ed25519 keys")
    parser.add_argument("--object", type=str, default="Unknown", help="Object name")
    
    args = parser.parse_args()
    
    try:
        seal_collection(args.fits, args.raw, args.keys, args.object)
        return 0
    except (OSError, ValueError, TypeError) as exc:
        print(f"\nERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())