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

"""Viewing the AstroProv passport (from FITS + manifest)."""

import argparse
import json
import sys
from pathlib import Path

from astropy.io import fits
from astroprov import fits_prov


SEP = "=" * 60


def find_manifest_by_id(object_dir: Path | str, passport_id: str) -> Path | None:
    """Find the JSON manifest by its ID in the hidden .astroprov folder."""
    object_dir = Path(object_dir).resolve()
    manifests_dir = object_dir / ".astroprov" / "manifests"

    if not manifests_dir.is_dir():
        return None

    manifest_path = manifests_dir / f"{passport_id}.json"
    if manifest_path.is_file():
        return manifest_path

    return None


def _safe_float(value: object) -> float | None:
    """Convert a value to float, or return None if impossible."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _safe_int(value: object) -> int | None:
    """Convert a value to int, or return None if impossible."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def format_time(seconds: float | None) -> str:
    """Format seconds into a readable form (hours, minutes)."""
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


def _print_composition(
    lights: int,
    darks: int,
    biases: int,
    flats: int,
    source: str,
) -> None:
    """Print the source data composition block."""
    print(f"\nSOURCE DATA COMPOSITION ({source})")
    print(f"  Lights:     {lights}")
    print(f"  Darks:      {darks}")
    print(f"  Biases:     {biases}")
    print(f"  Flats:      {flats}")
    total = lights + darks + biases + flats
    print(f"  {'Total:':<12} {total} files")


def inspect_fits(fits_path: Path | str) -> int:
    """Read the passport from FITS, find the manifest and print a report."""
    fits_path = Path(fits_path).resolve()

    print(f"\n{SEP}")
    print(f"ASTROPROV PASSPORT: {fits_path.name}")
    print(f"{SEP}\n")

    if not fits_path.is_file():
        print(f"ERROR: file not found: {fits_path}")
        return 1

    prov = fits_prov.read_provenance(fits_path)

    if prov is None:
        print("ERROR: this FITS file has no passport (HDU 'PROVENANCE').")
        return 1

    passport_id = prov.get("id")

    exptime_hdr = None
    stack_count = None
    try:
        with fits.open(fits_path, mode="readonly") as hdul:
            header = hdul[0].header
            exptime_hdr = header.get("EXPTIME")
            stack_count = header.get("STACKCNT") or header.get("NCOMBINE")
    except (OSError, ValueError, TypeError, KeyError) as exc:
        print(f"[WARNING] Could not read FITS header: {exc}")

    # Backward compatibility: older layouts have the manifest one level up.
    manifest_path = None
    if passport_id:
        manifest_path = find_manifest_by_id(fits_path.parent.parent, passport_id)
        if manifest_path is None:
            manifest_path = find_manifest_by_id(fits_path.parent, passport_id)

    print("GENERAL INFO")
    print(f"  Passport ID:      {passport_id or '—'}")
    print(f"  Created:          {prov.get('date', '—')}")
    print(f"  Format version:   {prov.get('version', '—')}")
    print(f"  Tool:             {prov.get('tool', '—')}")

    print("\nSOURCE DATA MERKLE ROOT (SHA-256)")
    print(f"  {prov.get('merkle_root', '—')}")

    lights_cnt = prov.get("lights_count")

    if lights_cnt is not None:
        _print_composition(
            lights=_safe_int(lights_cnt) or 0,
            darks=_safe_int(prov.get("darks_count")) or 0,
            biases=_safe_int(prov.get("biases_count")) or 0,
            flats=_safe_int(prov.get("flats_count")) or 0,
            source="from FITS passport",
        )
    elif manifest_path is not None:
        try:
            manifest_data = json.loads(manifest_path.read_text(encoding="utf-8"))
            cats = manifest_data.get("categories", {})

            m_lights = _safe_int(cats.get("lights", {}).get("file_count")) or 0
            m_darks = _safe_int(cats.get("darks", {}).get("file_count")) or 0
            m_biases = (
                (_safe_int(cats.get("bias", {}).get("file_count")) or 0)
                + (_safe_int(cats.get("biases", {}).get("file_count")) or 0)
            )
            m_flats = _safe_int(cats.get("flats", {}).get("file_count")) or 0

            _print_composition(
                lights=m_lights,
                darks=m_darks,
                biases=m_biases,
                flats=m_flats,
                source="from manifest, legacy format",
            )
            lights_cnt = m_lights
        except (OSError, ValueError, TypeError, KeyError) as exc:
            print(f"\n  [WARNING] Could not read manifest: {exc}")
    else:
        print("\nSOURCE DATA COMPOSITION")
        print("  [NO DATA] This FITS has no statistics, and the manifest was not found.")

    exptime_val = _safe_float(prov.get("exptime"))
    if exptime_val is None:
        exptime_val = _safe_float(exptime_hdr)

    if exptime_val is not None and exptime_val > 0:
        print("\nEXPOSURE")
        print(f"  Single frame:       {exptime_val} s")

        frames_count = _safe_int(stack_count)
        if (not frames_count or frames_count == 0) and lights_cnt:
            frames_count = _safe_int(lights_cnt)

        if frames_count and frames_count > 0:
            total_exp = exptime_val * frames_count
            print(f"  Frames in stack:     {frames_count}")
            print(f"  Total:               {format_time(total_exp)}")
        else:
            print(
                "  [NO DATA] FITS header has no STACKCNT or NCOMBINE, "
                "and passport has no Lights count."
            )
    else:
        print("\nEXPOSURE")
        print("  [NO DATA] Could not determine exposure time from FITS header or passport.")

    print(f"\n{SEP}")
    print("Integrity check: use `python -m astroprov.verify`")
    print(f"{SEP}\n")

    return 0


def inspect_directory(directory: Path | str) -> int:
    """Scan a directory and show passports of all FITS files in it."""
    directory = Path(directory).resolve()

    if not directory.is_dir():
        print(f"ERROR: {directory} is not a directory")
        return 1

    fits_files = sorted(directory.glob("*.fit")) + sorted(directory.glob("*.fits"))

    if not fits_files:
        print(f"No FITS files in {directory}.")
        return 1

    print(f"\n{SEP}")
    print(f"PASSPORT SCAN IN DIRECTORY: {directory}")
    print(f"{SEP}\n")

    with_passport = 0
    total = 0

    for f in fits_files:
        total += 1
        prov = fits_prov.read_provenance(f)
        if prov is not None:
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
    return inspect_directory(target)


if __name__ == "__main__":
    sys.exit(main())