"""Creating and updating the mapping between original NINA frames and processed Siril files."""

import argparse
import json
import shutil
import sys
from datetime import datetime
from pathlib import Path

from astropy.io import fits
from astroprov.utils import sha256_file


def metadata(path: Path | str) -> dict:
    """Reads metadata from the FITS header for matching."""
    path = Path(path)
    with fits.open(path, memmap=False) as hdul:
        h = hdul[0].header
        
        date_obs = h.get("DATE-OBS")
        exptime = h.get("EXPTIME")
        
        if not date_obs or exptime is None:
            raise ValueError(f"Missing DATE-OBS or EXPTIME in {path.name}")

        return {
            "date": datetime.fromisoformat(str(date_obs).replace("Z", "+00:00")),
            "exptime": float(exptime),
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


def _is_valid_processed_file(path: Path) -> bool:
    """Checks if a processed file is a valid aligned frame (not a master or stack)."""
    stem = path.stem.lower()
    invalid_keywords = ["stacked", "master", "dark", "bias", "flat"]
    return not any(kw in stem for kw in invalid_keywords)


def build_mapping(object_dir: Path | str) -> Path:
    """Creates or updates the mapping file."""
    object_dir = Path(object_dir).resolve()
    mapping_path = object_dir / "astroprov_frame_mapping.json"

    # --- STEP 1: Create or read the mapping base ---
    if not mapping_path.exists():
        print("  Mapping file not found. Creating from scratch...")
        mappings = []
    else:
        try:
            mapping_data = json.loads(mapping_path.read_text(encoding="utf-8"))
            mappings = mapping_data.get("mappings", [])
            if not mappings:
                print("  Mapping file is empty. Filling the structure...")
        except json.JSONDecodeError:
            print("  ERROR: Mapping file is corrupted. Creating from scratch...")
            mappings = []

    if not mappings:
        lights_dir = object_dir / "lights"
        light_files = sorted(lights_dir.glob("*.fits"))

        for f in light_files:
            mappings.append({
                "original": {
                    "path": f.relative_to(object_dir).as_posix(),
                    "sha256": sha256_file(f)
                },
                "processed": {
                    "path": None,
                    "sha256": None
                }
            })

    # --- STEP 2: Match with processed files ---
    all_processed = (object_dir / "process").glob("r_*.fit")
    processed_files = sorted([p for p in all_processed if _is_valid_processed_file(p)])

    if len(processed_files) != len(mappings):
        print(f"  WARNING: {len(mappings)} original frames, "
              f"but {len(processed_files)} processed FITS found.")
        print("  Continuing... Some frames may have been rejected by Siril.")

    processed_meta = {p: metadata(p) for p in processed_files}
    used = set()
    updates = []
    missing_count = 0

    for item in mappings:
        original_path = item["original"]["path"]
        original = (object_dir / original_path).resolve()

        # SECURITY: Prevent path traversal (e.g. "../../etc/passwd")
        if not original.is_relative_to(object_dir):
            raise ValueError(f"Original frame path escapes object directory: {original_path}")

        actual_original_hash = sha256_file(original)
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
        delta = abs(
            (original_meta["date"] - processed_meta[processed]["date"])
            .total_seconds()
        )
        updates.append((item, processed, delta))

    # --- STEP 3: Write results ---
    if mapping_path.exists():
        backup = mapping_path.with_name("astroprov_frame_mapping.backup.json")
        shutil.copy2(mapping_path, backup)
        print(f"  Backup: {backup}")

    for item, processed, delta in updates:
        item["processed"]["path"] = processed.relative_to(object_dir).as_posix()
        item["processed"]["sha256"] = sha256_file(processed)
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
        path = build_mapping(args.object_dir)  # resolve() is handled inside the function
        data = json.loads(path.read_text(encoding="utf-8"))
        
        print(f"\n  Mapping updated and saved: {path}")
        print(f"  Total originals: {data['matching_count']}")
        print(f"  Successfully matched: {data['matched_count']}")
        print(f"  Lost (rejected): {data['missing_count']}")
        print("  Source hashes verified.")
        
    except (OSError, ValueError, KeyError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()