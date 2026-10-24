"""Viewing the AstroProv passport (from FITS + manifest)."""

import argparse
import json
import sys
from pathlib import Path

from astropy.io import fits
from astroprov import fits_prov


def find_manifest_by_id(object_dir: Path | str, passport_id: str) -> Path | None:
    """Finds the JSON manifest by its ID in the hidden .astroprov folder."""
    object_dir = Path(object_dir).resolve()
    manifests_dir = object_dir / ".astroprov" / "manifests"
    
    if not manifests_dir.is_dir():
        return None

    manifest_path = manifests_dir / f"{passport_id}.json"
    if manifest_path.is_file():
        return manifest_path

    return None


def format_time(seconds: float) -> str:
    """Formats seconds into a readable form (hours, minutes)."""
    if seconds is None:
        return ""
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    
    if hours > 0:
        return f"{hours} h {minutes} min ({int(seconds)} s)"
    elif minutes > 0:
        return f"{minutes} min {secs} s"
    else:
        return f"{secs} s"


def inspect_fits(fits_path: Path | str) -> int:
    """Reads the passport from FITS, finds the manifest and prints a report."""
    fits_path = Path(fits_path).resolve()
    
    print(f"\n{'='*60}")
    print(f"ASTROPROV PASSPORT: {fits_path.name}")
    print(f"{'='*60}\n")

    if not fits_path.is_file():
        print(f"ERROR: file not found: {fits_path}")
        return 1

    # 1. Read the passport from FITS
    prov = fits_prov.read_provenance(fits_path)

    if prov is None:
        print("ERROR: this FITS file has no passport (HDU 'PROVENANCE').")
        return 1

    passport_id = prov.get("id")
    
    # 2. Read the FITS header for exposure info (EXPTIME and STACKCNT)
    exptime_hdr = None
    stack_count = None
    try:
        with fits.open(fits_path, mode="readonly") as hdul:
            header = hdul[0].header
            exptime_hdr = header.get("EXPTIME")
            stack_count = header.get("STACKCNT") or header.get("NCOMBINE")
    except Exception as e:
        print(f"[WARNING] Could not read FITS header: {e}")

    # 3. Try to find the manifest by ID (for backward compatibility with old files)
    object_dir = fits_path.parent.parent
    manifest_path = find_manifest_by_id(object_dir, passport_id) if passport_id else None

    # 4. Print general information
    print("GENERAL INFO")
    print(f"  Passport ID:      {passport_id or '—'}")
    print(f"  Created:          {prov.get('date', '—')}")
    print(f"  Format version:   {prov.get('version', '—')}")
    print(f"  Tool:             {prov.get('tool', '—')}")

    print(f"\nSOURCE DATA MERKLE ROOT (SHA-256)")
    print(f"  {prov.get('merkle_root', '—')}")

    # 5. Print statistics (priority: FITS, then manifest)
    lights_cnt = prov.get("lights_count")
    
    if lights_cnt is not None:
        darks_cnt = prov.get("darks_count", 0)
        biases_cnt = prov.get("biases_count", 0)
        flats_cnt = prov.get("flats_count", 0)
        
        print(f"\nSOURCE DATA COMPOSITION (from FITS passport)")
        print(f"  Lights:     {lights_cnt}")
        print(f"  Darks:      {darks_cnt}")
        print(f"  Biases:     {biases_cnt}")
        print(f"  Flats:      {flats_cnt}")
        
        total = lights_cnt + darks_cnt + biases_cnt + flats_cnt
        print(f"  {'Total:':<12} {total} files")
    elif manifest_path:
        try:
            manifest_data = json.loads(manifest_path.read_text(encoding="utf-8"))
            cats = manifest_data.get("categories", {})
            
            lights_cnt = cats.get("lights", {}).get("file_count", 0)
            darks_cnt = cats.get("darks", {}).get("file_count", 0)
            biases_cnt = (cats.get("bias", {}).get("file_count", 0) 
                          + cats.get("biases", {}).get("file_count", 0))
            flats_cnt = cats.get("flats", {}).get("file_count", 0)
            
            print(f"\nSOURCE DATA COMPOSITION (from manifest, legacy format)")
            print(f"  Lights:     {lights_cnt}")
            print(f"  Darks:      {darks_cnt}")
            print(f"  Biases:     {biases_cnt}")
            print(f"  Flats:      {flats_cnt}")
            
            total = lights_cnt + darks_cnt + biases_cnt + flats_cnt
            print(f"  {'Total:':<12} {total} files")
        except Exception as e:
            print(f"\n  [WARNING] Could not read manifest: {e}")
    else:
        print("\nSOURCE DATA COMPOSITION")
        print("  [NO DATA] This FITS has no statistics, and the manifest was not found.")

    # 6. Print exposure info
    exptime_val = prov.get("exptime")
    if exptime_val is None:
        exptime_val = exptime_hdr

    if exptime_val is not None and float(exptime_val) > 0:
        print(f"\nEXPOSURE")
        print(f"  Single frame:       {exptime_val} s")
        
        # Fallback to lights_cnt from the passport if STACKCNT is missing in the raw FITS header
        frames_count = stack_count
        if (not frames_count or int(frames_count) == 0) and lights_cnt:
            frames_count = lights_cnt

        if frames_count and int(frames_count) > 0:
            total_exp = float(exptime_val) * int(frames_count)
            print(f"  Frames in stack:     {frames_count}")
            print(f"  Total:               {format_time(total_exp)}")
        else:
            print(f"  [NO DATA] FITS header has no STACKCNT or NCOMBINE, and passport has no Lights count.")

    print(f"\n{'='*60}")
    print("Integrity check: use `python -m astroprov.verify`")
    print(f"{'='*60}\n")

    return 0


def inspect_directory(directory: Path | str) -> int:
    """Scans a directory and shows passports of all FITS files in it."""
    directory = Path(directory).resolve()
    
    if not directory.is_dir():
        print(f"ERROR: {directory} is not a directory")
        return 1

    fits_files = sorted(directory.glob("*.fit")) + sorted(directory.glob("*.fits"))
    
    if not fits_files:
        print(f"No FITS files in {directory}.")
        return 1

    print(f"\n{'='*60}")
    print(f"PASSPORT SCAN IN DIRECTORY: {directory}")
    print(f"{'='*60}\n")

    with_passport = 0
    total = 0

    for f in fits_files:
        total += 1
        prov = fits_prov.read_provenance(f)
        if prov:
            with_passport += 1
            print(f"  [PASSPORT] {f.name}")
            print(f"             ID: {prov.get('id')}")
        else:
            print(f"  [NO PASSPORT] {f.name}")

    print(f"\nTotal: {with_passport} with passport out of {total} files\n")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="View the AstroProv passport in a FITS file or directory."
    )
    parser.add_argument(
        "target",
        type=Path,
        help="Path to a FITS file or a directory with FITS files",
    )
    args = parser.parse_args()

    target = args.target
    if not target.exists():
        print(f"ERROR: path does not exist: {target}")
        return 1

    if target.is_file():
        return inspect_fits(target)
    else:
        return inspect_directory(target)


if __name__ == "__main__":
    sys.exit(main())