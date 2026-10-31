# Copyright (C) 2026 Aliaksei Chernik
#
# This file is part of AstroProv.
#
# AstroProv is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# AstroProv is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.

"""Module for writing and reading the cryptographic passport in a FITS file."""

import io
import hashlib
from pathlib import Path
from astropy.io import fits


def write_provenance(fits_path: Path | str, prov_data: dict) -> None:
    """Adds the passport HDU (PROVENANCE) to the FITS file."""
    fits_path = Path(fits_path).resolve()

    if not fits_path.is_file():
        raise FileNotFoundError(f"FITS file not found: {fits_path}")

    with fits.open(fits_path, mode="update") as hdul:
        if "PROVENANCE" in hdul:
            del hdul["PROVENANCE"]

        hdu = fits.ImageHDU(name="PROVENANCE")
        
        # Main data
        hdu.header["PROVVER"] = (str(prov_data.get("version", "1.0")), "")
        hdu.header["PROVID"] = (str(prov_data.get("id", "")), "")
        hdu.header["PROVTYPE"] = (str(prov_data.get("schema", "astroprov-standalone-v1")), "")
        hdu.header["PROVMRKL"] = (str(prov_data.get("merkle_root", "")), "")
        hdu.header["PROVDATE"] = (str(prov_data.get("date", "")), "")
        hdu.header["PROVTOOL"] = (str(prov_data.get("tool", "AstroProv")), "")
        
        # Statistics
        hdu.header["PROVLIT"] = (int(prov_data.get("lights_count", 0)), "")
        hdu.header["PROVDRK"] = (int(prov_data.get("darks_count", 0)), "")
        hdu.header["PROVBIA"] = (int(prov_data.get("biases_count", 0)), "")
        hdu.header["PROVFLT"] = (int(prov_data.get("flats_count", 0)), "")
        hdu.header["PROVEXP"] = (float(prov_data.get("exptime", 0.0)), "")

        # Cryptography
        hdu.header["PROVPUBK"] = (str(prov_data.get("pubkey_fingerprint", "")), "")
        hdu.header["PROVHASH"] = (str(prov_data.get("report_hash", "")), "")

        # Ed25519 signature (split into 3 parts to fit FITS 68-char limit)
        sig = str(prov_data.get("signature", ""))
        hdu.header["PROVSIG1"] = (sig[:43], "")
        hdu.header["PROVSIG2"] = (sig[43:86], "")
        hdu.header["PROVSIG3"] = (sig[86:], "")

        hdul.append(hdu)
        hdul.flush()


def read_provenance(fits_path: Path | str) -> dict | None:
    """Reads the passport from the FITS file."""
    fits_path = Path(fits_path).resolve()

    if not fits_path.is_file():
        raise FileNotFoundError(f"FITS file not found: {fits_path}")

    with fits.open(fits_path, mode="readonly", memmap=False) as hdul:
        if "PROVENANCE" not in hdul:
            return None

        header = hdul["PROVENANCE"].header
        
        # Reassemble the signature
        sig_parts = [
            str(header.get("PROVSIG1", "")),
            str(header.get("PROVSIG2", "")),
            str(header.get("PROVSIG3", ""))
        ]
        full_signature = "".join(sig_parts).strip()

        return {
            "version": header.get("PROVVER"),
            "id": header.get("PROVID"),
            "schema": header.get("PROVTYPE"), # Изменено здесь
            "merkle_root": header.get("PROVMRKL"),
            "date": header.get("PROVDATE"),
            "tool": header.get("PROVTOOL"),
            "lights_count": header.get("PROVLIT"),
            "darks_count": header.get("PROVDRK"),
            "biases_count": header.get("PROVBIA"),
            "flats_count": header.get("PROVFLT"),
            "exptime": header.get("PROVEXP"),
            "pubkey_fingerprint": header.get("PROVPUBK"),
            "report_hash": header.get("PROVHASH"),
            "signature": full_signature,
        }


def calculate_fits_data_hash(fits_path: Path | str) -> str:
    """
    Computes the SHA-256 hash of the FITS file, COMPLETELY IGNORING 
    the 'PROVENANCE' HDU and the service headers that change when it is added.
    """
    fits_path = Path(fits_path).resolve()
    if not fits_path.is_file():
        raise FileNotFoundError(f"FITS file not found: {fits_path}")

    with fits.open(fits_path, mode="readonly", memmap=False) as hdul:
        # Build a copy of the HDUList without the passport
        clean_hdul = fits.HDUList(
            [hdu.copy() for hdu in hdul if hdu.name != "PROVENANCE"]
        )
        
        # Clear the service headers that Astropy changes automatically
        for hdu in clean_hdul:
            for key in ("EXTEND", "CHECKSUM", "DATASUM"):
                if key in hdu.header:
                    del hdu.header[key]
        
        # Save to an in-memory buffer (checksum=False to avoid generating new sums)
        buffer = io.BytesIO()
        clean_hdul.writeto(buffer, checksum=False)
        data_bytes = buffer.getvalue()

    return hashlib.sha256(data_bytes).hexdigest()


if __name__ == "__main__":
    import sys
    
    if len(sys.argv) < 2:
        print("Usage: python -m astroprov.fits_prov <path_to_fits>")
        sys.exit(1)
        
    test_file = sys.argv[1]
    
    print(f"Reading passport from {test_file}...")
    try:
        prov = read_provenance(test_file)
        if prov:
            print("\nPassport found:")
            for k, v in prov.items():
                if k == "signature" and v:
                    print(f"  {k}: {v[:20]}...{v[-10:]} (length: {len(v)})")
                else:
                    print(f"  {k}: {v}")
            
            print("\nComputing the pure data hash (without passport)...")
            data_hash = calculate_fits_data_hash(test_file)
            print(f"  Data SHA-256: {data_hash}")
            
        else:
            print("Passport not found (HDU 'PROVENANCE' is missing).")
    except (OSError, ValueError) as e:
        print(f"Error: {e}")