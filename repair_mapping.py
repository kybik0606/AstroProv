import json
import shutil
from pathlib import Path
from datetime import datetime
from hashlib import sha256
from astropy.io import fits

root = Path("NGC5906")
mapping_path = root / "astroprov_frame_mapping.json"

def file_hash(path):
    h = sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()

def metadata(path):
    with fits.open(path, memmap=False) as hdul:
        h = hdul[0].header
        return {
            "date": datetime.fromisoformat(str(h["DATE-OBS"]).replace("Z", "+00:00")),
            "exptime": float(h["EXPTIME"]),
            "object": str(h.get("OBJECT", "")).strip(),
            "instrume": str(h.get("INSTRUME", "")).strip(),
        }

def same_metadata(a, b):
    delta = abs((a["date"] - b["date"]).total_seconds())
    return (
        delta <= 1.0
        and abs(a["exptime"] - b["exptime"]) <= 0.001
        and a["object"] == b["object"]
        and a["instrume"] == b["instrume"]
    )

# --- ШАГ 1: Создаем или читаем базу маппинга ---
if not mapping_path.exists():
    print("Файл маппинга не найден. Создаю с нуля...")
    mappings = []
else:
    mapping_data = json.loads(mapping_path.read_text(encoding="utf-8"))
    mappings = mapping_data.get("mappings", [])
    if not mappings:
        print("Файл маппинга пустой. Заполняю структуру...")

# Если список пустой — генерируем его из папки lights
if not mappings:
    lights_dir = root / "lights"
    light_files = sorted(lights_dir.glob("*.fits"))
    
    for f in light_files:
        mappings.append({
            "original": {
                "path": f.relative_to(root).as_posix(),
                "sha256": file_hash(f)
            },
            "processed": {
                "path": "",
                "sha256": ""
            }
        })

# --- ШАГ 2: Сопоставление с обработанными файлами ---
processed_files = sorted((root / "process").glob("r_pp_light_*.fit"))

if len(processed_files) != len(mappings):
    raise SystemExit(
        f"ОСТАНОВКА: в базах {len(mappings)} оригиналов, "
        f"а обработанных FITS найдено {len(processed_files)}."
    )

processed_meta = {p: metadata(p) for p in processed_files}
used = set()
updates = []

for item in mappings:
    original = root / item["original"]["path"]
    
    # Проверка хеша оригинала (чтобы убедиться, что его не подменили)
    actual_original_hash = file_hash(original)
    if actual_original_hash != item["original"]["sha256"]:
        raise SystemExit(f"ОСТАНОВКА: хеш исходного кадра изменился: {original}")

    original_meta = metadata(original)
    matches = [
        p for p, m in processed_meta.items()
        if p not in used and same_metadata(original_meta, m)
    ]

    if len(matches) != 1:
        raise SystemExit(
            f"ОСТАНОВКА: для {original.name} найдено совпадений: "
            f"{len(matches)}."
        )

    processed = matches[0]
    used.add(processed)
    delta = abs((original_meta["date"] - processed_meta[processed]["date"]).total_seconds())
    updates.append((item, processed, delta))

if len(used) != len(processed_files):
    unused = [p.name for p in processed_files if p not in used]
    raise SystemExit(f"ОСТАНОВКА: остались несопоставленные кадры: {unused}")

# --- ШАГ 3: Запись результатов ---
if mapping_path.exists():
    backup = mapping_path.with_name("astroprov_frame_mapping.backup.json")
    shutil.copy2(mapping_path, backup)
    print(f"Резервная копия: {backup}")

for item, processed, delta in updates:
    item["processed"]["path"] = processed.relative_to(root).as_posix()
    item["processed"]["sha256"] = file_hash(processed)
    item["matching"] = {
        "method": "DATE-OBS within 1 second + EXPTIME + OBJECT + INSTRUME",
        "date_obs_delta_seconds": round(delta, 6),
    }

result = {
    "schema": "astroprov-frame-mapping-v1",
    "matching_count": len(mappings),
    "mappings": mappings
}

mapping_path.write_text(
    json.dumps(result, indent=2, ensure_ascii=False) + "\n",
    encoding="utf-8",
)

print(f"Mapping обновлён и сохранен: {mapping_path}")
print(f"Проверено уникальных соответствий: {len(updates)}")
print("Исходные хеши проверены; метаданные всех пар совпали.")