"""Просмотр паспорта AstroProv (из FITS + манифеста)."""

import argparse
import json
import sys
from pathlib import Path

from astropy.io import fits
from astroprov import fits_prov


def find_manifest_by_id(object_dir: Path, passport_id: str) -> Path | None:
    """Ищет JSON-манифест по его ID в скрытой папке .astroprov."""
    manifests_dir = object_dir / ".astroprov" / "manifests"
    if not manifests_dir.is_dir():
        return None

    manifest_path = manifests_dir / f"{passport_id}.json"
    if manifest_path.is_file():
        return manifest_path

    return None


def format_time(seconds: float) -> str:
    """Переводит секунды в красивый формат (часы, минуты)."""
    if seconds is None:
        return ""
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    
    if hours > 0:
        return f"{hours} ч {minutes} мин ({int(seconds)} с)"
    elif minutes > 0:
        return f"{minutes} мин {secs} с"
    else:
        return f"{secs} с"


def inspect_fits(fits_path: Path) -> int:
    """Читает паспорт из FITS, находит манифест и выводит красивый отчёт."""
    fits_path = fits_path.resolve()
    
    print(f"\n{'='*60}")
    print(f"ПАСПОРТ ASTROPROV: {fits_path.name}")
    print(f"{'='*60}\n")

    if not fits_path.is_file():
        print(f"ОШИБКА: файл не найден: {fits_path}")
        return 1

    # 1. Читаем паспорт из FITS
    prov = fits_prov.read_provenance(fits_path)

    if prov is None:
        print("ОШИБКА: в этом FITS-файле нет паспорта (HDU 'PROVENANCE').")
        return 1

    passport_id = prov.get("id")
    
    # 2. Читаем заголовок FITS для выдержки (EXPTIME и STACKCNT)
    exptime_hdr = None
    stack_count = None
    try:
        with fits.open(fits_path, mode="readonly") as hdul:
            header = hdul[0].header
            exptime_hdr = header.get("EXPTIME")
            stack_count = header.get("STACKCNT") or header.get("NCOMBINE")
    except Exception as e:
        print(f"[ВНИМАНИЕ] Не удалось прочитать заголовок FITS: {e}")

    # 3. Пытаемся найти манифест по ID (для обратной совместимости со старыми файлами)
    object_dir = fits_path.parent.parent
    manifest_path = find_manifest_by_id(object_dir, passport_id) if passport_id else None

    # 4. Выводим основную информацию
    print("ОБЩАЯ ИНФОРМАЦИЯ")
    print(f"  ID паспорта:      {passport_id or '—'}")
    print(f"  Дата создания:    {prov.get('date', '—')}")
    print(f"  Версия формата:   {prov.get('version', '—')}")
    print(f"  Инструмент:       {prov.get('tool', '—')}")

    print(f"\nMERKLE-КОРЕНЬ ИСХОДНЫХ ДАННЫХ (SHA-256)")
    print(f"  {prov.get('merkle_root', '—')}")

    # 5. Выводим статистику (Приоритет - FITS, если нет - манифест)
    lights_cnt = prov.get("lights_count")
    
    if lights_cnt is not None:
        darks_cnt = prov.get("darks_count", 0)
        biases_cnt = prov.get("biases_count", 0)
        flats_cnt = prov.get("flats_count", 0)
        
        print(f"\nСОСТАВ ИСХОДНЫХ ДАННЫХ (из паспорта FITS)")
        print(f"  Лайтов:     {lights_cnt}")
        print(f"  Дарков:     {darks_cnt}")
        print(f"  Биасов:     {biases_cnt}")
        print(f"  Флэтов:     {flats_cnt}")
        
        total = lights_cnt + darks_cnt + biases_cnt + flats_cnt
        print(f"  {'Всего:':<12} {total} файлов")
    elif manifest_path:
        try:
            manifest_data = json.loads(manifest_path.read_text(encoding="utf-8"))
            cats = manifest_data.get("categories", {})
            
            lights_cnt = cats.get("lights", {}).get("file_count", 0)
            darks_cnt = cats.get("darks", {}).get("file_count", 0)
            biases_cnt = (cats.get("bias", {}).get("file_count", 0) 
                          + cats.get("biases", {}).get("file_count", 0))
            flats_cnt = cats.get("flats", {}).get("file_count", 0)
            
            print(f"\nСОСТАВ ИСХОДНЫХ ДАННЫХ (из манифеста, старый формат)")
            print(f"  Лайтов:     {lights_cnt}")
            print(f"  Дарков:     {darks_cnt}")
            print(f"  Биасов:     {biases_cnt}")
            print(f"  Флэтов:     {flats_cnt}")
            
            total = lights_cnt + darks_cnt + biases_cnt + flats_cnt
            print(f"  {'Всего:':<12} {total} файлов")
        except Exception as e:
            print(f"\n  [ВНИМАНИЕ] Не удалось прочитать манифест: {e}")
    else:
        print("\nСОСТАВ ИСХОДНЫХ ДАННЫХ")
        print("  [НЕТ ДАННЫХ] В этом FITS-файле нет статистики, и манифест не найден.")

    # 6. Выводим информацию о выдержке
    exptime_val = prov.get("exptime")
    if exptime_val is None:
        exptime_val = exptime_hdr

    if exptime_val is not None and float(exptime_val) > 0:
        print(f"\nВЫДЕРЖКА")
        print(f"  Одного кадра:       {exptime_val} с")
        
        if stack_count and int(stack_count) > 0:
            total_exp = float(exptime_val) * int(stack_count)
            print(f"  Кадров в стеке:     {stack_count}")
            print(f"  Суммарная:          {format_time(total_exp)}")
        else:
            print(f"  [НЕТ ДАННЫХ] В заголовке FITS нет STACKCNT или NCOMBINE.")

    print(f"\n{'='*60}")
    print("Проверка целостности: используйте `python -m astroprov.verify`")
    print(f"{'='*60}\n")

    return 0


def inspect_directory(directory: Path) -> int:
    """Сканирует папку и показывает паспорта всех FITS-файлов в ней."""
    directory = directory.resolve()
    if not directory.is_dir():
        print(f"ОШИБКА: {directory} — не папка")
        return 1

    fits_files = sorted(directory.glob("*.fit")) + sorted(directory.glob("*.fits"))
    
    if not fits_files:
        print(f"В папке {directory} нет FITS-файлов.")
        return 1

    print(f"\n{'='*60}")
    print(f"ПОИСК ПАСПОРТОВ В ПАПКЕ: {directory}")
    print(f"{'='*60}\n")

    with_passport = 0
    total = 0

    for f in fits_files:
        total += 1
        prov = fits_prov.read_provenance(f)
        if prov:
            with_passport += 1
            print(f"  [PASSPORT] {f.name}")
            print(f"             ID: {prov.get('id')}")
        else:
            print(f"  [NO PASSPORT] {f.name}")

    print(f"\nИтого: {with_passport} с паспортом из {total} файлов\n")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Просмотр паспорта AstroProv в FITS-файле или папке."
    )
    parser.add_argument(
        "target",
        type=Path,
        help="Путь к FITS-файлу или папке с FITS-файлами",
    )
    args = parser.parse_args()

    target = args.target
    if not target.exists():
        print(f"ОШИБКА: путь не существует: {target}")
        return 1

    if target.is_file():
        return inspect_fits(target)
    else:
        return inspect_directory(target)


if __name__ == "__main__":
    sys.exit(main())