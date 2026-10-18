
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

from astropy.io import fits


SELECTION_RE = re.compile(r"^\s*I\s+(\d+)\s+([01])\s*$")
SEQUENCE_RE = re.compile(
    r"""^\s*S\s+['"]([^'"]+)['"]\s+""",
    re.MULTILINE,
)


def sha256_file(path: Path) -> str:
    """Calculate a file's SHA-256 hash using bounded memory."""
    digest = hashlib.sha256()

    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)

    return digest.hexdigest()


def utc_timestamp() -> str:
    """Return the current UTC timestamp."""
    return datetime.now(timezone.utc).isoformat()


def timestamp_for_filename() -> str:
    """Return a UTC timestamp suitable for report filenames."""
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def parse_sequence(path: Path) -> dict:
    """Read frame selection from a Siril .seq file."""
    text = path.read_text(encoding="utf-8", errors="replace")
    sequence_match = SEQUENCE_RE.search(text)

    if not sequence_match:
        raise ValueError(
            "Could not find the sequence name in the .seq file."
        )

    sequence_name = sequence_match.group(1)
    selection = {}

    for line in text.splitlines():
        match = SELECTION_RE.match(line)

        if match:
            index = int(match.group(1))
            selected = bool(int(match.group(2)))

            if index in selection:
                raise ValueError(
                    f"Duplicate frame index in .seq: {index}"
                )

            selection[index] = selected

    if not selection:
        raise ValueError(
            "No frame selection records (I lines) found in .seq."
        )

    indices = sorted(selection)
    expected = list(range(indices[0], indices[-1] + 1))

    if indices != expected:
        raise ValueError(
            "Frame indices in .seq are not continuous."
        )

    selected_indices = [
        index
        for index, selected in sorted(selection.items())
        if selected
    ]
    excluded_indices = [
        index
        for index, selected in sorted(selection.items())
        if not selected
    ]

    return {
        "sequence_name": sequence_name,
        "total_frames": len(selection),
        "selected_count": len(selected_indices),
        "excluded_count": len(excluded_indices),
        "selected_indices": selected_indices,
        "excluded_indices": excluded_indices,
        "selection": selection,
    }


def inspect_output_fits(path: Path) -> dict:
    """Read basic output FITS metadata without loading image pixels."""
    with fits.open(path, mode="readonly", memmap=True) as hdul:
        header = hdul[0].header
        data = hdul[0].data

        shape = list(data.shape) if data is not None else None
        history_value = header.get("HISTORY", [])

        if isinstance(history_value, str):
            history = [history_value]
        else:
            try:
                history = [str(item) for item in history_value]
            except TypeError:
                history = (
                    [str(history_value)] if history_value else []
                )

        return {
            "hdu_count": len(hdul),
            "shape": shape,
            "object": (
                str(header["OBJECT"])
                if "OBJECT" in header else None
            ),
            "date_obs": (
                str(header["DATE-OBS"])
                if "DATE-OBS" in header else None
            ),
            "exptime": (
                float(header["EXPTIME"])
                if "EXPTIME" in header else None
            ),
            "instrument": (
                str(header["INSTRUME"])
                if "INSTRUME" in header else None
            ),
            "stack_count": (
                int(header["STACKCNT"])
                if "STACKCNT" in header else None
            ),
            "combine_count": (
                int(header["NCOMBINE"])
                if "NCOMBINE" in header else None
            ),
            "history": history,
        }


def normalize_relative_path(path_string: str) -> str:
    """Normalize separators in a mapping's relative path."""
    return path_string.replace("\\", "/").removeprefix("./")


def validate_frame_mapping(
    mapping_path: Path,
    sequence_path: Path,
    sequence: dict,
) -> dict:
    """Match .seq selections to mapping entries and verify file hashes."""
    mapping_path = mapping_path.resolve()
    sequence_path = sequence_path.resolve()
    object_dir = sequence_path.parent.parent.resolve()

    data = json.loads(
        mapping_path.read_text(encoding="utf-8")
    )

    if not isinstance(data, dict):
        raise ValueError("Frame mapping root must be a JSON object.")

    if data.get("schema") != "astroprov-frame-mapping-v1":
        raise ValueError(
            "Unsupported frame mapping schema: "
            f"{data.get('schema')!r}"
        )

    mappings = data.get("mappings")

    if not isinstance(mappings, list):
        raise ValueError(
            "Mapping field 'mappings' must be a list."
        )

    if data.get("matching_count") != len(mappings):
        raise ValueError(
            "Mapping matching_count does not match "
            "the mappings list length."
        )

    by_processed_path = {}

    for entry in mappings:
        if not isinstance(entry, dict):
            raise ValueError("Each mapping entry must be an object.")

        processed = entry.get("processed") or {}
        if not isinstance(processed, dict):
            raise ValueError(
                "Mapping entry processed field must be an object."
            )

        processed_path = processed.get("path")

        if not isinstance(processed_path, str) or not processed_path:
            raise ValueError(
                "Mapping entry is missing processed.path."
            )

        key = normalize_relative_path(processed_path)

        if key in by_processed_path:
            raise ValueError(
                f"Duplicate processed path in mapping: {key}"
            )

        by_processed_path[key] = entry

    selected_records = []
    excluded_records = []
    errors = []
    matched_indices = set()
    sequence_dir = sequence_path.parent

    for index, selected in sorted(sequence["selection"].items()):
        filename = f"{sequence['sequence_name']}{index:05d}.fit"
        processed_file = sequence_dir / filename

        try:
            expected_key = normalize_relative_path(
                processed_file.relative_to(object_dir).as_posix()
            )
        except ValueError:
            errors.append(
                f"Frame {index}: sequence file is outside object directory."
            )
            continue

        entry = by_processed_path.get(expected_key)

        if entry is None:
            errors.append(
                f"Frame {index}: no mapping entry for {expected_key}"
            )
            continue

        matched_indices.add(index)

        original = entry.get("original") or {}
        processed = entry.get("processed") or {}

        if not isinstance(original, dict) or not isinstance(processed, dict):
            errors.append(
                f"Frame {index}: original/processed must be objects."
            )
            continue

        record = {
            "sequence_index": index,
            "selected": selected,
            "original_path": original.get("path"),
            "original_sha256_recorded": original.get("sha256"),
            "processed_path": processed.get("path"),
            "processed_sha256_recorded": processed.get("sha256"),
            "matching": entry.get("matching", {}),
            "original_hash_verified": None,
            "processed_hash_verified": None,
        }

        # Verify processed frame hash.
        if processed_file.is_file():
            actual_hash = sha256_file(processed_file)
            expected_hash = processed.get("sha256")

            record["processed_sha256_actual"] = actual_hash
            record["processed_hash_verified"] = (
                isinstance(expected_hash, str)
                and actual_hash == expected_hash
            )

            if not record["processed_hash_verified"]:
                errors.append(
                    f"Frame {index}: processed SHA-256 mismatch"
                )
        else:
            errors.append(
                f"Frame {index}: processed file not found: "
                f"{processed_file}"
            )

        # Verify original frame hash and prevent path traversal.
        original_path = original.get("path")

        if isinstance(original_path, str) and original_path:
            original_file = (
                object_dir / Path(original_path)
            ).resolve()

            try:
                original_file.relative_to(object_dir)
            except ValueError:
                errors.append(
                    f"Frame {index}: original path escapes object directory."
                )
                original_file = None

            if original_file is not None and original_file.is_file():
                actual_hash = sha256_file(original_file)
                expected_hash = original.get("sha256")

                record["original_sha256_actual"] = actual_hash
                record["original_hash_verified"] = (
                    isinstance(expected_hash, str)
                    and actual_hash == expected_hash
                )

                if not record["original_hash_verified"]:
                    errors.append(
                        f"Frame {index}: original SHA-256 mismatch"
                    )
            elif original_file is not None:
                errors.append(
                    f"Frame {index}: original file not found: "
                    f"{original_file}"
                )
        else:
            errors.append(
                f"Frame {index}: missing original.path"
            )

        if selected:
            selected_records.append(record)
        else:
            excluded_records.append(record)

    missing_indices = sorted(
        set(sequence["selection"]) - matched_indices
    )

    expected_paths = {
        normalize_relative_path(
            (
                sequence_dir
                / f"{sequence['sequence_name']}{index:05d}.fit"
            ).relative_to(object_dir).as_posix()
        )
        for index in sequence["selection"]
    }

    extra_mapping_paths = sorted(
        set(by_processed_path) - expected_paths
    )

    consistent = (
        not errors
        and not missing_indices
        and not extra_mapping_paths
        and len(mappings) == sequence["total_frames"]
        and len(matched_indices) == sequence["total_frames"]
    )

    return {
        "path": str(mapping_path),
        "sha256": sha256_file(mapping_path),
        "schema": data["schema"],
        "matching_count": data["matching_count"],
        "sequence_frame_count": sequence["total_frames"],
        "matched_frame_count": len(matched_indices),
        "selected_mapping_count": len(selected_records),
        "excluded_mapping_count": len(excluded_records),
        "selected_frames": selected_records,
        "excluded_frames": excluded_records,
        "missing_sequence_indices": missing_indices,
        "extra_mapping_paths": extra_mapping_paths,
        "errors": errors,
        "consistent": consistent,
    }


def optional_file_record(path: Path | None) -> dict | None:
    """Return a hash record for an optional existing file."""
    if path is None:
        return None

    resolved = path.resolve()

    if not resolved.is_file():
        raise FileNotFoundError(f"File not found: {resolved}")

    return {
        "path": str(resolved),
        "sha256": sha256_file(resolved),
        "size_bytes": resolved.stat().st_size,
    }


def build_pre_stack_report(
    sequence_path: Path,
    mapping_path: Path,
    stack_command: str | None = None,
    manifest_path: Path | None = None,
) -> dict:
    """Record and validate the frame selection before stacking."""
    sequence_path = sequence_path.resolve()
    mapping_path = mapping_path.resolve()

    if not sequence_path.is_file():
        raise FileNotFoundError(
            f"Sequence file not found: {sequence_path}"
        )

    if not mapping_path.is_file():
        raise FileNotFoundError(
            f"Frame mapping not found: {mapping_path}"
        )

    sequence = parse_sequence(sequence_path)

    mapping_validation = validate_frame_mapping(
        mapping_path=mapping_path,
        sequence_path=sequence_path,
        sequence=sequence,
    )

    warnings = []

    if not mapping_validation["consistent"]:
        warnings.append(
            "Frame mapping validation failed. "
            "Review mapping_validation.errors before stacking."
        )

    return {
        "schema": "astroprov-pre-stack-v1",
        "stage": "pre-stack",
        "created_utc": utc_timestamp(),
        "sequence": {
            "path": str(sequence_path),
            "sha256": sha256_file(sequence_path),
            "size_bytes": sequence_path.stat().st_size,
            "name": sequence["sequence_name"],
            "total_frames": sequence["total_frames"],
            "selected_count": sequence["selected_count"],
            "excluded_count": sequence["excluded_count"],
            "selected_indices": sequence["selected_indices"],
            "excluded_indices": sequence["excluded_indices"],
        },
        "mapping_file": optional_file_record(mapping_path),
        "mapping_validation": mapping_validation,
        "source_manifest": optional_file_record(manifest_path),
        "planned_stack_command": stack_command,
        "status": (
            "ready"
            if mapping_validation["consistent"]
            else "validation_failed"
        ),
        "warnings": warnings,
        "limitations": [
            "This report records the selection state before stacking.",
            "It does not independently prove which frames Siril "
            "actually uses during stacking.",
            "The report is not digitally signed. Its hash must be "
            "stored separately or included in a later signed record "
            "to detect subsequent changes.",
        ],
    }


def save_report(report: dict, report_path: Path) -> Path:
    """Write a JSON report and return its absolute path."""
    report_path = report_path.resolve()
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return report_path


def pre_stack_main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Record and validate Siril frame selection before stacking."
        )
    )
    parser.add_argument("sequence", type=Path, help="Siril .seq file")
    parser.add_argument(
        "--mapping",
        type=Path,
        required=True,
        help="Frame mapping JSON to validate",
    )
    parser.add_argument(
        "--manifest",
        type=Path,
        help="Optional AstroProv source manifest JSON",
    )
    parser.add_argument(
        "--stack-command",
        help="Planned Siril stacking command",
    )
    parser.add_argument(
        "--report",
        type=Path,
        help="Explicit report path",
    )

    args = parser.parse_args()

    try:
        report = build_pre_stack_report(
            sequence_path=args.sequence,
            mapping_path=args.mapping,
            stack_command=args.stack_command,
            manifest_path=args.manifest,
        )

        if args.report:
            report_path = args.report
        else:
            object_dir = args.sequence.resolve().parent.parent
            report_path = (
                object_dir
                / ".astroprov"
                / "runs"
                / f"pre-stack-{timestamp_for_filename()}.json"
            )

        report_path = save_report(report, report_path)

    except (OSError, ValueError, json.JSONDecodeError) as exc:
        parser.error(str(exc))

    print(f"Pre-stack report: {report_path}")
    print(f"Sequence frames: {report['sequence']['total_frames']}")
    print(f"Selected: {report['sequence']['selected_count']}")
    print(f"Excluded: {report['sequence']['excluded_count']}")
    print(f"Excluded indices: {report['sequence']['excluded_indices']}")
    print(
        "Mapping frames: "
        f"{report['mapping_validation']['matched_frame_count']}/"
        f"{report['mapping_validation']['sequence_frame_count']}"
    )
    print(
        "Mapping consistent: "
        f"{report['mapping_validation']['consistent']}"
    )
    print(f"Status: {report['status']}")
    print(f"Report SHA-256: {sha256_file(report_path)}")

    for error in report["mapping_validation"]["errors"]:
        print(f"MAPPING ERROR: {error}")

    for warning in report["warnings"]:
        print(f"WARNING: {warning}")


def validate_pre_stack_report(
    report_path: Path,
    sequence_path: Path,
    sequence: dict,
    mapping_path: Path | None,
) -> dict:
    """Verify that the pre-stack report matches current inputs."""
    report_path = report_path.resolve()

    if not report_path.is_file():
        raise FileNotFoundError(
            f"Pre-stack report not found: {report_path}"
        )

    data = json.loads(report_path.read_text(encoding="utf-8"))

    if not isinstance(data, dict):
        raise ValueError("Pre-stack report root must be a JSON object.")

    if data.get("schema") != "astroprov-pre-stack-v1":
        raise ValueError("Unsupported pre-stack report schema.")

    if data.get("status") != "ready":
        raise ValueError(
            "Pre-stack report status is not 'ready'."
        )

    pre_sequence = data.get("sequence")

    if not isinstance(pre_sequence, dict):
        raise ValueError("Pre-stack report has no valid sequence section.")

    current_sequence_hash = sha256_file(sequence_path)

    if pre_sequence.get("sha256") != current_sequence_hash:
        raise ValueError(
            "The .seq file has changed since the pre-stack report."
        )

    checks = {
        "total_frames": sequence["total_frames"],
        "selected_count": sequence["selected_count"],
        "excluded_count": sequence["excluded_count"],
        "selected_indices": sequence["selected_indices"],
        "excluded_indices": sequence["excluded_indices"],
        "name": sequence["sequence_name"],
    }

    for field, current_value in checks.items():
        if pre_sequence.get(field) != current_value:
            raise ValueError(
                f"Pre-stack sequence field mismatch: {field}"
            )

    mapping_hash = None

    if mapping_path is not None:
        mapping_path = mapping_path.resolve()

        if not mapping_path.is_file():
            raise FileNotFoundError(
                f"Frame mapping not found: {mapping_path}"
            )

        mapping_hash = sha256_file(mapping_path)
        recorded_mapping = data.get("mapping_file")

        if not isinstance(recorded_mapping, dict):
            raise ValueError(
                "Pre-stack report has no valid mapping_file record."
            )

        if recorded_mapping.get("sha256") != mapping_hash:
            raise ValueError(
                "The mapping JSON has changed since the pre-stack report."
            )

    return {
        "path": str(report_path),
        "sha256": sha256_file(report_path),
        "schema": data["schema"],
        "created_utc": data.get("created_utc"),
        "status": data["status"],
        "sequence_sha256": current_sequence_hash,
        "mapping_sha256": mapping_hash,
        "verified": True,
    }


def build_report(
    sequence_path: Path,
    output_path: Path,
    mapping_path: Path | None = None,
    manifest_path: Path | None = None,
    stack_command: str | None = None,
    pre_stack_report_path: Path | None = None,
) -> dict:
    """Build a processing report linking sequence, mapping and output."""
    sequence_path = sequence_path.resolve()
    output_path = output_path.resolve()
    warnings = []

    if not sequence_path.is_file():
        raise FileNotFoundError(
            f"Sequence file not found: {sequence_path}"
        )

    if not output_path.is_file():
        raise FileNotFoundError(
            f"Output FITS not found: {output_path}"
        )

    sequence = parse_sequence(sequence_path)
    fits_info = inspect_output_fits(output_path)

    recorded_count = fits_info["stack_count"]
    count_source = "STACKCNT"

    if recorded_count is None:
        recorded_count = fits_info["combine_count"]
        count_source = "NCOMBINE"

    if recorded_count is not None:
        try:
            recorded_count = int(recorded_count)

            if recorded_count != sequence["selected_count"]:
                warnings.append(
                    f"FITS {count_source}={recorded_count}, but "
                    f".seq has {sequence['selected_count']} selected frames."
                )
        except (TypeError, ValueError):
            warnings.append(
                f"Could not interpret FITS {count_source} as an integer."
            )
    else:
        count_source = None
        warnings.append(
            "Output FITS has neither STACKCNT nor NCOMBINE; "
            "the stacked frame count could not be cross-checked."
        )

    mapping_validation = None

    if mapping_path is not None:
        mapping_validation = validate_frame_mapping(
            mapping_path=mapping_path,
            sequence_path=sequence_path,
            sequence=sequence,
        )

        if not mapping_validation["consistent"]:
            warnings.append(
                "Frame mapping validation found missing entries, "
                "hash mismatches, or other inconsistencies."
            )

    # Verify the pre-stack report before linking it.
    pre_stack_link = None

    if pre_stack_report_path is not None:
        pre_stack_link = validate_pre_stack_report(
            report_path=pre_stack_report_path,
            sequence_path=sequence_path,
            sequence=sequence,
            mapping_path=mapping_path,
        )

    return {
        "schema": "astroprov-processing-v2",
        "stage": "post-stack",
        "created_utc": utc_timestamp(),
        "sequence": {
            "path": str(sequence_path),
            "sha256": sha256_file(sequence_path),
            "size_bytes": sequence_path.stat().st_size,
            "name": sequence["sequence_name"],
            "total_frames": sequence["total_frames"],
            "selected_count": sequence["selected_count"],
            "excluded_count": sequence["excluded_count"],
            "selected_indices": sequence["selected_indices"],
            "excluded_indices": sequence["excluded_indices"],
        },
        "output_fits": {
            "path": str(output_path),
            "sha256": sha256_file(output_path),
            "size_bytes": output_path.stat().st_size,
            "metadata": fits_info,
        },
        "cross_check": {
            "selected_count": sequence["selected_count"],
            "fits_count_field": count_source,
            "fits_recorded_count": recorded_count,
            "consistent": (
                recorded_count == sequence["selected_count"]
                if recorded_count is not None
                else None
            ),
        },
        "mapping_file": optional_file_record(mapping_path),
        "mapping_validation": mapping_validation,
        "source_manifest": optional_file_record(manifest_path),
        "pre_stack_report": pre_stack_link,
        "stack_command": stack_command,
        "warnings": warnings,
        "limitations": [
            "A .seq file records selection state but does not independently "
            "prove which files Siril actually processed.",
            "Mapping hash checks compare current files against hashes "
            "recorded in the mapping JSON.",
            "The pre-stack report link verifies matching input hashes "
            "and frame selection, but does not prove Siril execution.",
            "This report is not digitally signed.",
        ],
    }


def post_stack_main() -> None:
    """Create a report for a completed Siril stacking run."""
    parser = argparse.ArgumentParser(
        description="Create an AstroProv report for a Siril stacking run."
    )
    parser.add_argument("sequence", type=Path, help="Siril .seq file")
    parser.add_argument("output_fits", type=Path, help="Stacked output FITS")
    parser.add_argument(
        "--mapping",
        type=Path,
        help="Frame mapping JSON to validate",
    )
    parser.add_argument(
        "--manifest",
        type=Path,
        help="Optional AstroProv source manifest JSON",
    )
    parser.add_argument(
        "--stack-command",
        help="Siril stacking command used for this output",
    )
    parser.add_argument(
        "--pre-stack-report",
        type=Path,
        help="Pre-stack JSON report to verify and link to this run",
    )
    parser.add_argument(
        "--report",
        type=Path,
        help="Explicit report path; defaults to .astroprov/runs/",
    )

    args = parser.parse_args()

    try:
        report = build_report(
            sequence_path=args.sequence,
            output_path=args.output_fits,
            mapping_path=args.mapping,
            manifest_path=args.manifest,
            stack_command=args.stack_command,
            pre_stack_report_path=args.pre_stack_report,
        )

        if args.report:
            report_path = args.report
        else:
            object_dir = args.sequence.resolve().parent.parent
            report_path = (
                object_dir
                / ".astroprov"
                / "runs"
                / f"processing-{timestamp_for_filename()}.json"
            )

        report_path = save_report(report, report_path)

    except (OSError, ValueError, json.JSONDecodeError) as exc:
        parser.error(str(exc))

    print(f"Report: {report_path}")
    print(f"Sequence frames: {report['sequence']['total_frames']}")
    print(f"Selected: {report['sequence']['selected_count']}")
    print(f"Excluded: {report['sequence']['excluded_count']}")
    print(f"Excluded indices: {report['sequence']['excluded_indices']}")
    print(f"Output SHA-256: {report['output_fits']['sha256']}")

    if report["cross_check"]["consistent"] is True:
        print("FITS frame count: consistent with .seq")
    elif report["cross_check"]["consistent"] is False:
        print("WARNING: FITS frame count does not match .seq selection.")

    validation = report["mapping_validation"]

    if validation is not None:
        print(
            "Mapping frames: "
            f"{validation['matched_frame_count']}/"
            f"{validation['sequence_frame_count']}"
        )
        print(
            "Mapping selected/excluded: "
            f"{validation['selected_mapping_count']}/"
            f"{validation['excluded_mapping_count']}"
        )
        print(f"Mapping consistent: {validation['consistent']}")

        for error in validation["errors"]:
            print(f"MAPPING ERROR: {error}")

    if report["pre_stack_report"] is not None:
        link = report["pre_stack_report"]
        print(f"Pre-stack report verified: {link['verified']}")
        print(f"Pre-stack report SHA-256: {link['sha256']}")
    else:
        print("Pre-stack report: not linked")

    for warning in report["warnings"]:
        print(f"WARNING: {warning}")


def main() -> None:
    """Dispatch to pre-stack or backward-compatible post-stack CLI."""
    if len(sys.argv) > 1 and sys.argv[1] == "pre-stack":
        sys.argv.pop(1)
        pre_stack_main()
    else:
        post_stack_main()


if __name__ == "__main__":
    main()
