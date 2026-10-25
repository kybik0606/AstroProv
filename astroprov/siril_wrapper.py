"""Wrapper around the Siril CLI used by the AstroProv pipelines."""

import os
import shutil
import subprocess
from pathlib import Path


def analyze_script_calibration(script_path: Path) -> dict:
    """Read the .ssf script and determine which calibration frames are used."""
    script_path = Path(script_path)
    text = script_path.read_text(encoding="utf-8", errors="replace")

    uses_darks = False
    uses_biases = False
    uses_flats = False

    for line in text.splitlines():
        line = line.strip()
        if line.startswith("#") or not line:
            continue

        if line.startswith("calibrate"):
            if "-dark=" in line:
                uses_darks = True
            if "-bias=" in line:
                uses_biases = True
            if "-flat=" in line:
                uses_flats = True

    return {
        "uses_darks": uses_darks,
        "uses_biases": uses_biases,
        "uses_flats": uses_flats,
    }


def find_siril_cli(cli_arg: Path | str | None) -> Path:
    """Find siril-cli executable on Windows and Linux."""
    if cli_arg:
        path = Path(cli_arg)
        if path.is_file():
            return path.resolve()
        print(f"WARNING: Provided siril-cli path not found: {path}")

    env_path = os.environ.get("SIRIL_CLI_PATH")
    if env_path:
        path = Path(env_path)
        if path.is_file():
            return path.resolve()
        print(f"WARNING: SIRIL_CLI_PATH set but file not found: {path}")

    which_path = shutil.which("siril-cli") or shutil.which("siril-cli.exe")
    if which_path:
        return Path(which_path).resolve()

    if os.name == "nt":
        default_win = Path(r"c:\program files\siril\bin\siril-cli.exe")
        if default_win.is_file():
            return default_win.resolve()

    raise FileNotFoundError(
        "siril-cli not found. Please specify --siril-cli PATH "
        "or set the SIRIL_CLI_PATH environment variable."
    )


def run_siril_cli(
    siril_exe: Path,
    script_path: Path,
    work_dir: Path,
    action_name: str,
    indent: str = "",
    prefix: str = "[*] ",
) -> None:
    """Run Siril CLI with the given script."""
    print(f"{indent}{prefix}Running Siril CLI: {action_name}")
    print(f"{indent}  Script: {script_path.name}")

    cmd = [
        str(siril_exe),
        "-d", str(work_dir.resolve()),
        "-s", str(script_path.resolve()),
    ]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    except FileNotFoundError as exc:
        raise RuntimeError(f"Could not execute siril-cli at {siril_exe}") from exc

    if result.returncode != 0:
        error_msg = (
            f"\n[-] SIRIL CLI ERROR during '{action_name}':\n"
            f"{result.stdout}\n{result.stderr}"
        )
        raise RuntimeError(error_msg)