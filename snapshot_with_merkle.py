#!/usr/bin/env python3
"""
snapshot_with_merkle.py — обход папки + Меркл-корень для каждой подпапки.

Использование:
    python snapshot_with_merkle.py M101/
"""

import hashlib
import sys
from pathlib import Path

from merkle import merkle_root


SOURCE_DIRS = ["lights", "darks", "biases", "bias", "flats", "offset", "offsets"]


def hash_file(path: Path, chunk_size: int = 65536) -> str:
    """SHA-256 файла."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(chunk_size):
            h.update(chunk)
    return h.hexdigest()


def snapshot(obj_dir: Path) -> dict:
    """
    Обходит папку, считает хеши, строит Меркл-корень для каждой подпапки.
    """
    result = {}

    for subdir_name in SOURCE_DIRS:
        subdir = obj_dir / subdir_name
        if not subdir.is_dir():
            continue

        files = []
        hashes = []

        for file_path in sorted(subdir.iterdir()):
            if not file_path.is_file():
                continue
            file_hash = hash_file(file_path)
            files.append({
                "file": file_path.name,
                "sha256": file_hash,
            })
            hashes.append(file_hash)

        if files:
            result[subdir_name] = {
                "count": len(files),
                "files": files,
                "merkle_root": merkle_root(hashes),
            }

    return result


def print_report(obj_dir: Path, data: dict) -> None:
    """Печатает отчёт."""
    print(f"\n=== Snapshot + Merkle: {obj_dir} ===\n")

    for subdir_name, info in data.items():
        print(f"[{subdir_name}] — {info['count']} файлов")
        print(f"  Merkle root: {info['merkle_root']}")
        print()

    print("Детали по файлам:")
    for subdir_name, info in data.items():
        print(f"\n[{subdir_name}]")
        for f in info["files"]:
            print(f"  {f['file']:<50} {f['sha256'][:16]}...")


def main():
    if len(sys.argv) != 2:
        print("Использование: python snapshot_with_merkle.py <папка_объекта>")
        sys.exit(1)

    obj_dir = Path(sys.argv[1])
    if not obj_dir.is_dir():
        print(f"Ошибка: {obj_dir} — не папка")
        sys.exit(1)

    data = snapshot(obj_dir)
    print_report(obj_dir, data)


if __name__ == "__main__":
    main()