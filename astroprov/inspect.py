
from pathlib import Path
import argparse

from astropy.io import fits


# Папки, в которых ожидаются исходные астрономические кадры.
SOURCE_DIRS = {
    "lights",
    "darks",
    "bias",
    "flats",
    "offset",
    "offsets",
}


# Расширения файлов, которые будем проверять как возможные FITS.
FITS_EXTENSIONS = {".fit", ".fits", ".fts"}


def find_source_directories(object_dir: Path) -> list[Path]:
    """Находит известные папки с исходниками непосредственно в папке объекта."""
    found = []

    for path in sorted(object_dir.iterdir()):
        if path.is_dir() and path.name.lower() in SOURCE_DIRS:
            found.append(path)

    return found


def find_fits_files(object_dir: Path) -> list[Path]:
    """Рекурсивно находит файлы с типичными расширениями FITS."""
    found = []

    for path in sorted(object_dir.rglob("*")):
        if not path.is_file():
            continue

        if path.suffix.lower() in FITS_EXTENSIONS:
            found.append(path)

    return found


def inspect_fits(path: Path) -> dict:
    """Читает заголовки FITS и собирает краткую информацию о файле."""
    result = {
        "path": str(path),
        "valid_fits": False,
        "hdu_count": 0,
        "primary_header": {},
        "has_passport": False,
        "error": None,
    }

    try:
        with fits.open(path, mode="readonly", memmap=True) as hdul:
            result["valid_fits"] = True
            result["hdu_count"] = len(hdul)

            header = hdul[0].header

            # Берём только полезные для первичной диагностики поля.
            for key in (
                "SIMPLE",
                "BITPIX",
                "NAXIS",
                "OBJECT",
                "DATE-OBS",
                "EXPTIME",
                "INSTRUME",
                "PROVVER",
                "PROVID",
            ):
                if key in header:
                    result["primary_header"][key] = header[key]

            # Пока проверяем наличие признака паспорта.
            # Полную проверку паспорта реализуем позже.
            result["has_passport"] = (
                "PROVVER" in header and "PROVID" in header
            )

    except Exception as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"

    return result



def inspect_object(object_dir: Path, verbose: bool = False) -> None:
    """Компактный отчёт о структуре папки и найденных FITS-файлах."""
    object_dir = object_dir.resolve()

    if not object_dir.exists():
        raise FileNotFoundError(f"Папка не найдена: {object_dir}")

    if not object_dir.is_dir():
        raise NotADirectoryError(f"Это не папка: {object_dir}")

    print(f"\nОбъект: {object_dir.name}")

    print("\nИСХОДНЫЕ ДАННЫЕ")
    source_dirs = find_source_directories(object_dir)

    if not source_dirs:
        print("  Папки исходных кадров не найдены.")
    else:
        for directory in source_dirs:
            count = sum(1 for p in directory.rglob("*") if p.is_file())
            print(f"  {directory.name + '/':12} {count:>5} файлов")

    print("\nАНАЛИЗ FITS")
    fits_files = find_fits_files(object_dir)

    if not fits_files:
        print("  FITS-файлы не найдены.")
        return

    total = len(fits_files)
    valid = 0
    with_passport = 0
    errors = []
    by_folder = {}

    for path in fits_files:
        info = inspect_fits(path)
        relative_path = path.relative_to(object_dir)
        folder = relative_path.parent.as_posix()

        by_folder[folder] = by_folder.get(folder, 0) + 1

        if info["valid_fits"]:
            valid += 1

            if info["has_passport"]:
                with_passport += 1
        else:
            errors.append((relative_path, info["error"]))

        if verbose:
            status = "OK" if info["valid_fits"] else "ОШИБКА"
            passport = "есть" if info["has_passport"] else "нет"
            print(f"  [{status}] {relative_path} | паспорт: {passport}")

            if info["valid_fits"]:
                header = info["primary_header"]
                object_name = header.get("OBJECT", "—")
                exposure = header.get("EXPTIME", "—")
                print(
                    f"       OBJECT={object_name}; "
                    f"EXPTIME={exposure}; HDU={info['hdu_count']}"
                )
            elif info["error"]:
                print(f"       {info['error']}")

    print(f"  Всего найдено:  {total}")
    print(f"  Читаются:       {valid}")
    print(f"  С ошибками:     {len(errors)}")
    print(f"  С паспортом:    {with_passport}")

    print("\nПО ПАПКАМ")
    for folder, count in sorted(by_folder.items()):
        display_folder = folder if folder != "." else "(корень)"
        print(f"  {display_folder + '/':20} {count:>5} FITS")

    if errors:
        print("\nФАЙЛЫ С ОШИБКАМИ")
        for path, error in errors:
            print(f"  {path}: {error}")

    print(
        "\nИТОГ: "
        + (
            "найдены FITS-файлы с признаками паспорта."
            if with_passport
            else "признаки паспорта не обнаружены."
        )
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Компактное исследование папки астрономического объекта."
    )
    parser.add_argument(
        "object_dir",
        type=Path,
        help="Путь к папке объекта, например M101/",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Показывать подробности по каждому FITS-файлу.",
    )
    args = parser.parse_args()

    try:
        inspect_object(args.object_dir, verbose=args.verbose)
    except (FileNotFoundError, NotADirectoryError) as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    main()