"""Module for writing and reading the cryptographic passport in a FITS file."""

import io
import hashlib
from pathlib import Path
from astropy.io import fits


def write_provenance(fits_path: Path, prov_data: dict) -> None:
    """Adds the passport HDU (PROVENANCE) to the FITS file."""
    fits_path = fits_path.resolve()

    if not fits_path.is_file():
        raise FileNotFoundError(f"FITS file not found: {fits_path}")

    with fits.open(fits_path, mode="update") as hdul:
        if "PROVENANCE" in hdul:
            del hdul["PROVENANCE"]

        hdu = fits.ImageHDU(name="PROVENANCE")
        
        # Main data
        hdu.header["PROVVER"] = (str(prov_data.get("version", "1.0")), "")
        hdu.header["PROVID"] = (str(prov_data.get("id", "")), "")
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

        # Ed25519 signature (split into 3 parts)
        sig = str(prov_data.get("signature", ""))
        hdu.header["PROVSIG1"] = (sig[:43], "")
        hdu.header["PROVSIG2"] = (sig[43:86], "")
        hdu.header["PROVSIG3"] = (sig[86:], "")

        hdul.append(hdu)
        hdul.flush()


def read_provenance(fits_path: Path) -> dict | None:
    """Reads the passport from the FITS file."""
    fits_path = fits_path.resolve()

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


def calculate_fits_data_hash(fits_path: Path) -> str:
    """
    Computes the SHA-256 hash of the FITS file, COMPLETELY IGNORING the 'PROVENANCE' HDU
    and the service headers that change when it is added.
    """
    fits_path = fits_path.resolve()
    if not fits_path.is_file():
        raise FileNotFoundError(f"FITS file not found: {fits_path}")

    with fits.open(fits_path, mode="readonly", memmap=False) as hdul:
        # Build a copy of the HDUList without the passport
        clean_hdul = fits.HDUList([hdu.copy() for hdu in hdul if hdu.name != "PROVENANCE"])
        
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
    # Simple test when running the file directly
    import sys
    
    if len(sys.argv) < 2:
        print("Usage: python -m astroprov.fits_prov <path_to_fits>")
        sys.exit(1)
        
    test_file = Path(sys.argv[1])
    
    print(f"Reading passport from {test_file.name}...")
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
    except Exception as e:
        print(f"Error: {e}")