
from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path

from astropy.io import fits


FITS_EXTENSIONS = {".fit", ".fits", ".fts"}

# Минимальная оценка и преимущество, необходимые для автовыбора.
MIN_AUTO_SCORE = 6
MIN_SCORE_MARGIN = 3


class TargetSelectionError(Exception):
    """Ошибка поиска или выбора итогового FITS-файла."""


@dataclass
class FitsCandidate:
    path: Path
    score: int
    reasons: list[str]
    history: str
    stack_count: int | None
    image_shape: tuple[int, ...] | None
    object_name: str | None
    image_type: str | None


def get_primary_metadata(path: Path) -> dict:
    """Читает полезные для определения стека параметры FITS."""
    with fits.open(
        path,
        mode="readonly",
        memmap=False,
        lazy_load_hdus=False,
    ) as hdul:
        hdul.verify("exception")

        if len(hdul) == 0:
            raise ValueError("Файл не содержит HDU")

        header = hdul[0].header
        data = hdul[0].data

        # HISTORY может состоять из нескольких FITS-карт.
        history_items = header.get("HISTORY", [])
        if isinstance(history_items, str):
            history_items = [history_items]

        history = " ".join(str(item) for item in history_items)
        history = re.sub(r"\s+", " ", history).strip()

        stack_count = None
        for key in ("STACKCNT", "NCOMBINE"):
            value = header.get(key)
            if value is not None:
                try:
                    count = int(value)
                    if count > 0:
                        stack_count = count
                        break
                except (TypeError, ValueError, OverflowError):
                    pass

        return {
            "history": history,
            "stack_count": stack_count,
            "image_shape": (
                tuple(data.shape)
                if data is not None and hasattr(data, "shape")
                else None
            ),
            "object_name": header.get("OBJECT"),
            "image_type": header.get("IMAGETYP"),
            "exptime": header.get("EXPTIME"),
            "filter_name": header.get("FILTER"),
            "instrument": header.get("INSTRUME"),
        }


def is_valid_fits(path: Path) -> bool:
    """Проверяет, что файл читается как FITS и содержит изображение."""
    try:
        with fits.open(
            path,
            mode="readonly",
            memmap=False,
            lazy_load_hdus=False,
        ) as hdul:
            hdul.verify("exception")

            return any(
                hdu.data is not None
                and hdu.header.get("NAXIS", 0) > 0
                for hdu in hdul
            )
    except Exception:
        return False


def score_filename(path: Path) -> tuple[int, list[str]]:
    """Оценивает имя файла. Это только один из источников признаков."""
    name = path.stem.lower()
    score = 0
    reasons = []

    positive_patterns = [
        (r"result", 8, "имя содержит result"),
        (r"stacked", 7, "имя содержит stacked"),
        (r"stack", 6, "имя содержит stack"),
        (r"combined", 6, "имя содержит combined"),
        (r"final", 6, "имя содержит final"),
    ]

    negative_patterns = [
        (r"lights?", -4, "имя похоже на отдельные light-кадры"),
        (r"darks?", -8, "имя похоже на dark-кадры"),
        (r"bias(?:es)?", -8, "имя похоже на bias-кадры"),
        (r"flats?", -8, "имя похоже на flat-кадры"),
        (r"master", -5, "имя содержит master"),
        (r"calibrated|calibration", -3, "имя указывает на калибровку"),
    ]

    for pattern, points, reason in positive_patterns:
        if re.search(pattern, name):
            score += points
            reasons.append(f"{reason} ({points:+d})")

    for pattern, points, reason in negative_patterns:
        if re.search(pattern, name):
            score += points
            reasons.append(f"{reason} ({points:+d})")

    return score, reasons


def analyze_candidate(path: Path) -> FitsCandidate:
    """Считывает признаки FITS и вычисляет оценку кандидата."""
    metadata = get_primary_metadata(path)
    score, reasons = score_filename(path)

    history = metadata["history"].lower()

    # Прямое описание объединения кадров — сильный признак стека.
    stack_patterns = [
        (
            r"\bstacking\b",
            8,
            "HISTORY описывает stacking",
        ),
        (
            r"\bsigma clipping\b|\bsigma rejection\b",
            4,
            "HISTORY описывает sigma clipping/rejection",
        ),
        (
            r"\brejection\b",
            2,
            "HISTORY содержит описание rejection",
        ),
        (
            r"\bnormalized output\b",
            2,
            "HISTORY указывает normalized output",
        ),
        (
            r"\bnormalized input\b",
            1,
            "HISTORY указывает normalized input",
        ),
    ]

    for pattern, points, reason in stack_patterns:
        if re.search(pattern, history):
            score += points
            reasons.append(f"{reason} ({points:+d})")

    stack_count = metadata["stack_count"]
    if stack_count is not None:
        # Сам факт наличия счётчика полезен, но его величина
        # не должна автоматически определять победителя.
        score += 2
        reasons.append(f"есть счётчик объединения: {stack_count} (+2)")

    shape = metadata["image_shape"]
    if shape is not None and len(shape) >= 2:
        reasons.append(f"размерность изображения: {shape}")

    if metadata["image_type"]:
        reasons.append(f"IMAGETYP={metadata['image_type']}")

    if metadata["object_name"]:
        reasons.append(f"OBJECT={metadata['object_name']}")

    return FitsCandidate(
        path=path,
        score=score,
        reasons=reasons,
        history=metadata["history"],
        stack_count=stack_count,
        image_shape=shape,
        object_name=metadata["object_name"],
        image_type=metadata["image_type"],
    )


def find_target_fits(
    directory: str | Path,
    target: str | Path | None = None,
) -> Path:
    """
    Выбирает итоговый FITS.

    Если кандидат не выделяется достаточно уверенно, возвращает
    ошибку со списком файлов вместо рискованного автовыбора.
    """
    directory = Path(directory).expanduser().resolve()

    if not directory.is_dir():
        raise TargetSelectionError(
            f"Папка объекта не найдена: {directory}"
        )

    # Явный выбор пользователя всегда имеет приоритет.
    if target is not None:
        target_path = Path(target).expanduser()

        if not target_path.is_absolute():
            target_path = directory / target_path

        target_path = target_path.resolve()

        if not target_path.is_file():
            raise TargetSelectionError(
                f"Указанный файл не найден: {target_path}"
            )

        if target_path.suffix.lower() not in FITS_EXTENSIONS:
            raise TargetSelectionError(
                f"Неподдерживаемое расширение: {target_path.name}"
            )

        if not is_valid_fits(target_path):
            raise TargetSelectionError(
                f"Файл не прошёл проверку FITS: {target_path.name}"
            )

        return target_path

    paths = sorted(
        (
            path
            for path in directory.iterdir()
            if path.is_file()
            and path.suffix.lower() in FITS_EXTENSIONS
        ),
        key=lambda path: path.name.lower(),
    )

    candidates = []

    for path in paths:
        if not is_valid_fits(path):
            continue

        try:
            candidates.append(analyze_candidate(path))
        except Exception as exc:
            print(
                f"Предупреждение: не удалось проанализировать "
                f"{path.name}: {exc}",
                file=sys.stderr,
            )

    if not candidates:
        raise TargetSelectionError(
            f"В папке {directory} не найдено подходящих FITS-файлов."
        )

    if len(candidates) == 1:
        return candidates[0].path

    candidates.sort(
        key=lambda item: (-item.score, item.path.name.lower())
    )

    best = candidates[0]
    second = candidates[1]
    margin = best.score - second.score

    if best.score >= MIN_AUTO_SCORE and margin >= MIN_SCORE_MARGIN:
        print("Автоматический выбор по признакам FITS:")
        for reason in best.reasons:
            print(f"  + {reason}")
        return best.path

    details = []

    for candidate in candidates:
        details.append(
            f"  - {candidate.path.name}: {candidate.score} баллов"
        )

        for reason in candidate.reasons:
            details.append(f"      {reason}")

        if candidate.history:
            history_preview = candidate.history
            if len(history_preview) > 240:
                history_preview = history_preview[:237] + "..."
            details.append(f"      HISTORY: {history_preview}")

    raise TargetSelectionError(
        "Не удалось однозначно определить итоговый FITS-файл.\n"
        "Выбери нужный файл явно с помощью --target.\n\n"
        + "\n".join(details)
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Определение итогового FITS по имени файла "
            "и метаданным заголовка."
        )
    )
    parser.add_argument(
        "directory",
        help="Папка объекта, например NGC5906",
    )
    parser.add_argument(
        "--target",
        help="Явное имя или путь к итоговому FITS-файлу",
    )

    args = parser.parse_args()

    try:
        target = find_target_fits(args.directory, args.target)
    except TargetSelectionError as exc:
        print(f"\nОшибка выбора: {exc}", file=sys.stderr)
        return 1

    print(f"\nИтоговый FITS-файл: {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
