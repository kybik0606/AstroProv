"""Скрипт-оркестратор для автоматизации пайплайна AstroProv."""

import argparse
import sys
from pathlib import Path

from astroprov import mapping, processing, snapshot, verify


def run_pipeline(object_dir: Path):
    object_dir = object_dir.resolve()
    print(f"\n{'='*60}")
    print(f"ЗАПУСК ПАЙПЛАЙНА ASTROPROV: {object_dir.name}")
    print(f"{'='*60}\n")

    # --- ШАГ 1: Snapshot (сканирование исходников) ---
    print("[1/5] Сканирование исходных данных (snapshot)...")
    manifest_path = snapshot.create_manifest(object_dir)
    print(f"  Манифест создан: {manifest_path.name}\n")

    # --- ШАГ 2: Mapping (сопоставление NINA <-> Siril) ---
    print("[2/5] Создание маппинга (mapping)...")
    mapping_path = mapping.build_mapping(object_dir)
    print(f"  Маппинг создан: {mapping_path.name}\n")

    # --- ПЕРВАЯ ПАУЗА: Ручной отбор кадров ---
    print("="*60)
    print("ШАГ 1: РУЧНОЙ ОТБОР КАДРОВ")
    print("1. Открой Siril (GUI).")
    print("2. Загрузи последовательность (r_pp_light_.seq).")
    print("3. Отбери хорошие кадры (выкинь облака, брак).")
    print("4. НЕ делай стекинг! Просто отбери и сохрани.")
    print("="*60)
    
    input("\n>>> Когда отберешь кадры, нажми ENTER здесь...")
    print("\nПродолжаем...\n")

    # --- ШАГ 3: Поиск финального .seq (после отбора) ---
    print("[3/5] Фиксация состояния последовательности...")
    process_dir = object_dir / "process"
    
    seq_files = list(process_dir.glob("*.seq"))
    if not seq_files:
        print("  ОШИБКА: Файлы .seq не найдены в папке process.")
        sys.exit(1)
    seq_path = max(seq_files, key=lambda p: p.stat().st_mtime)
    print(f"  Найден .seq: {seq_path.name}\n")

    # --- ШАГ 4: Pre-stack (фиксация ДО стекинга) ---
    print("[4/5] Создание предварительного отчета (pre-stack)...")
    object_dir_runs = object_dir / ".astroprov" / "runs"
    object_dir_runs.mkdir(parents=True, exist_ok=True)

    try:
        report = processing.build_pre_stack_report(
            sequence_path=seq_path,
            mapping_path=mapping_path,
            manifest_path=manifest_path
        )
        report_path = object_dir_runs / f"pre-stack-{processing.timestamp_for_filename()}.json"
        processing.save_report(report, report_path)
        print(f"  ✓ Pre-stack отчет: {report_path.name}")
        
        if report["status"] == "validation_failed":
            print("  ВНИМАНИЕ: Pre-stack отчет имеет статус validation_failed.")
            print("    Это нормально, если часть кадров была отбракована вручную.")
    except Exception as exc:
        print(f"  ОШИБКА при создании pre-stack отчета: {exc}")
        sys.exit(1)

    # --- ВТОРАЯ ПАУЗА: Ручной стекинг ---
    print("\n" + "="*60)
    print("ШАГ 2: СТЕКИНГ")
    print("1. Вернись в Siril.")
    print("2. Сделай стекинг (команда stack).")
    print("3. Дождись окончания обработки.")
    print("="*60)
    
    input("\n>>> Когда стекинг будет завершен, нажми ENTER здесь...")
    print("\nПродолжаем...\n")

    # --- ШАГ 5: Поиск итогового FITS ---
    print("[5/5] Анализ результатов стекинга...")
    
    fits_files = list(process_dir.glob("*.fit"))
    stack_files = [f for f in fits_files if "stacked" in f.stem.lower() or "result" in f.stem.lower()]
    
    if not stack_files:
        print("  ОШИБКА: Итоговый FITS-файл не найден.")
        sys.exit(1)
    
    output_fits = max(stack_files, key=lambda p: p.stat().st_mtime)
    print(f"  Найден итоговый FITS: {output_fits.name}\n")

    # --- ШАГ 6: Post-stack (фиксация ПОСЛЕ и проверка) ---
    print("[6/6] Создание финального отчета (post-stack)...")
    try:
        proc_report = processing.build_report(
            sequence_path=seq_path,
            output_path=output_fits,
            mapping_path=mapping_path,
            manifest_path=manifest_path,
            pre_stack_report_path=report_path
        )
        proc_report_path = object_dir_runs / f"processing-{processing.timestamp_for_filename()}.json"
        processing.save_report(proc_report, proc_report_path)
        print(f"  Post-stack отчет: {proc_report_path.name}\n")
    except Exception as exc:
        print(f"  ОШИБКА при создании post-stack отчета: {exc}")
        sys.exit(1)

    # --- ШАГ 7: Verify (Финальная проверка) ---
    print("\n" + "="*60)
    print("ФИНАЛЬНАЯ ПРОВЕРКА ЦЕЛОСТНОСТИ")
    print("="*60 + "\n")
    
    exit_code = verify.verify_output(str(output_fits), str(proc_report_path))
    
    if exit_code == 0:
        print("\nПАЙПЛАЙН ЗАВЕРШЕН УСПЕШНО. Все проверки пройдены.")
    else:
        print("\nПАЙПЛАЙН ЗАВЕРШЕН С ОШИБКАМИ. Проверь логи выше.")
    
    sys.exit(exit_code)


def main():
    parser = argparse.ArgumentParser(description="Автоматизированный пайплайн AstroProv.")
    parser.add_argument("object_dir", type=Path, help="Папка объекта (например, NGC5906)")
    args = parser.parse_args()

    if not args.object_dir.is_dir():
        print(f"Ошибка: {args.object_dir} — не папка.")
        sys.exit(1)

    run_pipeline(args.object_dir)


if __name__ == "__main__":
    main()
