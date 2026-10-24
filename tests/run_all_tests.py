"""Run all AstroProv full-auto pipeline test scenarios and print a PASS/FAIL summary."""

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from astroprov import fits_prov

# Test scenarios. Expected counts are from previous manual runs.
TESTS = [
    {
        "name": "M101 + WithoutFlat",
        "object_dir": "test_data/M101",
        "script": "siril_scripts/OSC_Preprocessing_AstroProv_WithoutFlat.ssf",
        "expected_frames": 37,
        "expected_passport": {
            "lights_count": 38,
            "darks_count": 20,
            "biases_count": 0,
            "flats_count": 0,
        },
    },
    {
        "name": "M101 + WithoutDBF",
        "object_dir": "test_data/M101",
        "script": "siril_scripts/OSC_Preprocessing_AstroProv_WithoutDBF.ssf",
        "expected_frames": 37,
        "expected_passport": {
            "lights_count": 38,
            "darks_count": 0,
            "biases_count": 0,
            "flats_count": 0,
        },
    },
    {
        "name": "SN 2026sqf + WithoutFlat",
        "object_dir": "test_data/SN 2026sqf",
        "script": "siril_scripts/Mono_Preprocessing_AstroProv_WithoutFlat.ssf",
        "expected_frames": 120,
        "expected_passport": {
            "lights_count": 120,
            "darks_count": 20,
            "biases_count": 0,
            "flats_count": 0,
        },
    },
    {
        "name": "SN 2026sqf + WithoutDBF",
        "object_dir": "test_data/SN 2026sqf",
        "script": "siril_scripts/Mono_Preprocessing_AstroProv_WithoutDBF.ssf",
        "expected_frames": 120,
        "expected_passport": {
            "lights_count": 120,
            "darks_count": 0,
            "biases_count": 0,
            "flats_count": 0,
        },
    },
]


def find_output_fits(object_dir: Path) -> Path | None:
    """Find the stacked output FITS in <object_dir>/process."""
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


def find_latest_processing_report(object_dir: Path) -> Path | None:
    """Find the newest processing-*.json in <object_dir>/.astroprov/runs/."""
    runs_dir = object_dir / ".astroprov" / "runs"
    if not runs_dir.is_dir():
        return None
    candidates = list(runs_dir.glob("processing-*.json"))
    if not candidates:
        return None
    return max(candidates, key=lambda p: p.stat().st_mtime)


def run_pipeline(test: dict) -> tuple[bool, str]:
    """Run full_auto_pipeline for one scenario.

    full_auto_pipeline already calls verify.verify_output() at the end
    and exits with its return code, so a non-zero exit code here means
    either the pipeline itself failed or verification failed.
    """
    cmd = [
        sys.executable, "-m", "astroprov.cli.full_auto_pipeline",
        test["object_dir"], "--script", test["script"],
    ]
    print(f"\n$ {' '.join(cmd)}\n")
    result = subprocess.run(cmd, cwd=PROJECT_ROOT)
    ok = result.returncode == 0
    return ok, f"exit code {result.returncode}"


def check_frame_count(object_dir: Path, expected: int) -> tuple[bool, str]:
    """Verify the stacked frame count matches the expected value."""
    report = find_latest_processing_report(object_dir)
    if report is None:
        return False, "no processing report found"

    data = json.loads(report.read_text(encoding="utf-8"))
    selected = data["sequence"]["selected_count"]
    cross = data.get("cross_check", {})
    fits_count = cross.get("fits_recorded_count")
    consistent = cross.get("consistent")

    problems = []
    if selected != expected:
        problems.append(f"selected {selected} != {expected}")
    if not consistent:
        problems.append(
            f"cross_check inconsistent (FITS={fits_count}, seq={selected})"
        )
    if fits_count != expected:
        problems.append(f"FITS STACKCNT {fits_count} != {expected}")

    if problems:
        return False, "; ".join(problems)
    return True, f"selected={selected}, FITS={fits_count}"


def check_passport(
    output_fits: Path | None, expected: dict
) -> tuple[bool, str]:
    """Verify the passport in the FITS matches expected calibration counts."""
    if output_fits is None:
        return False, "no output FITS"

    prov = fits_prov.read_provenance(output_fits)
    if prov is None:
        return False, "no passport in FITS"

    mismatches = []
    for key, expected_val in expected.items():
        actual = prov.get(key)
        if actual != expected_val:
            mismatches.append(f"{key}={actual} (expected {expected_val})")

    if mismatches:
        return False, "; ".join(mismatches)
    return True, "passport calibration counts match"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run all AstroProv full-auto test scenarios."
    )
    parser.add_argument(
        "--only",
        type=str,
        help="Run only tests whose name contains this substring",
    )
    args = parser.parse_args()

    overall_start = time.perf_counter()

    tests = TESTS
    if args.only:
        tests = [t for t in TESTS if args.only.lower() in t["name"].lower()]
        if not tests:
            print(f"No tests match: {args.only}")
            return 1

    results = []

    for idx, test in enumerate(tests, 1):
        print(f"\n{'='*70}")
        print(f"TEST {idx}/{len(tests)}: {test['name']}")
        print(f"{'='*70}")

        object_dir = (PROJECT_ROOT / test["object_dir"]).resolve()

        start_time = time.perf_counter()
        pipeline_ok, pipeline_msg = run_pipeline(test)
        duration = time.perf_counter() - start_time

        output_fits = find_output_fits(object_dir)
        if output_fits is None:
            pipeline_ok = False
            pipeline_msg = "output FITS not found after pipeline"

        frames_ok, frames_msg = check_frame_count(
            object_dir, test["expected_frames"]
        )
        passport_ok, passport_msg = check_passport(
            output_fits, test["expected_passport"]
        )

        test_ok = pipeline_ok and frames_ok and passport_ok
        results.append({
            "name": test["name"],
            "ok": test_ok,
            "duration": duration,
            "details": [
                # exit code 0 already includes verify.verify_output(),
                # so a separate verify run would be redundant.
                ("pipeline+verify", pipeline_ok, pipeline_msg),
                ("frames", frames_ok, frames_msg),
                ("passport", passport_ok, passport_msg),
            ],
        })

    print(f"\n{'='*70}")
    print("SUMMARY")
    print(f"{'='*70}\n")

    for r in results:
        status = "PASS" if r["ok"] else "FAIL"
        print(f"  [{status}] {r['name']} (took {r['duration']:.2f}s)")
        for name, ok, msg in r["details"]:
            mark = "yes" if ok else "no"
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