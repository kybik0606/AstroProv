"""Создание и обновление маппинга между оригинальными кадрами NINA и обработанными файлами Siril."""

import argparse
import json
import shutil
import sys
from datetime import datetime
from hashlib import sha256
from pathlib import Path

from astropy.io import fits


def file_hash(path: Path) -> str:
    """Вычисляет SHA-256 файла потоково."""
    h = sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def metadata(path: Path) -> dict:
    """Читает метаданные из FITS-заголовка для сопоставления."""
    with fits.open(path, memmap=False) as hdul:
        h = hdul[0].header
        return {
            "date": datetime.fromisoformat(str(h["DATE-OBS"]).replace("Z", "+00:00")),
            "exptime": float(h["EXPTIME"]),
            "object": str(h.get("OBJECT", "")).strip(),
            "instrume": str(h.get("INSTRUME", "")).strip(),
        }


def same_metadata(a: dict, b: dict) -> bool:
    """Сравнивает метаданные двух кадров."""
    delta = abs((a["date"] - b["date"]).total_seconds())
    return (
        delta <= 1.0
        and abs(a["exptime"] - b["exptime"]) <= 0.001
        and a["object"] == b["object"]
        and a["instrume"] == b["instrume"]
    )


def build_mapping(object_dir: Path) -> Path:
    """Создает или обновляет файл маппинга."""
    mapping_path = object_dir / "astroprov_frame_mapping.json"

    # --- ШАГ 1: Создаем или читаем базу маппинга ---
    if not mapping_path.exists():
        print("Файл маппинга не найден. Создаю с нуля...")
        mappings = []
    else:
        mapping_data = json.loads(mapping_path.read_text(encoding="utf-8"))
        mappings = mapping_data.get("mappings", [])
        if not mappings:
            print("Файл маппинга пустой. Заполняю структуру...")

    if not mappings:
        lights_dir = object_dir / "lights"
        light_files = sorted(lights_dir.glob("*.fits"))

        for f in light_files:
            mappings.append({
                "original": {
                    "path": f.relative_to(object_dir).as_posix(),
                    "sha256": file_hash(f)
                },
                "processed": {
                    "path": None,
                    "sha256": None
                }
            })

    # --- ШАГ 2: Сопоставление с обработанными файлами ---
    # Ищем ВСЕ файлы, начинающиеся на r_ (выровненные), 
    # и исключаем финальный стек (stacked) и мастер-кадры.
    all_processed = (object_dir / "process").glob("r_*.fit")
    processed_files = sorted(
        [
            p for p in all_processed 
            if "stacked" not in p.stem.lower() 
            and "master" not in p.stem.lower()
            and "dark" not in p.stem.lower()
            and "bias" not in p.stem.lower()
            and "flat" not in p.stem.lower()
        ]
    )

    # Вместо жесткого падения, просто предупреждаем
    if len(processed_files) != len(mappings):
        print(f"ВНИМАНИЕ: В базе {len(mappings)} оригиналов, "
              f"а обработанных FITS найдено {len(processed_files)}.")
        print("Продолжаем... Некоторые кадры могли быть отбракованы Siril.")

    processed_meta = {p: metadata(p) for p in processed_files}
    used = set()
    updates = []
    missing_count = 0

    for item in mappings:
        original = object_dir / item["original"]["path"]

        actual_original_hash = file_hash(original)
        if actual_original_hash != item["original"]["sha256"]:
            raise ValueError(f"Хеш исходного кадра изменился: {original}")

        original_meta = metadata(original)
        matches = [
            p for p, m in processed_meta.items()
            if p not in used and same_metadata(original_meta, m)
        ]

        if len(matches) == 0:
            # Кадр потерян (отбракован Siril)
            print(f"  [MISSING] {original.name} — не найден в process")
            item["processed"]["path"] = None
            item["processed"]["sha256"] = None
            item["matching"] = {"method": "NOT_FOUND", "date_obs_delta_seconds": None}
            missing_count += 1
            continue
            
        if len(matches) > 1:
            raise ValueError(
                f"Для {original.name} найдено более одного совпадения: {len(matches)}."
            )

        processed = matches[0]
        used.add(processed)
        delta = abs((original_meta["date"] - processed_meta[processed]["date"]).total_seconds())
        updates.append((item, processed, delta))

    # --- ШАГ 3: Запись результатов ---
    if mapping_path.exists():
        backup = mapping_path.with_name("astroprov_frame_mapping.backup.json")
        shutil.copy2(mapping_path, backup)
        print(f"Резервная копия: {backup}")

    for item, processed, delta in updates:
        item["processed"]["path"] = processed.relative_to(object_dir).as_posix()
        item["processed"]["sha256"] = file_hash(processed)
        item["matching"] = {
            "method": "DATE-OBS within 1 second + EXPTIME + OBJECT + INSTRUME",
            "date_obs_delta_seconds": round(delta, 6),
        }

    result = {
        "schema": "astroprov-frame-mapping-v1",
        "matching_count": len(mappings),
        "matched_count": len(updates),
        "missing_count": missing_count,
        "mappings": mappings
    }

    mapping_path.write_text(
        json.dumps(result, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    return mapping_path


def main() -> None:
    parser = argparse.ArgumentParser(description="Создание маппинга NINA <-> Siril.")
    parser.add_argument("object_dir", type=Path, help="Папка объекта, например NGC5906")
    args = parser.parse_args()

    try:
        path = build_mapping(args.object_dir.resolve())
        data = json.loads(path.read_text(encoding="utf-8"))
        
        print(f"\nMapping обновлён и сохранен: {path}")
        print(f"Всего оригиналов: {data['matching_count']}")
        print(f"Успешно сопоставлено: {data['matched_count']}")
        print(f"Потеряно (отбраковано): {data['missing_count']}")
        print("Исходные хеши проверены.")
        
    except (OSError, ValueError, KeyError) as exc:
        print(f"ОШИБКА: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()