
import argparse
from pathlib import Path

from astropy.io import fits


def inspect_file(path: Path) -> None:
    print(f"\n{'=' * 60}")
    print(f"Файл: {path}")
    print(f"Размер файла: {path.stat().st_size:,} байт")

    with fits.open(path, mode="readonly", memmap=False) as hdul:
        print(f"Количество HDU: {len(hdul)}")

        for index, hdu in enumerate(hdul):
            print(f"\nHDU #{index}: {type(hdu).__name__}")
            print(f"  Размеры данных: {hdu.data.shape if hdu.data is not None else 'нет данных'}")
            print(f"  BITPIX: {hdu.header.get('BITPIX', '—')}")
            print(f"  NAXIS: {hdu.header.get('NAXIS', '—')}")

            if index == 0:
                for key in (
                    "OBJECT",
                    "DATE-OBS",
                    "EXPTIME",
                    "INSTRUME",
                    "FILTER",
                    "BUNIT",
                    "NCOMBINE",
                    "STACKCNT",
                    "IMAGETYP",
                    "HISTORY",
                ):
                    value = hdu.header.get(key)
                    if value is not None:
                        print(f"  {key}: {value}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Сравнение структуры двух FITS-файлов."
    )
    parser.add_argument("files", nargs="+", type=Path)
    args = parser.parse_args()

    for path in args.files:
        if not path.is_file():
            print(f"\nФайл не найден: {path}")
            continue

        try:
            inspect_file(path)
        except Exception as exc:
            print(f"\nОшибка чтения {path}: {exc}")


if __name__ == "__main__":
    main()
