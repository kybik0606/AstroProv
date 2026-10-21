"""Creating a manifest of source astronomical data."""

import argparse
import hashlib
import json
import uuid

from datetime import datetime, timezone
from pathlib import Path

from tqdm import tqdm


CHUNK_SIZE = 1024 * 1024

# Directories containing source frames.
SOURCE_DIRS = (
    "lights",
    "darks",
    "bias",
    "biases",
    "flats",
    "offset",
    "offsets",
)


def sha256_file(path: Path) -> str:
    """Computes a file's SHA-256 in streaming mode, without loading it fully."""
    digest = hashlib.sha256()

    with path.open("rb") as file:
        while True:
            chunk = file.read(CHUNK_SIZE)
            if not chunk:
                break
            digest.update(chunk)

    return digest.hexdigest()


def merkle_root(files: list[dict]) -> str:
    """
    Builds the Merkle root from relative paths and file SHA-256 hashes.

    Format:
      leaf = SHA256(0x00 || canonical_json)
      node = SHA256(0x01 || left || right)
      empty = SHA256(0x02)

    If a level contains an odd number of nodes,
    the last node is duplicated.
    """
    if not files:
        return hashlib.sha256(b"\x02").hexdigest()

    leaves = []

    for item in sorted(files, key=lambda x: x["path"]):
        leaf_data = {
            "path": item["path"],
            "sha256": item["sha256"],
        }

        encoded = json.dumps(
            leaf_data,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")

        leaf_hash = hashlib.sha256(b"\x00" + encoded).digest()
        leaves.append(leaf_hash)

    level = leaves

    while len(level) > 1:
        if len(level) % 2 == 1:
            level = level + [level[-1]]

        next_level = []

        for index in range(0, len(level), 2):
            parent = hashlib.sha256(
                b"\x01" + level[index] + level[index + 1]
            ).digest()
            next_level.append(parent)

        level = next_level

    return level[0].hex()


def discover_source_dirs(object_dir: Path) -> list[Path]:
    """Finds known source frame directories."""
    found = []

    for name in SOURCE_DIRS:
        candidate = object_dir / name

        if candidate.is_dir():
            found.append(candidate)

    return found


def snapshot_directory(object_dir: Path) -> dict:
    """Scans source directories and builds manifest data."""
    categories = {}

    for source_dir in discover_source_dirs(object_dir):
        category_name = source_dir.name
        file_entries = []

        paths = sorted(
            (
                path
                for path in source_dir.rglob("*")
                if path.is_file() and not path.is_symlink()
            ),
            key=lambda path: path.relative_to(object_dir).as_posix(),
        )

        total = len(paths)

        if total == 0:
            categories[category_name] = {
                "file_count": 0,
                "merkle_root": merkle_root([]),
                "files": [],
            }
            continue

        pbar = tqdm(
            total=total,
            bar_format="{desc}",
            dynamic_ncols=True,
            disable=None,
        )

        for idx, path in enumerate(paths, 1):
            pbar.set_description_str(
                f"{category_name}: {idx}/{total} {path.name}",
                refresh=True,
            )

            relative_path = path.relative_to(object_dir).as_posix()

            file_entries.append(
                {
                    "path": relative_path,
                    "size_bytes": path.stat().st_size,
                    "sha256": sha256_file(path),
                }
            )

            pbar.update(1)

        pbar.set_description_str(
            f"{category_name}: {total}/{total} done",
            refresh=True,
        )
        pbar.close()

        categories[category_name] = {
            "file_count": len(file_entries),
            "merkle_root": merkle_root(file_entries),
            "files": file_entries,
        }

    if not categories:
        raise ValueError(
            "No source data directories found: "
            + ", ".join(SOURCE_DIRS)
        )

    return categories


def create_manifest(object_dir: Path, output: Path | None = None) -> Path:
    """Creates a JSON manifest and returns its path."""
    object_dir = object_dir.resolve()

    if not object_dir.exists():
        raise FileNotFoundError(
            f"Directory not found: {object_dir}"
        )

    if not object_dir.is_dir():
        raise NotADirectoryError(
            f"Not a directory: {object_dir}"
        )

    passport_id = str(uuid.uuid4())

    print(f"Object: {object_dir.name}")
    print("Starting source data scan...")

    categories = snapshot_directory(object_dir)

    manifest = {
        "schema": "astroprov-manifest-v1",
        "passport_id": passport_id,
        "object_name": object_dir.name,
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "hash_algorithm": "SHA-256",
        "merkle_algorithm": "astroprov-merkle-v1",
        "path_policy": "relative-to-object-directory",
        "categories": categories,
    }

    if output is None:
        output_dir = (
            object_dir / ".astroprov" / "manifests"
        )
        output = output_dir / f"{passport_id}.json"
    else:
        output = output.resolve()
        output_dir = output.parent

    output_dir.mkdir(parents=True, exist_ok=True)

    output.write_text(
        json.dumps(
            manifest,
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    print("\nMANIFEST CREATED")
    print(f"ID: {passport_id}")
    print(f"File: {output}")

    for name, category in categories.items():
        print(
            f"  {name}: {category['file_count']} files; "
            f"Merkle root = {category['merkle_root']}"
        )

    print(
        "\nNote: the manifest records discovered files, "
        "not proof of their actual participation in the stack."
    )

    return output


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Creating a SHA-256 and Merkle manifest."
    )
    parser.add_argument(
        "object_dir",
        type=Path,
        help="Object directory, e.g. NGC5906",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="Optional path to the JSON manifest",
    )

    args = parser.parse_args()

    try:
        create_manifest(args.object_dir, args.output)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    main()