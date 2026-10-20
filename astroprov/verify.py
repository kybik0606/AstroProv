from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from astropy.io import fits


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_json(path: Path) -> dict:
    with path.open("r", encoding="utf-8-sig") as stream:
        return json.load(stream)


def resolve_recorded_path(value: str, base: Path) -> Path:
    path = Path(value)
    if not path.is_absolute():
        path = base / path
    return path.resolve()


class Verification:
    def __init__(self) -> None:
        self.errors: list[str] = []
        self.checks: list[str] = []

    def ok(self, message: str) -> None:
        self.checks.append(message)

    def fail(self, message: str) -> None:
        self.errors.append(message)

    def check_hash(self, path: Path, expected: str, label: str) -> bool:
        if not path.is_file():
            self.fail(f"{label}: файл не найден: {path}")
            return False

        actual = sha256_file(path)
        if actual.lower() != str(expected).lower():
            self.fail(f"{label}: SHA-256 НЕ совпадает: {path}")
            return False

        self.ok(f"{label}: SHA-256 совпадает")
        return True


def find_processing_report(output: Path, root: Path) -> Path:
    reports_dir = root / ".astroprov" / "runs"
    if not reports_dir.is_dir():
        raise FileNotFoundError(f"Папка отчётов не найдена: {reports_dir}")

    matches = []
    for candidate in reports_dir.glob("processing-*.json"):
        try:
            report = read_json(candidate)
            recorded = report.get("output_fits", {}).get("path")
            if recorded and Path(recorded).resolve() == output.resolve():
                matches.append(candidate)
        except (OSError, ValueError, TypeError):
            continue

    if not matches:
        raise FileNotFoundError(f"Не найден processing-отчёт для {output}")

    return max(matches, key=lambda p: p.stat().st_mtime)


def verify_output(output_arg: str, report_arg: str | None = None) -> int:
    verifier = Verification()
    output = Path(output_arg).resolve()

    print("\nAstroProv — проверка происхождения FITS\n")
    print(f"Итоговый файл: {output.name}")

    if not output.is_file():
        print(f"\nFAIL: итоговый FITS не найден: {output}")
        return 1

    try:
        if report_arg:
            report_path = Path(report_arg).resolve()
        else:
            root = output.parent.parent
            report_path = find_processing_report(output, root)

        report = read_json(report_path)

        # --- НОВАЯ ПРОВЕРКА: HDU PROVENANCE В ФАЙЛЕ ---
        fits_prov_id = None
        try:
            with fits.open(output, mode="readonly") as hdul:
                if "PROVENANCE" not in hdul:
                    verifier.fail("В FITS отсутствует HDU с паспортом (PROVENANCE)!")
                else:
                    fits_prov_id = hdul["PROVENANCE"].header.get("PROVID")
                    verifier.ok(f"Паспорт в FITS найден. ID: {fits_prov_id}")
        except Exception as e:
            verifier.fail(f"Ошибка чтения FITS-паспорта: {e}")

        if report.get("schema") != "astroprov-processing-v2":
            verifier.fail("Неизвестная схема processing-отчёта")
        elif report.get("stage") != "post-stack":
            verifier.fail("Отчёт не относится к этапу post-stack")
        else:
            verifier.ok("Тип и этап processing-отчёта проверены")
            
            # Сверяем ID паспорта
            report_prov_id = report.get("passport_id")
            if not report_prov_id:
                verifier.fail("В JSON-отчете отсутствует passport_id для сверки.")
            elif fits_prov_id != report_prov_id:
                verifier.fail(f"ID паспорта не совпадает! FITS: {fits_prov_id}, JSON: {report_prov_id}")
            else:
                verifier.ok("ID паспорта в FITS совпадает с ID в JSON-отчете")

        recorded_output = report.get("output_fits", {})
        report_output_path = recorded_output.get("path")

        if not report_output_path:
            verifier.fail("В отчёте отсутствует путь итогового FITS")
        elif Path(report_output_path).resolve() != output:
            verifier.fail("Отчёт относится к другому итоговому FITS")

        if recorded_output.get("sha256"):
            verifier.check_hash(output, recorded_output["sha256"], "Итоговый FITS")
        else:
            verifier.fail("В отчёте отсутствует SHA-256 итогового FITS")

        sequence = report.get("sequence", {})
        sequence_path = Path(sequence.get("path", ""))
        if not sequence_path.is_absolute():
            sequence_path = output.parent.parent / sequence_path

        if sequence.get("sha256"):
            verifier.check_hash(sequence_path, sequence["sha256"], "Последовательность .seq")
        else:
            verifier.fail("В отчёте отсутствует SHA-256 .seq")

        mapping_info = report.get("mapping_file", {})
        mapping_path_value = mapping_info.get("path")

        if not mapping_path_value:
            verifier.fail("В отчёте отсутствует путь mapping")
            mapping_path = None
        else:
            mapping_path = Path(mapping_path_value)
            if not mapping_path.is_absolute():
                mapping_path = output.parent.parent / mapping_path
            mapping_path = mapping_path.resolve()
            verifier.check_hash(mapping_path, mapping_info.get("sha256", ""), "Mapping")

        pre_info = report.get("pre_stack_report", {})
        pre_path_value = pre_info.get("path")

        if not pre_path_value:
            verifier.fail("Не указан предварительный отчёт")
            pre_path = None
            pre_report = None
        else:
            pre_path = Path(pre_path_value)
            if not pre_path.is_absolute():
                pre_path = output.parent.parent / pre_path
            pre_path = pre_path.resolve()
            verifier.check_hash(pre_path, pre_info.get("sha256", ""), "Предварительный отчёт")

            try:
                pre_report = read_json(pre_path)
                if pre_report.get("schema") != "astroprov-pre-stack-v1":
                    verifier.fail("Неизвестная схема предварительного отчёта")
                
                pre_status = pre_report.get("status")
                if pre_status not in ("ready", "validation_failed"):
                    verifier.fail(f"Предварительный отчёт имеет неизвестный статус: {pre_status}")
                elif pre_status == "validation_failed":
                    verifier.ok("Предварительный отчёт имеет статус validation_failed (ожидаемо при отбраковке)")

                pre_seq = pre_report.get("sequence", {})
                curr_seq = sequence
                
                if pre_seq.get("sha256") != curr_seq.get("sha256"):
                    if pre_seq.get("selected_indices") == curr_seq.get("selected_indices"):
                        verifier.ok("Хеш .seq изменился (стекинг Siril), но список кадров тот же")
                    else:
                        verifier.fail("Список кадров в .seq изменился!")

                if pre_report.get("mapping_file", {}).get("sha256") != mapping_info.get("sha256"):
                    verifier.fail("Хеш mapping в отчётах не совпадает")

                if pre_info.get("verified") is not True:
                    verifier.fail("Предварительный отчёт не отмечен как проверенный")
                else:
                    verifier.ok("Связь предварительного отчёта проверена")

            except (OSError, ValueError, TypeError) as exc:
                verifier.fail(f"Не удалось прочитать предварительный отчёт: {exc}")
                pre_report = None

        mapping_data = None
        if mapping_path is not None and mapping_path.is_file():
            try:
                mapping_data = read_json(mapping_path)
            except (OSError, ValueError, TypeError) as exc:
                verifier.fail(f"Не удалось прочитать mapping: {exc}")

        mapping_root = mapping_path.parent if mapping_path else output.parent.parent

        if mapping_data is not None:
            if mapping_data.get("schema") != "astroprov-frame-mapping-v1":
                verifier.fail("Неизвестная схема mapping")
            else:
                records = mapping_data.get("mappings", [])
                if not isinstance(records, list) or not records:
                    verifier.fail("Mapping не содержит записей")
                else:
                    frame_errors_before = len(verifier.errors)
                    checked_pairs = 0

                    for record in records:
                        processed_item = record.get("processed", {})
                        if not processed_item.get("path"):
                            continue

                        for key, label in (
                            ("original", "Исходный кадр"),
                            ("processed", "Обработанный кадр"),
                        ):
                            item = record.get(key, {})
                            relative_path = item.get("path")
                            expected_hash = item.get("sha256")

                            if not relative_path or not expected_hash:
                                verifier.fail(f"В mapping отсутствуют путь или SHA-256: {key}")
                                continue

                            frame_path = resolve_recorded_path(relative_path, mapping_root)
                            verifier.check_hash(frame_path, expected_hash, label)
                        
                        checked_pairs += 1

                    if len(verifier.errors) == frame_errors_before:
                        verifier.ok(f"Хеши всех доступных исходных и обработанных кадров проверены: {checked_pairs} пар (потерянные пропущены)")

                    if mapping_data.get("matching_count") != len(records):
                        verifier.fail("matching_count не совпадает с числом записей mapping")

        cross = report.get("cross_check", {})
        selected_count = sequence.get("selected_count")
        excluded_count = sequence.get("excluded_count")
        total_count = sequence.get("total_frames")

        if selected_count + excluded_count != total_count:
            verifier.fail("Числа выбранных и исключённых кадров не сходятся")
        else:
            verifier.ok(f"Отбор кадров согласован: {selected_count} выбрано, {excluded_count} исключено")

        if cross.get("consistent") is not True:
            verifier.fail("Проверка числа кадров в FITS не пройдена")
        elif cross.get("selected_count") != selected_count:
            verifier.fail("Число выбранных кадров в cross_check не совпадает")
        elif cross.get("fits_recorded_count") != selected_count:
            verifier.fail("Число кадров в FITS не совпадает с отбором")
        else:
            verifier.ok(f"Число кадров в FITS согласовано: {selected_count}")

        validation = report.get("mapping_validation", {})
        
        if validation.get("consistent") is not True:
            real_errors = [e for e in validation.get("errors", []) if "no mapping entry" not in e]
            if real_errors:
                verifier.fail(f"Mapping validation не пройдена: {real_errors}")
            else:
                verifier.ok("Mapping validation: есть только потерянные кадры (ожидаемо)")
        elif validation.get("errors"):
            verifier.fail("Mapping validation содержит ошибки")
        elif (
            validation.get("matched_frame_count") != total_count
            or validation.get("selected_mapping_count") != selected_count
            or validation.get("excluded_mapping_count") != excluded_count
            or validation.get("missing_sequence_indices")
            or validation.get("extra_mapping_paths")
        ):
            verifier.fail("Числа или состав кадров в mapping не совпадают")
        else:
            verifier.ok("Mapping и последовательность согласованы")

        if report.get("warnings"):
            print("\n[INFO] В processing-отчёте есть предупреждения:")
            for w in report["warnings"]:
                print(f"       - {w}")

        print(f"\nОтчёт: {report_path}")
        print("\nРезультаты:")

        for message in verifier.checks:
            print(f"[OK] {message}")

        for message in verifier.errors:
            print(f"[FAIL] {message}")

        if verifier.errors:
            print("\nВЕРДИКТ: FAIL — цепочка не прошла все проверки.")
            return 1

        print("\nВЕРДИКТ: PASS — файлы и отчёты согласуются с зафиксированной цепочкой AstroProv.")
        print("Это подтверждает целостность и согласованность артефактов, но не является независимым доказательством выполнения Siril.")
        return 0

    except (OSError, ValueError, TypeError, KeyError) as exc:
        print(f"\nFAIL: ошибка чтения или проверки данных: {exc}")
        return 1


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify an AstroProv FITS provenance chain.")
    parser.add_argument("output_fits", help="Path to the stacked output FITS")
    parser.add_argument("--report", help="Explicit processing report path; otherwise auto-detected")
    args = parser.parse_args()
    return verify_output(args.output_fits, args.report)


if __name__ == "__main__":
    sys.exit(main())