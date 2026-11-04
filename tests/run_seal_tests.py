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

"""End-to-end tests for astroprov.cli.seal (standalone and collection)."""

import shutil
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

TEST_DIR = PROJECT_ROOT / "test_data" / "_seal_e2e_tmp"
M101_STACK = PROJECT_ROOT / "test_data" / "M101" / "process" / "r_pp_light__stacked.fit"
M101_RAW = PROJECT_ROOT / "test_data" / "M101"


def _setup_test_dir():
    """Prepare a clean temp dir and copy the M101 stack into it."""
    if TEST_DIR.exists():
        shutil.rmtree(TEST_DIR)
    TEST_DIR.mkdir(parents=True)
    
    if not M101_STACK.is_file():
        print(f"ERROR: M101 test data not found at {M101_STACK}")
        sys.exit(1)
        
    test_fits = TEST_DIR / "M101_test.fits"
    shutil.copy2(M101_STACK, test_fits)
    return test_fits


def _run_cmd(cmd: list[str]) -> tuple[bool, str]:
    print(f"\n$ {' '.join(cmd)}\n")
    result = subprocess.run(cmd, cwd=PROJECT_ROOT, capture_output=True, text=True)
    output = result.stdout + result.stderr
    return result.returncode == 0, output


def main():
    print("Setting up test environment (copying M101 stack)...")
    test_fits = _setup_test_dir()
    
    # --- Test 1: Standalone Mode ---
    print("\n" + "="*50)
    print("TEST 1: Standalone Sealing (Success Path)")
    print("="*50)
    
    cmd_seal = [
        sys.executable, "-m", "astroprov.cli.seal", str(test_fits), 
        "--object", "M101"
    ]
    ok, out = _run_cmd(cmd_seal)
    print(out)
    if not ok: return 1
    
    cmd_verify = [sys.executable, "-m", "astroprov.verify", str(test_fits)]
    ok, out = _run_cmd(cmd_verify)
    print(out)
    if not ok or "[OK] VERDICT: PASS" not in out:
        print("Standalone verify failed!")
        return 1

    # --- Test 2: Collection Mode ---
    print("\n" + "="*50)
    print("TEST 2: Collection Sealing (Success Path)")
    print("="*50)
    
    test_fits_collection = TEST_DIR / "M101_collection.fits"
    shutil.copy2(M101_STACK, test_fits_collection)
    
    cmd_seal = [
        sys.executable, "-m", "astroprov.cli.seal", str(test_fits_collection), 
        "--raw", str(M101_RAW), "--object", "M101"
    ]
    ok, out = _run_cmd(cmd_seal)
    print(out)
    if not ok: return 1

    cmd_verify = [
        sys.executable, "-m", "astroprov.verify", str(test_fits_collection), 
        "--raw-dir", str(M101_RAW)
    ]
    ok, out = _run_cmd(cmd_verify)
    print(out)
    if not ok or "[OK] VERDICT: PASS" not in out:
        print("Collection verify failed!")
        return 1
        
    print("\n" + "="*50)
    print("OVERALL: PASS (2/2 e2e tests)")
    return 0


if __name__ == "__main__":
    sys.exit(main())