#!/usr/bin/env python3
"""
snapshot.py — обход папки объекта, подсчёт SHA-256 хешей исходников.

Использование:
    python snapshot.py M101/
"""

import hashlib
import sys
from pathlib import Path


# Подпапки, которые нас интересуют как исходники
SOURCE_DIRS = ["lights", "darks", "bias", "flats"]


def hash_file(path: Path, chunk_size: int = 65536) -> str:
    """
    Считает SHA-256 файла.

    Читает файл блоками по 64 КБ, чтобы не грузить в память
    целиком большие FITS-файлы.
    """
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(chunk_size):
            h.update(chunk)
    return h.hexdigest()


def snapshot(obj_dir: Path) -> dict:
    """
    Обходит папку объекта, считает хеши всех файлов в исходных подпапках.

    Возвращает словарь:
        {
            "lights": [{"file": "light_001.fit", "sha256": "..."}, ...],
            "darks":  [...],
            "biases": [...],
        }
    """
    result = {}

    for subdir_name in SOURCE_DIRS:
        subdir = obj_dir / subdir_name
        if not subdir.is_dir():
            continue  # папки нет — пропускаем

        files = []
        # sorted() — чтобы порядок был детерминированным
        for file_path in sorted(subdir.iterdir()):
            if not file_path.is_file():
                continue
            files.append({
                "file": file_path.name,
                "sha256": hash_file(file_path),
            })

        if files:
            result[subdir_name] = files

    return result


def print_report(obj_dir: Path, data: dict) -> None:
    """Печатает отчёт в консоль."""
    print(f"\n=== Snapshot: {obj_dir} ===\n")

    total_files = 0
    for subdir_name, files in data.items():
        print(f"[{subdir_name}] — {len(files)} файлов")
        for f in files:
            # Обрезаем хеш для читаемости
            print(f"  {f['file']:<40} {f['sha256'][:16]}...")
        print()
        total_files += len(files)

    print(f"Всего файлов: {total_files}")


def main():
    if len(sys.argv) != 2:
        print("Использование: python snapshot.py <папка_объекта>")
        sys.exit(1)

    obj_dir = Path(sys.argv[1])
    if not obj_dir.is_dir():
        print(f"Ошибка: {obj_dir} — не папка")
        sys.exit(1)

    data = snapshot(obj_dir)
    print_report(obj_dir, data)


if __name__ == "__main__":
    main()