"""Run tamper-resistance tests (A2) against the AstroProv passport.

For each passport field, the script:
  1. Backs up the FITS file.
  2. Mutates the field in the PROVENANCE HDU.
  3. Runs `python -m astroprov.verify` and expects a FAIL (non-zero exit).
  4. Restores the backup.
  5. Runs verify again and expects a PASS (zero exit).
"""

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

from astropy.io import fits


PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Fields in the PROVENANCE HDU and the value type used for mutation.
# Each tuple: (header_key, label, mutated_value)
TAMPER_TARGETS = [
    ("PROVLIT", "lights_count", 999),
    ("PROVDRK", "darks_count", 999),
    ("PROVBIA", "biases_count", 999),
    ("PROVFLT", "flats_count", 999),
    ("PROVEXP", "exptime", 999.0),
    ("PROVMRKL", "merkle_root", "0" * 64),
    ("PROVID", "passport_id", "00000000-0000-0000-0000-000000000000"),
]


def read_field(fits_path: Path, key: str) -> any:
    """Read a single field from the PROVENANCE HDU."""
    with fits.open(fits_path, mode="readonly") as hdul:
        return hdul["PROVENANCE"].header.get(key)


def run_verify(fits_path: Path, verbose: bool = False) -> tuple[bool, str]:
    """Run verify on the FITS file, return (is_pass, output)."""
    cmd = [sys.executable, "-m", "astroprov.verify", str(fits_path)]
    result = subprocess.run(
        cmd,
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
    )
    output = result.stdout + result.stderr
    is_pass = result.returncode == 0
    
    if verbose:
        print(f"      [debug] verify exit code: {result.returncode}")
        # Выводим только строки, относящиеся к подписи или паспорту
        for line in output.splitlines():
            if any(kw in line for kw in ("[OK]", "[FAIL]", "VERDICT", "Passport")):
                print(f"      [debug] {line}")
                
    return is_pass, output


def mutate_fits(fits_path: Path, key: str, value) -> None:
    """Overwrite a PROVENANCE header key in-place."""
    with fits.open(fits_path, mode="update") as hdul:
        hdul["PROVENANCE"].header[key] = (value, "")
        hdul.flush()


def test_field(fits_path: Path, key: str, label: str, value, verbose: bool = False) -> tuple[bool, str]:
    """Run one tamper scenario. Returns (ok, message)."""
    backup = fits_path.with_suffix(fits_path.suffix + ".sec_bak")
    
    # Создаем бэкап перед началом
    shutil.copy2(fits_path, backup)
    
    try:
        # 1. Tamper
        old_val = read_field(fits_path, key)
        mutate_fits(fits_path, key, value)
        new_val = read_field(fits_path, key)
        
        if verbose:
            print(f"      [debug] {key}: {old_val!r} -> {new_val!r}")
            if old_val == new_val:
                print(f"      [debug] WARNING: value did not change in file!")

        # 2. Verify — expect FAIL
        is_pass, output = run_verify(fits_path, verbose=verbose)
        if is_pass:
            return False, "tampered file passed verification (vulnerability!)"

        # Check that FAIL was due to signature/ID mismatch, not a random crash
        if "Digital signature is INVALID" not in output and "Passport ID mismatch" not in output:
            return False, "verify failed, but not on signature/ID check"

        # 3. Restore
        shutil.copy2(backup, fits_path)
        restored_val = read_field(fits_path, key)
        if verbose:
            print(f"      [debug] {key} restored to: {restored_val!r}")

        # 4. Verify — expect PASS
        is_pass, output = run_verify(fits_path, verbose=False)
        if not is_pass:
            return False, "restored file failed verification (rollback broken)"

        return True, "FAIL on tamper, PASS on restore"

    finally:
        # Guarantee the file is restored even if an exception occurred or early return
        if backup.exists():
            shutil.copy2(backup, fits_path)
            backup.unlink()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run A2 tamper-resistance tests against a FITS passport."
    )
    parser.add_argument(
        "fits",
        type=Path,
        help="Path to the stacked output FITS to test",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Show detailed debug output",
    )
    args = parser.parse_args()

    fits_path = args.fits.resolve()
    if not fits_path.is_file():
        print(f"ERROR: FITS file not found: {fits_path}")
        return 1

    print(f"\n{'='*70}")
    print(f"A2 TAMPER-RESISTANCE TESTS: {fits_path.name}")
    print(f"{'='*70}\n")

    results = []

    for key, label, value in TAMPER_TARGETS:
        print(f"[*] Tampering {key} ({label}) -> {value!r} ...")
        ok, msg = test_field(fits_path, key, label, value, verbose=args.verbose)
        status = "PASS" if ok else "FAIL"
        print(f"    [{status}] {msg}\n")
        results.append((key, label, ok, msg))

    # Summary
    print(f"{'='*70}")
    print("SUMMARY")
    print(f"{'='*70}\n")

    for key, label, ok, msg in results:
        status = "PASS" if ok else "FAIL"
        print(f"  [{status}] {key} ({label}): {msg}")

    passed = sum(1 for r in results if r[2])
    total = len(results)

    print(f"\n{'='*70}")
    if passed == total:
        print(f"OVERALL: PASS ({passed}/{total} fields protected)")
        return 0
    print(f"OVERALL: FAIL ({passed}/{total} fields protected)")
    return 1


if __name__ == "__main__":
    sys.exit(main())