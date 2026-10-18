
"""Создание манифеста исходных астрономических данных."""

import argparse
import hashlib
import json
import uuid

from datetime import datetime, timezone
from pathlib import Path


CHUNK_SIZE = 1024 * 1024

# Каталоги, содержащие исходные кадры.
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
    """Вычисляет SHA-256 файла потоково, не загружая его целиком."""
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
    Строит Merkle root по относительным путям и SHA-256 файлов.

    Формат:
      leaf = SHA256(0x00 || canonical_json)
      node = SHA256(0x01 || left || right)
      empty = SHA256(0x02)

    Если уровень содержит нечётное число узлов,
    последний узел дублируется.
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
    """Находит известные каталоги исходных кадров."""
    found = []

    for name in SOURCE_DIRS:
        candidate = object_dir / name

        if candidate.is_dir():
            found.append(candidate)

    return found


def snapshot_directory(object_dir: Path) -> dict:
    """Сканирует исходные каталоги и формирует данные манифеста."""
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

        for path in paths:
            relative_path = path.relative_to(object_dir).as_posix()

            print(f"Хеширование: {relative_path}")

            file_entries.append(
                {
                    "path": relative_path,
                    "size_bytes": path.stat().st_size,
                    "sha256": sha256_file(path),
                }
            )

        categories[category_name] = {
            "file_count": len(file_entries),
            "merkle_root": merkle_root(file_entries),
            "files": file_entries,
        }

    if not categories:
        raise ValueError(
            "Не найдены каталоги исходных данных: "
            + ", ".join(SOURCE_DIRS)
        )

    return categories


def create_manifest(object_dir: Path, output: Path | None = None) -> Path:
    """Создаёт JSON-манифест и возвращает путь к нему."""
    object_dir = object_dir.resolve()

    if not object_dir.exists():
        raise FileNotFoundError(
            f"Каталог не найден: {object_dir}"
        )

    if not object_dir.is_dir():
        raise NotADirectoryError(
            f"Это не каталог: {object_dir}"
        )

    passport_id = str(uuid.uuid4())

    print(f"Объект: {object_dir.name}")
    print("Начинаем сканирование исходных данных...")

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

    print("\nМАНИФЕСТ СОЗДАН")
    print(f"ID: {passport_id}")
    print(f"Файл: {output}")

    for name, category in categories.items():
        print(
            f"  {name}: {category['file_count']} файлов; "
            f"Merkle root = {category['merkle_root']}"
        )

    print(
        "\nВажно: манифест фиксирует найденные файлы, "
        "а не подтверждает их фактическое участие в стеке."
    )

    return output


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Создание SHA-256 и Merkle-манифеста."
    )
    parser.add_argument(
        "object_dir",
        type=Path,
        help="Папка объекта, например NGC5906",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="Необязательный путь к JSON-манифесту",
    )

    args = parser.parse_args()

    try:
        create_manifest(args.object_dir, args.output)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    main()
