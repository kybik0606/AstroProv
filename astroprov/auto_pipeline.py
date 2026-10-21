"""Полностью автоматизированный пайплайн: калибровка, отбор, стекинг и верификация."""

import argparse
import subprocess
import sys
import shutil
import json
import hashlib
from datetime import datetime, timezone
from pathlib import Path
from astropy.io import fits

from astroprov import mapping, processing, snapshot, verify, fits_prov, signature


def clean_process_dir(object_dir: Path) -> None:
    """Очищает папку process от старых файлов, чтобы Siril начал с чистого листа."""
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
    """Запускает Siril CLI с указанным скриптом и выводит информацию о нем."""
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


def run_auto_pipeline(object_dir: Path, siril_cli_exe: Path, siril_script: Path):
    object_dir = object_dir.resolve()
    print(f"\n{'='*60}")
    print(f"АВТО-ПАЙПЛАЙН ASTROPROV: {object_dir.name}")
    print(f"{'='*60}\n")

    clean_process_dir(object_dir)
    run_siril_cli(siril_cli_exe, siril_script, object_dir, "Калибровка и выравнивание")
    print("  Калибровка завершена.\n")

    print("[2/6] Сканирование исходных данных (snapshot)...")
    manifest_path = snapshot.create_manifest(object_dir)
    print(f"  Манифест создан: {manifest_path.name}\n")
    
    # Извлекаем статистику из манифеста
    manifest_data = json.loads(manifest_path.read_text(encoding="utf-8"))
    passport_id = manifest_data["passport_id"]
    merkle_root = manifest_data["categories"]["lights"]["merkle_root"]
    
    cats = manifest_data.get("categories", {})
    lights_cnt = cats.get("lights", {}).get("file_count", 0)
    darks_cnt = cats.get("darks", {}).get("file_count", 0)
    biases_cnt = (cats.get("bias", {}).get("file_count", 0) + cats.get("biases", {}).get("file_count", 0))
    flats_cnt = cats.get("flats", {}).get("file_count", 0)
    
    exptime = 0.0
    lights_dir = object_dir / "lights"
    if lights_dir.is_dir():
        first_lights = sorted(lights_dir.glob("*.fits"))
        if first_lights:
            with fits.open(first_lights[0], mode="readonly") as h:
                exptime = h[0].header.get("EXPTIME", 0.0)

    print("[3/6] Создание маппинга (mapping)...")
    mapping_path = mapping.build_mapping(object_dir)
    print(f"  Маппинг создан: {mapping_path.name}\n")

    print("="*60)
    print("ШАГ 1: РУЧНОЙ ОТБОР КАДРОВ")
    print("1. Открой Siril (GUI) вручную.")
    print(f"2. Перейди в папку: {object_dir.name}")
    print("3. Открой последовательность r_pp_light_.seq.")
    print("4. Отбери хорошие кадры (выкинь облака, брак).")
    print("5. Сохрани изменения и ЗАКРОЙ SIRIL (важно!).")
    print("="*60)
    
    input("\n>>> Когда закроешь Siril, нажми ENTER здесь...")
    print("\nПродолжаем...\n")

    print("[4/6] Фиксация состояния последовательности...")
    process_dir = object_dir / "process"
    
    seq_files = list(process_dir.glob("r_*.seq"))
    if not seq_files:
        seq_files = list(process_dir.glob("*.seq"))
        
    if not seq_files:
        print("  ОШИБКА: Файлы .seq не найдены в папке process.")
        sys.exit(1)
        
    seq_path = max(seq_files, key=lambda p: p.stat().st_mtime)
    seq_name = seq_path.stem 
    print(f"  Найден .seq: {seq_path.name}\n")

    print("[5/6] Создание предварительного отчета (pre-stack)...")
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
        print(f"  Pre-stack отчет: {report_path.name}")
        
        if report["status"] == "validation_failed":
            print("  ВНИМАНИЕ: Pre-stack отчет имеет статус validation_failed.")
    except Exception as exc:
        print(f"  ОШИБКА при создании pre-stack отчета: {exc}")
        sys.exit(1)

    print("\n" + "="*60)
    print("ШАГ 2: АВТОМАТИЧЕСКИЙ СТЕКИНГ")
    print("="*60)
    
    stack_script_path = process_dir / "_stack_temp.ssf"
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

    print("[6/6] Анализ результатов стекинга...")
    fits_files = list(process_dir.glob("*.fit"))
    stack_files = [f for f in fits_files if "stacked" in f.stem.lower() or "result" in f.stem.lower()]
    
    if not stack_files:
        print("  ОШИБКА: Итоговый FITS-файл не найден.")
        sys.exit(1)
    
    output_fits = max(stack_files, key=lambda p: p.stat().st_mtime)
    print(f"  Найден итоговый FITS: {output_fits.name}\n")

    # --- ШАГ 7: ПОДПИСЬ И ВШИВАНИЕ ПАСПОРТА ---
    print("[7/7] Подпись Ed25519, вшивание паспорта и создание post-stack отчета...")
    try:
        keys_dir = object_dir / ".astroprov" / "keys"
        
        # 1. Генерируем/читаем ключи
        signature.generate_keys(keys_dir)
        pubkey_fp = signature.get_public_key_fingerprint(keys_dir)

        # 2. Создаем финальный JSON-отчет (он посчитает хеш данных БЕЗ учета паспорта)
        proc_report = processing.build_report(
            sequence_path=seq_path,
            output_path=output_fits,
            mapping_path=mapping_path,
            manifest_path=manifest_path,
            pre_stack_report_path=report_path
        )
        proc_report_path = object_dir_runs / f"processing-{processing.timestamp_for_filename()}.json"
        processing.save_report(proc_report, proc_report_path)

        # 3. Подписываем SHA-256 хеш этого JSON-отчета
        report_bytes = proc_report_path.read_bytes()
        report_hash = hashlib.sha256(report_bytes).hexdigest()
        sig_hex = signature.sign_data(report_hash.encode('utf-8'), keys_dir)

        # 4. Формируем паспорт с подписью
        prov_data = {
            "version": "1.0",
            "id": passport_id,
            "merkle_root": merkle_root,
            "date": datetime.now(timezone.utc).isoformat(),
            "tool": "AstroProv",
            "lights_count": lights_cnt,
            "darks_count": darks_cnt,
            "biases_count": biases_cnt,
            "flats_count": flats_cnt,
            "exptime": exptime,
            "pubkey_fingerprint": pubkey_fp,
            "report_hash": report_hash,
            "signature": sig_hex
        }
        
        # 5. Вшиваем паспорт в FITS
        fits_prov.write_provenance(output_fits, prov_data)
        
        print(f"  Post-stack отчет: {proc_report_path.name}")
        print(f"  ✓ Отчет подписан Ed25519 (FP: {pubkey_fp})")
    except Exception as exc:
        print(f"  ОШИБКА при создании post-stack отчета: {exc}")
        sys.exit(1)

    print("\n" + "="*60)
    print("ФИНАЛЬНАЯ ПРОВЕРКА ЦЕЛОСТНОСТИ")
    print("="*60 + "\n")
    
    exit_code = verify.verify_output(str(output_fits), str(proc_report_path))
    
    if exit_code == 0:
        print("\nАВТО-ПАЙПЛАЙН ЗАВЕРШЕН УСПЕШНО. Все проверки пройдены.")
    else:
        print("\nАВТО-ПАЙПЛАЙН ЗАВЕРШЕН С ОШИБКАМИ. Проверь логи выше.")
    
    sys.exit(exit_code)


def main():
    parser = argparse.ArgumentParser(description="Полный автопилот AstroProv.")
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

    run_auto_pipeline(args.object_dir, args.siril_cli, args.script)


if __name__ == "__main__":
    main()