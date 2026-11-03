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

"""End-to-end tests for astroprov.cli.pipeline.

Covers three modes for both mono and OSC datasets:
  - auto (default parameters)
  - custom (non-default rej-sigma / rej-iters / normalize)
  - manual (--manual with automatic ENTER via stdin)

Requires real Siril CLI and test_data/ with source frames.
"""

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from astroprov import fits_prov


# ---------------------------------------------------------------- config ---

TESTS = [
    {
        "name": "M101 auto default",
        "object_dir": "test_data/M101",
        "script": "siril_scripts/OSC_Preprocessing_AstroProv_WithoutFlat.ssf",
        "mode": "auto",
        "expected_frames": 37,
        "expected_passport": {
            "lights_count": 38, "darks_count": 20,
            "biases_count": 0, "flats_count": 0,
        },
    },
    {
        "name": "M101 auto custom",
        "object_dir": "test_data/M101",
        "script": "siril_scripts/OSC_Preprocessing_AstroProv_WithoutFlat.ssf",
        "mode": "custom",
        "rej_sigma": 5, "rej_iters": 2, "normalize": True,
        "expected_frames": 37,
        "expected_passport": {
            "lights_count": 38, "darks_count": 20,
            "biases_count": 0, "flats_count": 0,
        },
    },
    {
        "name": "M101 manual",
        "object_dir": "test_data/M101",
        "script": "siril_scripts/OSC_Preprocessing_AstroProv_WithoutFlat.ssf",
        "mode": "manual",
        "expected_frames": 37,
        "expected_passport": {
            "lights_count": 38, "darks_count": 20,
            "biases_count": 0, "flats_count": 0,
        },
    },
    {
        "name": "SN 2026sqf auto default",
        "object_dir": "test_data/SN 2026sqf",
        "script": "siril_scripts/Mono_Preprocessing_AstroProv_WithoutFlat.ssf",
        "mode": "auto",
        "expected_frames": 120,
        "expected_passport": {
            "lights_count": 120, "darks_count": 20,
            "biases_count": 0, "flats_count": 0,
        },
    },
    {
        "name": "SN 2026sqf auto custom",
        "object_dir": "test_data/SN 2026sqf",
        "script": "siril_scripts/Mono_Preprocessing_AstroProv_WithoutFlat.ssf",
        "mode": "custom",
        "rej_sigma": 4, "rej_iters": 1, "normalize": False,
        "expected_frames": 120,
        "expected_passport": {
            "lights_count": 120, "darks_count": 20,
            "biases_count": 0, "flats_count": 0,
        },
    },
    {
        "name": "SN 2026sqf manual",
        "object_dir": "test_data/SN 2026sqf",
        "script": "siril_scripts/Mono_Preprocessing_AstroProv_WithoutFlat.ssf",
        "mode": "manual",
        "expected_frames": 120,
        "expected_passport": {
            "lights_count": 120, "darks_count": 20,
            "biases_count": 0, "flats_count": 0,
        },
    },
]


# --------------------------------------------------------------- helpers ---

def _find_output_fits(object_dir: Path) -> Path | None:
    process_dir = object_dir / "process"
    if not process_dir.is_dir():
        return None
    candidates = [
        f for f in process_dir.glob("*.fit")
        if "stacked" in f.stem.lower()
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda p: p.stat().st_mtime)


def _find_latest_processing_report(object_dir: Path) -> Path | None:
    runs_dir = object_dir / ".astroprov" / "runs"
    if not runs_dir.is_dir():
        return None
    candidates = list(runs_dir.glob("processing-*.json"))
    if not candidates:
        return None
    return max(candidates, key=lambda p: p.stat().st_mtime)


def _build_command(test: dict) -> list[str]:
    cmd = [
        sys.executable, "-m", "astroprov.cli.pipeline",
        test["object_dir"], "--script", test["script"],
    ]
    if test["mode"] == "manual":
        cmd.append("--manual")
    elif test["mode"] == "custom":
        if "rej_sigma" in test:
            cmd.extend(["--rej-sigma", str(test["rej_sigma"])])
        if "rej_iters" in test:
            cmd.extend(["--rej-iters", str(test["rej_iters"])])
        if test.get("normalize"):
            cmd.append("--normalize")
    return cmd


# ---------------------------------------------------------- pipeline run ---

def _run_pipeline(test: dict) -> tuple[bool, str]:
    cmd = _build_command(test)
    print(f"\n$ {' '.join(cmd)}\n")

    if test["mode"] == "manual":
        # Auto-ENTER so the pause at step 5 does not block.
        result = subprocess.run(
            cmd, cwd=PROJECT_ROOT, input="\n", text=True, errors="replace",
        )
    else:
        result = subprocess.run(cmd, cwd=PROJECT_ROOT)

    ok = result.returncode == 0
    return ok, f"exit code {result.returncode}"


# ------------------------------------------------------------- checks ---

def _check_frame_count(object_dir: Path, expected: int) -> tuple[bool, str]:
    report_path = _find_latest_processing_report(object_dir)
    if report_path is None:
        return False, "no processing report found"

    data = json.loads(report_path.read_text(encoding="utf-8"))
    selected = data["sequence"]["selected_count"]
    cross = data.get("cross_check", {})
    fits_count = cross.get("fits_recorded_count")
    consistent = cross.get("consistent")

    problems = []
    if selected != expected:
        problems.append(f"selected {selected} != {expected}")
    if not consistent:
        problems.append(f"cross_check inconsistent (FITS={fits_count}, seq={selected})")
    if fits_count != expected:
        problems.append(f"FITS STACKCNT {fits_count} != {expected}")

    if problems:
        return False, "; ".join(problems)
    return True, f"selected={selected}, FITS={fits_count}"


def _check_stack_command(object_dir: Path, test: dict) -> tuple[bool, str]:
    """Verify the recorded stack command reflects the mode/parameters used."""
    if test["mode"] == "auto":
        return True, "skipped (auto)"

    report_path = _find_latest_processing_report(object_dir)
    if report_path is None:
        return False, "no processing report found"

    data = json.loads(report_path.read_text(encoding="utf-8"))
    cmd = data.get("stack_command")
    if not cmd:
        return False, "stack_command is missing in report"

    problems = []
    if test["mode"] == "custom":
        sigma = test.get("rej_sigma", 3)
        iters = test.get("rej_iters", 3)
        normalize = test.get("normalize", False)

        if f"rej {float(sigma)} {iters}" not in cmd and f"rej {sigma} {iters}" not in cmd:
            problems.append(f"rej params missing in: {cmd!r}")
        if normalize and "-nonorm" in cmd:
            problems.append(f"normalize enabled but -nonorm present: {cmd!r}")
        if not normalize and "-nonorm" not in cmd:
            problems.append(f"normalize disabled but -nonorm missing: {cmd!r}")

    if problems:
        return False, "; ".join(problems)
    return True, f"recorded: {cmd}"


def _check_passport(output_fits: Path | None, test: dict) -> tuple[bool, str]:
    if output_fits is None:
        return False, "no output FITS"

    prov = fits_prov.read_provenance(output_fits)
    if prov is None:
        return False, "no passport in FITS"

    problems = []

    if prov.get("schema") != "astroprov-processing-v2":
        problems.append(f"schema={prov.get('schema')!r}")

    for key, expected_val in test["expected_passport"].items():
        actual = prov.get(key)
        if actual != expected_val:
            problems.append(f"{key}={actual} (expected {expected_val})")

    report_path = _find_latest_processing_report(output_fits.parent.parent)
    if report_path is not None:
        report = json.loads(report_path.read_text(encoding="utf-8"))
        if prov.get("date") != report.get("created_utc"):
            problems.append(
                f"passport date {prov.get('date')!r} "
                f"!= report created_utc {report.get('created_utc')!r}"
            )

    if problems:
        return False, "; ".join(problems)
    return True, "schema, counts and date match"


# ---------------------------------------------------------------- main ---

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run end-to-end tests for astroprov.cli.pipeline."
    )
    parser.add_argument(
        "--only", type=str,
        help="Run only tests whose name contains this substring",
    )
    args = parser.parse_args()

    tests = TESTS
    if args.only:
        tests = [t for t in TESTS if args.only.lower() in t["name"].lower()]
        if not tests:
            print(f"No tests match: {args.only}")
            return 1

    overall_start = time.perf_counter()
    results = []

    for idx, test in enumerate(tests, 1):
        print(f"\n{'='*70}")
        print(f"TEST {idx}/{len(tests)}: {test['name']}  [{test['mode']}]")
        print(f"{'='*70}")

        object_dir = (PROJECT_ROOT / test["object_dir"]).resolve()

        start = time.perf_counter()
        pipeline_ok, pipeline_msg = _run_pipeline(test)
        duration = time.perf_counter() - start

        output_fits = _find_output_fits(object_dir)
        if output_fits is None:
            pipeline_ok = False
            pipeline_msg = "output FITS not found after pipeline"

        frames_ok, frames_msg = _check_frame_count(
            object_dir, test["expected_frames"]
        )
        stack_ok, stack_msg = _check_stack_command(object_dir, test)
        passport_ok, passport_msg = _check_passport(output_fits, test)

        test_ok = pipeline_ok and frames_ok and stack_ok and passport_ok
        results.append({
            "name": test["name"],
            "mode": test["mode"],
            "ok": test_ok,
            "duration": duration,
            "details": [
                ("pipeline+verify", pipeline_ok, pipeline_msg),
                ("frames", frames_ok, frames_msg),
                ("stack_command", stack_ok, stack_msg),
                ("passport", passport_ok, passport_msg),
            ],
        })

    print(f"\n{'='*70}")
    print("SUMMARY")
    print(f"{'='*70}\n")

    for r in results:
        status = "PASS" if r["ok"] else "FAIL"
        print(f"  [{status}] {r['name']} [{r['mode']}] (took {r['duration']:.2f}s)")
        for name, ok, msg in r["details"]:
            mark = "yes" if ok else "no "
            print(f"           {mark} {name}: {msg}")
        print()

    passed = sum(1 for r in results if r["ok"])
    total = len(results)
    overall_duration = time.perf_counter() - overall_start

    print(f"{'='*70}")
    if passed == total:
        print(f"OVERALL: PASS ({passed}/{total} tests) in {overall_duration:.2f}s")
        return 0
    print(f"OVERALL: FAIL ({passed}/{total} tests passed) in {overall_duration:.2f}s")
    return 1


if __name__ == "__main__":
    sys.exit(main())