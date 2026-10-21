"""Модуль для записи и чтения криптографического паспорта в FITS-файл."""

from pathlib import Path
from astropy.io import fits


def write_provenance(fits_path: Path, prov_data: dict) -> None:
    """Добавляет HDU с паспортом (PROVENANCE) в FITS-файл."""
    fits_path = fits_path.resolve()

    if not fits_path.is_file():
        raise FileNotFoundError(f"FITS-файл не найден: {fits_path}")

    with fits.open(fits_path, mode="update") as hdul:
        if "PROVENANCE" in hdul:
            del hdul["PROVENANCE"]

        hdu = fits.ImageHDU(name="PROVENANCE")
        
        # Пустые комментарии, чтобы избежать WARNING: Card is too long
        hdu.header["PROVVER"] = (str(prov_data.get("version", "1.0")), "")
        hdu.header["PROVID"] = (str(prov_data.get("id", "")), "")
        hdu.header["PROVMRKL"] = (str(prov_data.get("merkle_root", "")), "")
        hdu.header["PROVDATE"] = (str(prov_data.get("date", "")), "")
        hdu.header["PROVTOOL"] = (str(prov_data.get("tool", "AstroProv")), "")
        
        # НОВЫЕ КЛЮЧИ: Статистика кадров и выдержка
        hdu.header["PROVLIT"] = (int(prov_data.get("lights_count", 0)), "")
        hdu.header["PROVDRK"] = (int(prov_data.get("darks_count", 0)), "")
        hdu.header["PROVBIA"] = (int(prov_data.get("biases_count", 0)), "")
        hdu.header["PROVFLT"] = (int(prov_data.get("flats_count", 0)), "")
        hdu.header["PROVEXP"] = (float(prov_data.get("exptime", 0.0)), "")

        hdul.append(hdu)
        hdul.flush()


def read_provenance(fits_path: Path) -> dict | None:
    """Читает паспорт из FITS-файла."""
    fits_path = fits_path.resolve()

    if not fits_path.is_file():
        raise FileNotFoundError(f"FITS-файл не найден: {fits_path}")

    with fits.open(fits_path, mode="readonly", memmap=False) as hdul:
        if "PROVENANCE" not in hdul:
            return None

        header = hdul["PROVENANCE"].header

        return {
            "version": header.get("PROVVER"),
            "id": header.get("PROVID"),
            "merkle_root": header.get("PROVMRKL"),
            "date": header.get("PROVDATE"),
            "tool": header.get("PROVTOOL"),
            "lights_count": header.get("PROVLIT"),
            "darks_count": header.get("PROVDRK"),
            "biases_count": header.get("PROVBIA"),
            "flats_count": header.get("PROVFLT"),
            "exptime": header.get("PROVEXP"),
        }

if __name__ == "__main__":
    # Простой тест при запуске файла напрямую
    import sys
    
    if len(sys.argv) < 2:
        print("Использование: python -m astroprov.fits_prov <path_to_fits>")
        sys.exit(1)
        
    test_file = Path(sys.argv[1])
    
    print(f"Чтение паспорта из {test_file.name}...")
    try:
        prov = read_provenance(test_file)
        if prov:
            print("Паспорт найден:")
            for k, v in prov.items():
                print(f"  {k}: {v}")
        else:
            print("Паспорт не найден (HDU 'PROVENANCE' отсутствует).")
    except Exception as e:
        print(f"Ошибка: {e}")