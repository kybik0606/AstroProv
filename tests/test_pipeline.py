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

"""Unit tests for astroprov.cli.pipeline (no Siril required)."""

import json
import os
import time
from pathlib import Path

import pytest
from astropy.io import fits

from astroprov.cli import pipeline


# ---------------------------------------------------------------- helpers ---

def make_fits(path: Path, exptime: float | None = 10.0) -> Path:
    hdu = fits.PrimaryHDU()
    if exptime is not None:
        hdu.header["EXPTIME"] = exptime
    hdu.writeto(path, overwrite=True)
    return path


def make_seq(path: Path, name: str) -> Path:
    path.write_text(f"S '{name}'\n", encoding="utf-8")
    return path


def _cat(count: int, root: str = "00" * 32) -> dict:
    return {"file_count": count, "merkle_root": root}


CALIB_ALL = {"uses_darks": True, "uses_biases": True, "uses_flats": True}
CALIB_NONE = {"uses_darks": False, "uses_biases": False, "uses_flats": False}


# ------------------------------------------------------- clean_process_dir ---

def test_clean_process_dir_clears_all_content(tmp_path):
    process = tmp_path / "process"
    process.mkdir()
    (process / "file.fit").write_text("x")
    sub = process / "sub"
    sub.mkdir()
    (sub / "inner.txt").write_text("y")

    pipeline.clean_process_dir(tmp_path)

    assert process.exists()
    assert list(process.iterdir()) == []


def test_clean_process_dir_noop_if_missing(tmp_path):
    pipeline.clean_process_dir(tmp_path)
    assert not (tmp_path / "process").exists()


# ----------------------------------------------------------- _safe_exptime ---

def test_safe_exptime_valid(tmp_path):
    f = make_fits(tmp_path / "a.fits", exptime=120.0)
    assert pipeline._safe_exptime(f) == 120.0


def test_safe_exptime_missing_key(tmp_path):
    f = make_fits(tmp_path / "a.fits", exptime=None)
    assert pipeline._safe_exptime(f) == 0.0


def test_safe_exptime_invalid_string(tmp_path):
    hdu = fits.PrimaryHDU()
    hdu.header["EXPTIME"] = "unknown"
    f = tmp_path / "a.fits"
    hdu.writeto(f)
    assert pipeline._safe_exptime(f) == 0.0


def test_safe_exptime_missing_file(tmp_path):
    assert pipeline._safe_exptime(tmp_path / "nonexistent.fits") == 0.0


# --------------------------------------------------------- _find_sequence ---

def test_find_sequence_prefers_r(tmp_path):
    make_seq(tmp_path / "other.seq", "other")
    r_seq = make_seq(tmp_path / "r_pp_light_.seq", "r_pp_light_")
    assert pipeline._find_sequence(tmp_path) == r_seq


def test_find_sequence_falls_back(tmp_path):
    s = make_seq(tmp_path / "custom.seq", "custom")
    assert pipeline._find_sequence(tmp_path) == s


def test_find_sequence_picks_newest(tmp_path):
    s1 = make_seq(tmp_path / "r_a.seq", "r_a")
    s2 = make_seq(tmp_path / "r_b.seq", "r_b")
    now = time.time()
    os.utime(s1, (now - 10, now - 10))
    os.utime(s2, (now, now))
    assert pipeline._find_sequence(tmp_path) == s2


def test_find_sequence_raises(tmp_path):
    with pytest.raises(FileNotFoundError, match="No .seq file"):
        pipeline._find_sequence(tmp_path)


# --------------------------------------------------- _extract_source_stats ---

def test_extract_stats_full():
    data = {"categories": {
        "lights": _cat(37, "ab" * 32),
        "darks": _cat(20),
        "bias": _cat(15),
        "biases": _cat(5),
        "flats": _cat(10),
    }}
    stats = pipeline._extract_source_stats(data, CALIB_ALL)
    assert stats["merkle_root"] == "ab" * 32
    assert stats["lights_count"] == 37
    assert stats["darks_count"] == 20
    assert stats["biases_count"] == 20
    assert stats["flats_count"] == 10


def test_extract_stats_respects_flags():
    data = {"categories": {
        "lights": _cat(100),
        "darks": _cat(20),
        "bias": _cat(15),
        "flats": _cat(10),
    }}
    stats = pipeline._extract_source_stats(data, CALIB_NONE)
    assert stats["lights_count"] == 100
    assert stats["darks_count"] == 0
    assert stats["biases_count"] == 0
    assert stats["flats_count"] == 0


def test_extract_stats_raises_without_lights():
    data = {"categories": {"darks": _cat(20)}}
    with pytest.raises(ValueError, match="no 'lights' category"):
        pipeline._extract_source_stats(data, CALIB_ALL)


def test_extract_stats_missing_optional():
    data = {"categories": {"lights": _cat(10)}}
    stats = pipeline._extract_source_stats(data, CALIB_ALL)
    assert stats["darks_count"] == 0
    assert stats["biases_count"] == 0
    assert stats["flats_count"] == 0


# -------------------------------------------------------- _find_final_fits ---

def test_find_final_fits_exact(tmp_path):
    make_fits(tmp_path / "my_stacked.fit")
    make_fits(tmp_path / "other.fit")
    assert pipeline._find_final_fits(tmp_path, "my_stacked").name == "my_stacked.fit"


def test_find_final_fits_case_insensitive(tmp_path):
    make_fits(tmp_path / "My_Stacked.FIT")
    assert pipeline._find_final_fits(tmp_path, "my_stacked").name == "My_Stacked.FIT"


def test_find_final_fits_fallback(tmp_path):
    make_fits(tmp_path / "random_stacked.fit")
    assert pipeline._find_final_fits(tmp_path, "expected").name == "random_stacked.fit"


def test_find_final_fits_raises(tmp_path):
    make_fits(tmp_path / "unrelated.fit")
    with pytest.raises(FileNotFoundError):
        pipeline._find_final_fits(tmp_path, "expected")


# -------------------------------------------------------------- seq regex ---

@pytest.mark.parametrize("name", [
    "r_pp_light_", "stack1", "test-file", "test.file", "ABC_123", "a",
])
def test_seq_name_valid(name):
    assert pipeline.SEQ_NAME_RE.fullmatch(name) is not None


@pytest.mark.parametrize("name", [
    "with space", "with'quote", 'with"quote', "with\\backslash",
    "with;sem", "with/slash", "",
])
def test_seq_name_invalid(name):
    assert pipeline.SEQ_NAME_RE.fullmatch(name) is None


# --------------------------------------------------------------- main() ---

def test_main_fails_if_object_dir_missing(monkeypatch, tmp_path):
    script = tmp_path / "s.ssf"
    script.write_text("x")
    monkeypatch.setattr("sys.argv", [
        "pipeline", str(tmp_path / "nope"), "--script", str(script),
    ])
    assert pipeline.main() == 1


def test_main_fails_if_script_missing(monkeypatch, tmp_path):
    obj = tmp_path / "M42"
    obj.mkdir()
    monkeypatch.setattr("sys.argv", [
        "pipeline", str(obj), "--script", str(tmp_path / "nope.ssf"),
    ])
    assert pipeline.main() == 1


def test_main_parses_cli_flags_correctly(monkeypatch, tmp_path):
    obj = tmp_path / "M42"
    obj.mkdir()
    script = tmp_path / "s.ssf"
    script.write_text("x")

    captured = {}
    def fake_run_pipeline(*args, **kwargs):
        captured.update(kwargs)
        return 0

    monkeypatch.setattr(pipeline, "run_pipeline", fake_run_pipeline)
    monkeypatch.setattr(pipeline, "find_siril_cli", lambda arg: Path("/fake"))
    monkeypatch.setattr("sys.argv", [
        "pipeline", str(obj), "--script", str(script),
        "--manual", "--rej-sigma", "5", "--rej-iters", "2", "--normalize",
    ])

    assert pipeline.main() == 0
    assert captured["manual"] is True
    assert captured["rej_sigma"] == 5
    assert captured["rej_iters"] == 2
    assert captured["normalize"] is True


# ------------------------------------------ run_pipeline with all mocked ---

def _setup_common_mocks(monkeypatch, tmp_path, obj, fake_script):
    """Set up mocks shared by all run_pipeline integration tests."""
    siril_calls = []

    def fake_run_siril(exe, script, work_dir, action_name, **kw):
        siril_calls.append(action_name)
        process = Path(work_dir) / "process"
        process.mkdir(exist_ok=True)
        if action_name == "Calibration and Alignment":
            make_seq(process / "r_pp_light_.seq", "r_pp_light_")
        elif action_name == "Stacking":
            make_fits(process / "r_pp_light__stacked.fit", exptime=600.0)

    monkeypatch.setattr(pipeline, "run_siril_cli", fake_run_siril)
    monkeypatch.setattr(
        pipeline, "analyze_script_calibration",
        lambda p: {"uses_darks": False, "uses_biases": False, "uses_flats": False},
    )

    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps({
        "passport_id": "test-passport-id",
        "categories": {"lights": {"file_count": 1, "merkle_root": "cd" * 32}},
    }))
    monkeypatch.setattr(pipeline.snapshot, "create_manifest", lambda o: manifest_path)
    monkeypatch.setattr(pipeline.mapping, "build_mapping", lambda o: tmp_path / "mapping.json")

    monkeypatch.setattr(
        pipeline.processing, "build_pre_stack_report",
        lambda **kw: {"status": "ready", "created_utc": "2026-01-01T00:00:00+00:00"},
    )
    monkeypatch.setattr(
        pipeline.processing, "build_report",
        lambda **kw: {"created_utc": "2026-01-01T00:00:05+00:00"},
    )
    monkeypatch.setattr(
        pipeline.processing, "save_report",
        lambda report, path: Path(path).write_text(json.dumps(report)) or path,
    )
    monkeypatch.setattr(
        pipeline.processing, "timestamp_for_filename", lambda: "20260101T000000Z",
    )

    monkeypatch.setattr(pipeline.signature, "generate_keys", lambda d: None)
    monkeypatch.setattr(
        pipeline.signature, "get_public_key_fingerprint", lambda d: "0123456789abcdef",
    )
    monkeypatch.setattr(
        pipeline.signature, "sign_data", lambda data, d: "ab" * 64,
    )
    monkeypatch.setattr(pipeline.timestamping, "stamp_data", lambda d, p: None)
    monkeypatch.setattr(pipeline.verify, "verify_output", lambda o, r: 0)

    return siril_calls


def _make_object_dir(tmp_path):
    obj = tmp_path / "M42"
    obj.mkdir()
    (obj / "lights").mkdir()
    make_fits(obj / "lights" / "l.fits", exptime=600.0)
    script = tmp_path / "s.ssf"
    script.write_text("requires 1.2.0")
    return obj, script


def test_run_pipeline_auto_mode_executes_all_steps(monkeypatch, tmp_path):
    obj, script = _make_object_dir(tmp_path)
    siril_calls = _setup_common_mocks(monkeypatch, tmp_path, obj, script)

    captured = {}
    monkeypatch.setattr(
        pipeline.fits_prov, "write_provenance",
        lambda fits_path, prov_data: captured.update(prov_data),
    )

    def fail_if_called(_):
        raise AssertionError("input_func must not be called in auto mode")

    exit_code = pipeline.run_pipeline(
        object_dir=obj,
        siril_cli_exe=Path("/fake"),
        siril_script=script,
        manual=False,
        input_func=fail_if_called,
    )

    assert exit_code == 0
    assert siril_calls == ["Calibration and Alignment", "Stacking"]
    assert captured["schema"] == "astroprov-processing-v2"
    assert captured["date"] == "2026-01-01T00:00:05+00:00"
    assert captured["exptime"] == 600.0


def test_run_pipeline_manual_mode_waits_for_input_once(monkeypatch, tmp_path):
    obj, script = _make_object_dir(tmp_path)
    _setup_common_mocks(monkeypatch, tmp_path, obj, script)
    monkeypatch.setattr(pipeline.fits_prov, "write_provenance", lambda f, p: None)

    calls = {"n": 0}
    def fake_input(_prompt):
        calls["n"] += 1
        return ""

    exit_code = pipeline.run_pipeline(
        object_dir=obj,
        siril_cli_exe=Path("/fake"),
        siril_script=script,
        manual=True,
        input_func=fake_input,
    )

    assert exit_code == 0
    assert calls["n"] == 1


def test_run_pipeline_cleans_temp_ssf_on_siril_error(monkeypatch, tmp_path):
    obj, script = _make_object_dir(tmp_path)

    def fake_run_siril(exe, script_path, work_dir, action_name, **kw):
        process = Path(work_dir) / "process"
        process.mkdir(exist_ok=True)
        if action_name == "Calibration and Alignment":
            make_seq(process / "r.seq", "r")
        else:
            raise RuntimeError("Siril crashed")

    monkeypatch.setattr(pipeline, "run_siril_cli", fake_run_siril)
    monkeypatch.setattr(
        pipeline, "analyze_script_calibration",
        lambda p: CALIB_NONE,
    )
    manifest_path = tmp_path / "m.json"
    manifest_path.write_text(json.dumps({
        "passport_id": "x",
        "categories": {"lights": {"file_count": 1, "merkle_root": "0" * 64}},
    }))
    monkeypatch.setattr(pipeline.snapshot, "create_manifest", lambda o: manifest_path)
    monkeypatch.setattr(pipeline.mapping, "build_mapping", lambda o: tmp_path / "mm.json")
    monkeypatch.setattr(
        pipeline.processing, "build_pre_stack_report",
        lambda **kw: {"status": "ready", "created_utc": "x"},
    )
    monkeypatch.setattr(
        pipeline.processing, "save_report",
        lambda r, p: Path(p).write_text("{}") or p,
    )
    monkeypatch.setattr(
        pipeline.processing, "timestamp_for_filename", lambda: "x",
    )

    with pytest.raises(RuntimeError, match="Siril crashed"):
        pipeline.run_pipeline(
            object_dir=obj,
            siril_cli_exe=Path("/fake"),
            siril_script=script,
            manual=False,
            input_func=lambda _: "",
        )

    assert not (obj / "process" / "_stack_pipeline.ssf").exists()


def test_run_pipeline_raises_clear_error_without_lights(monkeypatch, tmp_path):
    obj = tmp_path / "M42"
    obj.mkdir()
    (obj / "process").mkdir()
    script = tmp_path / "s.ssf"
    script.write_text("x")

    def fake_run_siril(exe, script_path, work_dir, action_name, **kw):
        process = Path(work_dir) / "process"
        process.mkdir(exist_ok=True)
        if action_name == "Calibration and Alignment":
            make_seq(process / "r.seq", "r")

    monkeypatch.setattr(pipeline, "run_siril_cli", fake_run_siril)
    monkeypatch.setattr(
        pipeline, "analyze_script_calibration",
        lambda p: CALIB_NONE,
    )
    manifest_path = tmp_path / "m.json"
    manifest_path.write_text(json.dumps({
        "passport_id": "x",
        "categories": {"darks": {"file_count": 5, "merkle_root": "0" * 64}},
    }))
    monkeypatch.setattr(pipeline.snapshot, "create_manifest", lambda o: manifest_path)

    with pytest.raises(ValueError, match="no 'lights' category"):
        pipeline.run_pipeline(
            object_dir=obj,
            siril_cli_exe=Path("/fake"),
            siril_script=script,
            manual=False,
            input_func=lambda _: "",
        )