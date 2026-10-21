"""Creating and updating the mapping between original NINA frames and processed Siril files."""

import argparse
import json
import shutil
import sys
from datetime import datetime
from hashlib import sha256
from pathlib import Path

from astropy.io import fits


def file_hash(path: Path) -> str:
    """Computes a file's SHA-256 in streaming mode."""
    h = sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def metadata(path: Path) -> dict:
    """Reads metadata from the FITS header for matching."""
    with fits.open(path, memmap=False) as hdul:
        h = hdul[0].header
        return {
            "date": datetime.fromisoformat(str(h["DATE-OBS"]).replace("Z", "+00:00")),
            "exptime": float(h["EXPTIME"]),
            "object": str(h.get("OBJECT", "")).strip(),
            "instrume": str(h.get("INSTRUME", "")).strip(),
        }


def same_metadata(a: dict, b: dict) -> bool:
    """Compares metadata of two frames."""
    delta = abs((a["date"] - b["date"]).total_seconds())
    return (
        delta <= 1.0
        and abs(a["exptime"] - b["exptime"]) <= 0.001
        and a["object"] == b["object"]
        and a["instrume"] == b["instrume"]
    )


def build_mapping(object_dir: Path) -> Path:
    """Creates or updates the mapping file."""
    mapping_path = object_dir / "astroprov_frame_mapping.json"

    # --- STEP 1: Create or read the mapping base ---
    if not mapping_path.exists():
        print("Mapping file not found. Creating from scratch...")
        mappings = []
    else:
        mapping_data = json.loads(mapping_path.read_text(encoding="utf-8"))
        mappings = mapping_data.get("mappings", [])
        if not mappings:
            print("Mapping file is empty. Filling the structure...")

    if not mappings:
        lights_dir = object_dir / "lights"
        light_files = sorted(lights_dir.glob("*.fits"))

        for f in light_files:
            mappings.append({
                "original": {
                    "path": f.relative_to(object_dir).as_posix(),
                    "sha256": file_hash(f)
                },
                "processed": {
                    "path": None,
                    "sha256": None
                }
            })

    # --- STEP 2: Match with processed files ---
    # Look for ALL files starting with r_ (aligned),
    # and exclude the final stack (stacked) and master frames.
    all_processed = (object_dir / "process").glob("r_*.fit")
    processed_files = sorted(
        [
            p for p in all_processed 
            if "stacked" not in p.stem.lower() 
            and "master" not in p.stem.lower()
            and "dark" not in p.stem.lower()
            and "bias" not in p.stem.lower()
            and "flat" not in p.stem.lower()
        ]
    )

    # Instead of failing hard, just warn
    if len(processed_files) != len(mappings):
        print(f"WARNING: {len(mappings)} originals in the base, "
              f"but {len(processed_files)} processed FITS found.")
        print("Continuing... Some frames may have been rejected by Siril.")

    processed_meta = {p: metadata(p) for p in processed_files}
    used = set()
    updates = []
    missing_count = 0

    for item in mappings:
        original = object_dir / item["original"]["path"]

        actual_original_hash = file_hash(original)
        if actual_original_hash != item["original"]["sha256"]:
            raise ValueError(f"Original frame hash changed: {original}")

        original_meta = metadata(original)
        matches = [
            p for p, m in processed_meta.items()
            if p not in used and same_metadata(original_meta, m)
        ]

        if len(matches) == 0:
            # Frame lost (rejected by Siril)
            print(f"  [MISSING] {original.name} — not found in process")
            item["processed"]["path"] = None
            item["processed"]["sha256"] = None
            item["matching"] = {"method": "NOT_FOUND", "date_obs_delta_seconds": None}
            missing_count += 1
            continue
            
        if len(matches) > 1:
            raise ValueError(
                f"Multiple matches found for {original.name}: {len(matches)}."
            )

        processed = matches[0]
        used.add(processed)
        delta = abs((original_meta["date"] - processed_meta[processed]["date"]).total_seconds())
        updates.append((item, processed, delta))

    # --- STEP 3: Write results ---
    if mapping_path.exists():
        backup = mapping_path.with_name("astroprov_frame_mapping.backup.json")
        shutil.copy2(mapping_path, backup)
        print(f"Backup: {backup}")

    for item, processed, delta in updates:
        item["processed"]["path"] = processed.relative_to(object_dir).as_posix()
        item["processed"]["sha256"] = file_hash(processed)
        item["matching"] = {
            "method": "DATE-OBS within 1 second + EXPTIME + OBJECT + INSTRUME",
            "date_obs_delta_seconds": round(delta, 6),
        }

    result = {
        "schema": "astroprov-frame-mapping-v1",
        "matching_count": len(mappings),
        "matched_count": len(updates),
        "missing_count": missing_count,
        "mappings": mappings
    }

    mapping_path.write_text(
        json.dumps(result, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    return mapping_path


def main() -> None:
    parser = argparse.ArgumentParser(description="Creating NINA <-> Siril mapping.")
    parser.add_argument("object_dir", type=Path, help="Object directory, e.g. NGC5906")
    args = parser.parse_args()

    try:
        path = build_mapping(args.object_dir.resolve())
        data = json.loads(path.read_text(encoding="utf-8"))
        
        print(f"\nMapping updated and saved: {path}")
        print(f"Total originals: {data['matching_count']}")
        print(f"Successfully matched: {data['matched_count']}")
        print(f"Lost (rejected): {data['missing_count']}")
        print("Source hashes verified.")
        
    except (OSError, ValueError, KeyError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()