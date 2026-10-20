"""Полностью автоматический пайплайн без ручного отбора кадров."""

import argparse
import subprocess
import sys
import shutil
from datetime import datetime, timezone
from pathlib import Path

from astroprov import mapping, processing, snapshot, verify, fits_prov


def clean_process_dir(object_dir: Path) -> None:
    """Очищает папку process от старых файлов."""
    process_dir = object_dir / "process"
    if process_dir.exists():
        print(f"[*] Очистка папки process от старых файлов...")
        for item in process_dir.iterdir():
            if item.is_file():
                item.unlink()
            elif item.is_dir():
                shutil.rmtree(item)
        print("    Папка process очищена.\n")


def run_siril_cli(siril_exe: Path, script_path: Path, work_dir: Path, action_name: str) -> None:
    """Запускает Siril CLI с указанным скриптом."""
    print(f"[*] Запуск Siril CLI: {action_name}")
    print(f"    Скрипт: {script_path.name}")
    
    cmd = [
        str(siril_exe),
        "-d", str(work_dir.resolve()),
        "-s", str(script_path.resolve())
    ]
    
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=False)
        if result.returncode != 0:
            print(f"\n[-] ОШИБКА SIRIL CLI во время '{action_name}':")
            print(result.stdout)
            print(result.stderr)
            sys.exit(1)
    except FileNotFoundError:
        print(f"\n[-] ОШИБКА: Не найден siril-cli.exe по пути: {siril_exe}")
        sys.exit(1)


def run_full_auto_pipeline(object_dir: Path, siril_cli_exe: Path, siril_script: Path):
    object_dir = object_dir.resolve()
    print(f"\n{'='*60}")
    print(f"ПОЛНЫЙ АВТОПИЛОТ ASTROPROV: {object_dir.name}")
    print(f"{'='*60}\n")

    # --- ШАГ 1: Очистка и калибровка ---
    clean_process_dir(object_dir)
    run_siril_cli(siril_cli_exe, siril_script, object_dir, "Калибровка и выравнивание")
    print("  Калибровка завершена.\n")

    # --- ШАГ 2: Snapshot ---
    print("[2/6] Сканирование исходных данных (snapshot)...")
    manifest_path = snapshot.create_manifest(object_dir)
    print(f"  Манифест создан: {manifest_path.name}\n")
    
    import json
    manifest_data = json.loads(manifest_path.read_text(encoding="utf-8"))
    passport_id = manifest_data["passport_id"]
    merkle_root = manifest_data["categories"]["lights"]["merkle_root"]

    # --- ШАГ 3: Mapping ---
    print("[3/6] Создание маппинга (mapping)...")
    mapping_path = mapping.build_mapping(object_dir)
    print(f"  Маппинг создан: {mapping_path.name}\n")

    # --- ШАГ 4: Поиск .seq и Pre-stack ---
    print("[4/6] Фиксация состояния последовательности (pre-stack)...")
    process_dir = object_dir / "process"
    
    seq_files = list(process_dir.glob("r_*.seq"))
    if not seq_files:
        seq_files = list(process_dir.glob("*.seq"))
        
    if not seq_files:
        print("  ОШИБКА: Файлы .seq не найдены в папке process.")
        sys.exit(1)
        
    seq_path = max(seq_files, key=lambda p: p.stat().st_mtime)
    seq_name = seq_path.stem 
    print(f"  Найден .seq: {seq_path.name}")

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
        print(f"  Pre-stack отчет: {report_path.name}\n")
    except Exception as exc:
        print(f"  ОШИБКА при создании pre-stack отчета: {exc}")
        sys.exit(1)

    # --- ШАГ 5: Автоматический стекинг ---
    print("[5/6] Автоматический стекинг ВСЕХ доступных кадров...")
    
    stack_script_path = process_dir / "_stack_full_auto.ssf"
    output_fits_name = f"{seq_name}_stacked"
    
    stack_cmd = f"stack {seq_name} rej 3 3 -nonorm -filter-included -out={output_fits_name}"
    print(f"[*] Параметры стекинга: {stack_cmd}")

    stack_script_content = f"""requires 1.2.0
cd process
{stack_cmd}
cd ..
"""
    stack_script_path.write_text(stack_script_content, encoding="utf-8")

    run_siril_cli(siril_cli_exe, stack_script_path, object_dir, "Стекинг")
    print("  Стекинг завершен.\n")

    if stack_script_path.exists():
        stack_script_path.unlink()

    # --- ШАГ 6: Поиск FITS, вшивание паспорта и Post-stack ---
    print("[6/6] Вшивание паспорта (fits_prov) и создание post-stack отчета...")
    fits_files = list(process_dir.glob("*.fit"))
    stack_files = [f for f in fits_files if "stacked" in f.stem.lower() or "result" in f.stem.lower()]
    
    if not stack_files:
        print("  ОШИБКА: Итоговый FITS-файл не найден.")
        sys.exit(1)
    
    output_fits = max(stack_files, key=lambda p: p.stat().st_mtime)
    print(f"  Найден итоговый FITS: {output_fits.name}")

    try:
        # Вшиваем паспорт
        prov_data = {
            "version": "1.0",
            "id": passport_id,
            "merkle_root": merkle_root,
            "date": datetime.now(timezone.utc).isoformat(),
            "tool": "AstroProv"
        }
        fits_prov.write_provenance(output_fits, prov_data)
        
        # Создаем отчет (с новым хешем файла)
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

    # --- ШАГ 7: Verify ---
    print("\n" + "="*60)
    print("ФИНАЛЬНАЯ ПРОВЕРКА ЦЕЛОСТНОСТИ")
    print("="*60 + "\n")
    
    exit_code = verify.verify_output(str(output_fits), str(proc_report_path))
    
    if exit_code == 0:
        print("\nПОЛНЫЙ АВТОПИЛОТ ЗАВЕРШЕН УСПЕШНО. Все проверки пройдены.")
    else:
        print("\nПОЛНЫЙ АВТОПИЛОТ ЗАВЕРШЕН С ОШИБКАМИ. Проверь логи выше.")
    
    sys.exit(exit_code)


def main():
    parser = argparse.ArgumentParser(description="Полный автопилот AstroProv (без ручного отбора).")
    parser.add_argument("object_dir", type=Path, help="Папка объекта (например, NGC5906)")
    parser.add_argument("--script", type=Path, required=True, help="Путь к .ssf скрипту Siril для калибровки")
    parser.add_argument(
        "--siril-cli", 
        type=Path, 
        default=Path(r"c:\program files\siril\bin\siril-cli.exe"),
        help="Путь к siril-cli.exe"
    )
    
    args = parser.parse_args()

    if not args.object_dir.is_dir():
        print(f"Ошибка: {args.object_dir} — не папка.")
        sys.exit(1)
        
    if not args.script.is_file():
        print(f"Ошибка: Скрипт {args.script} не найден.")
        sys.exit(1)

    run_full_auto_pipeline(args.object_dir, args.siril_cli, args.script)


if __name__ == "__main__":
    main()