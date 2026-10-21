# AstroProv: Cryptographic Provenance for Astronomical FITS Data

AstroProv is a high-performance Python framework designed to guarantee the **integrity**, **authenticity**, and **provenance** of astronomical data generated through the standard observational pipeline (NINA -> Siril). 

While protocols like FAIR (Findable, Accessible, Interoperable, Reusable) govern public data archives, there is a critical lack of security during the proprietary/embargo phase. AstroProv fills this gap by embedding a cryptographically signed, self-contained passport directly into the final FITS file.

## 🌟 Key Features

*   **Integrity (SHA-256 & Merkle Trees):** Calculates cryptographic hashes of all raw frames (lights, darks, biases) and builds a Merkle Tree to create a single, tamper-evident root hash.
*   **Authenticity (Ed25519 Signatures):** Uses asymmetric cryptography to digitally sign the processing report. This guarantees non-repudiation (proof of who processed the data).
*   **Self-Contained FITS Passport:** Injects a custom `PROVENANCE` HDU (Header/Data Unit) directly into the stacked FITS file. The file carries its own metadata, statistics, and signature, requiring no external JSON files for basic inspection.
*   **Anti-Tampering:** Instantly detects if pixels, `.seq` sequence files, or JSON reports have been modified, preventing context-swapping and data manipulation.
*   **Full Automation (Siril CLI):** Orchestrates Siril's command-line interface to automate the entire calibration and stacking process.

## 🏗️ Architecture

AstroProv is built around a chain of trust:

1.  **Raw Data**: Hashed and organized into a Merkle Tree.
2.  **Processing**: Siril calibrates and aligns the frames.
3.  **Reports**: A JSON manifest links the raw NINA frames to the processed Siril frames.
4.  **Final Stack**: The `result.fit` file is generated.
5.  **Sealing**: A digital signature is generated from the JSON report.
6.  **Embedding**: The hash, statistics, and signature are baked into the `PROVENANCE` HDU of the FITS file.

### Core Modules

*   `astroprov/snapshot.py`: Hashes input directories and builds Merkle Trees.
*   `astroprov/mapping.py`: Matches NINA raw files to Siril processed files using FITS headers.
*   `astroprov/processing.py`: Creates JSON reports for the "pre-stack" and "post-stack" states.
*   `astroprov/signature.py`: Handles Ed25519 key generation, signing, and verification.
*   `astroprov/fits_prov.py`: Embeds and reads the `PROVENANCE` HDU, and calculates the canonical data hash.
*   `astroprov/verify.py`: Validates the entire chain of trust (hashes + signature + FITS HDU).
*   `astroprov/inspect.py`: A human-readable CLI to view the passport inside a FITS file.

## 🚀 Installation

*(Note: PyPI package is coming soon. For now, use a local clone)*

1. Clone the repository:
```bash
git clone https://github.com/yourusername/astroprov.git
cd astroprov
```

2. Install the required dependencies:
```bash
pip install astropy cryptography
```

3. (Optional) Install in editable mode:
```bash
pip install -e .
```

## 💻 Usage

AstroProv provides two main workflows:

### 1. Full Auto Pipeline (No manual intervention)
This mode automatically calibrates, stacks all available frames, seals, and verifies the data.

```bash
python -m astroprov.full_auto_pipeline M101 --script "siril_scripts/Preprocessing_astroprov.ssf"
```

### 2. Semi-Auto Pipeline (Manual Quality Control)
This mode pauses after calibration, allowing you to open Siril (GUI), manually reject bad frames (e.g., clouds, satellites), and then resume stacking.

```bash
python -m astroprov.auto_pipeline M101 --script "siril_scripts/Preprocessing_astroprov.ssf"
```

### 🔍 Inspecting and Verifying Data

**View the Passport (Human-Readable):**
You can inspect the provenance of any processed FITS file without needing the original raw data.

```bash
python -m astroprov.inspect "M101/process/r_pp_light__stacked.fit"
```
*Output includes: Passport ID, Merkle Root, Frame Counts (Lights/Darks/Biases), Total Exposure Time, and Author Fingerprint.*

**Verify Integrity (Cryptographic Check):**
Re-calculates all hashes, validates the Ed25519 signature, and checks the `.seq` file state.

```bash
python -m astroprov.verify "M101/process/r_pp_light__stacked.fit"
```
*Verdict: `PASS` (Data is authentic and unmodified) or `FAIL` (Tampering detected).*

## 🔒 The "Chicken and Egg" Solution

Adding a passport to a FITS file changes the file's physical hash. To solve this, AstroProv uses a **Canonical FITS Hash** (`calculate_fits_data_hash`). It calculates the SHA-256 hash of the image data *while ignoring* the `PROVENANCE` HDU and standard FITS checksums (`EXTEND`, `CHECKSUM`). This allows the passport to be re-signed or updated without invalidating the data hash.

## 📄 License

This project is licensed under the **GNU General Public License v3.0 (GPL-3.0-or-later)**. 

### Acknowledgements

*   The Siril preprocessing script (`siril_scripts/Preprocessing_astroprov.ssf`) is a modified derivative of the original `Preprocessing_WithoutFlat.ssf` (v1.1) created by **Cyril Richard** (C) 2020. 
*   Modifications were made by Aliaksei Chernik to remove the final stacking command, allowing AstroProv to perform cryptographic verification between calibration and stacking. 
*   See the original GPL-3.0 license terms for details.
