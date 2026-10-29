![Version](https://img.shields.io/badge/version-v1.4.0-blue)
![License](https://img.shields.io/badge/license-GPL--3.0-green)
![Python](https://img.shields.io/badge/python-3.10%2B-blue)

# AstroProv: Cryptographic Provenance for Astronomical FITS Data

AstroProv is a lightweight Python framework designed to guarantee the **integrity**, **authenticity**, and **provenance** of astronomical data generated through the standard observational pipeline (NINA -> Siril). 

While protocols like FAIR (Findable, Accessible, Interoperable, Reusable) govern public data archives, there is a critical lack of security during the proprietary/embargo phase. AstroProv fills this gap by embedding a cryptographically signed, self-contained passport directly into the final FITS file.

## Video Demonstration

Watch a short demonstration of AstroProv in action, showing the sealing process, passport inspection, and cryptographic verification:

[![AstroProv Demo](https://img.youtube.com/vi/dAb1o1f6iws/0.jpg)](https://www.youtube.com/watch?v=dAb1o1f6iws)

## Key Features

*   **Integrity (SHA-256 & Merkle Trees):** Calculates cryptographic hashes of all raw frames (lights, darks, biases) and builds a Merkle Tree to create a single, tamper-evident root hash.
*   **Authenticity (Ed25519 Signatures):** Uses asymmetric cryptography to digitally sign the processing report and all passport metadata. This guarantees non-repudiation (proof of who processed the data) and prevents tampering with statistics.
*   **Trusted Timestamps (RFC 3161):** Integrates with the [FreeTSA](https://freetsa.org/) Time Stamping Authority. This provides cryptographic proof of *when* the file was sealed, preventing backdating attacks even if a private key is compromised. Verification is performed fully offline using bundled public certificates.
*   **Self-Contained FITS Passport:** Injects a custom `PROVENANCE` HDU (Header/Data Unit) directly into the stacked FITS file. The file carries its own metadata, statistics, and signature, requiring no external JSON files for basic inspection.
*   **Anti-Tampering:** Instantly detects if pixels, `.seq` sequence files, JSON reports, or passport fields (e.g., frame counts, merkle roots) have been modified, preventing context-swapping and data manipulation.
*   **Cross-Platform:** Fully tested on **Windows 10** and **Linux (Debian)**. The framework automatically detects the Siril CLI executable across different operating systems.

## Architecture

AstroProv is built around a chain of trust:

1.  **Raw Data**: Hashed and organized into a Merkle Tree.
2.  **Processing**: Siril calibrates and aligns the frames.
3.  **Reports**: A JSON manifest links the raw NINA frames to the processed Siril frames.
4.  **Final Stack**: The `result.fit` file is generated.
5.  **Sealing**: A digital signature is generated from the JSON report and passport data.
    *   *Timestamping*: The report hash is sent to [FreeTSA](https://freetsa.org/) (RFC 3161 TSA), which returns a cryptographically signed `.tsr` token confirming the exact time.
6.  **Embedding**: The hash, statistics, and signature are baked into the `PROVENANCE` HDU of the FITS file.

### Core Modules

*   `astroprov/utils.py`: Shared utility functions (e.g., memory-bounded SHA-256 file hashing).
*   `astroprov/snapshot.py`: Hashes input directories and builds Merkle Trees.
*   `astroprov/mapping.py`: Matches NINA raw files to Siril processed files using FITS headers.
*   `astroprov/processing.py`: Creates JSON reports for the "pre-stack" and "post-stack" states.
*   `astroprov/signature.py`: Handles Ed25519 key generation, signing, and verification.
*   `astroprov/fits_prov.py`: Embeds and reads the `PROVENANCE` HDU, and calculates the canonical data hash.
*   `astroprov/verify.py`: Validates the entire chain of trust (hashes + signature + FITS HDU).
*   `astroprov/inspect.py`: A human-readable CLI to view the passport inside a FITS file.
*   `astroprov/siril_wrapper.py`: Shared wrapper for locating and executing the Siril CLI (prevents code duplication).
*   `astroprov/timestamping.py`: Handles RFC 3161 Time Stamping Authority (TSA) requests and offline verification.
*   `astroprov/cli/`: Command-line interfaces (orchestrators integrating with Siril).
*   `astroprov/cli/seal.py`: Standalone tool to seal arbitrary FITS files without Siril.

## Installation

*(Note: PyPI package is coming soon. For now, use a local clone)*

1. Clone the repository:
```bash
git clone https://github.com/kybik0606/AstroProv.git
cd AstroProv
```

2. Create a virtual environment and install the required dependencies:
```bash
python -m venv venv

# On Windows:
venv\Scripts\activate

# On Linux:
source venv/bin/activate

pip install -r requirements.txt
```

## Usage

AstroProv provides three main workflows:

> *Note on Siril CLI: By default, the pipeline automatically searches for `siril-cli` in your system `PATH`. If it is installed in a custom location, you can specify it using the `--siril-cli /path/to/siril-cli` argument.*

### 1. Full Auto Pipeline (No manual intervention)
This mode automatically calibrates, stacks all available frames, seals, and verifies the data.

```bash
python -m astroprov.cli.full_auto_pipeline M101 --script "siril_scripts/OSC_Preprocessing_AstroProv_WithoutDBF.ssf"
```

### 2. Semi-Auto Pipeline (Manual Quality Control)
This mode pauses after calibration, allowing you to open Siril (GUI), manually reject bad frames (e.g., clouds, satellites), and then resume stacking.

```bash
python -m astroprov.cli.auto_pipeline M101 --script "siril_scripts/OSC_Preprocessing_AstroProv_WithoutDBF.ssf"
```

### 3. Standalone Sealing (No Siril required)
You can cryptographically seal any existing FITS file (e.g., processed in PixInsight or ASTAP) without running the full processing pipeline. The tool automatically detects metadata (like frame counts and exposure) from standard FITS headers.

```bash
python -m astroprov.cli.seal "path/to/your/image.fits" --object "M31"
```

If metadata is missing in the FITS header, you can manually override it using `--lights` and `--exptime` arguments.

### Inspecting and Verifying Data

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

## Testing

The project includes automated tests for both standard pipeline execution and security (tamper-resistance). All core modules have been tested in isolation, and the full suite runs successfully on both Windows and Linux. Test execution times are benchmarked to help identify performance bottlenecks.

To run the predefined end-to-end tests, place your test object directories inside a `test_data/` folder in the project root (e.g., `test_data/M101`). This folder is ignored by Git to prevent committing large FITS files.

```bash
# Run end-to-end pipeline tests (4 scenarios with benchmarking)
python tests/run_all_tests.py

# Run tamper-resistance tests against a generated FITS file (A2)
python tests/run_security_tests.py "test_data/M101/process/r_pp_light__stacked.fit"

# You can also run tamper-resistance tests on standalone sealed files
python tests/run_security_tests.py "test_data/result.fit"
```

## The "Chicken and Egg" Solution

Adding a passport to a FITS file changes the file's physical hash. To solve this, AstroProv uses a **Canonical FITS Data Hash** (`calculate_fits_data_hash`). It calculates the SHA-256 hash of the image data *while ignoring* the `PROVENANCE` HDU and standard FITS checksums (`EXTEND`, `CHECKSUM`). This allows the passport to be re-signed or updated without invalidating the data hash.

## Trusted Timestamps (RFC 3161)

While Ed25519 signatures prove *who* created the data, they cannot inherently prove *when* it was created (a compromised key could be used to backdate a file). To solve this, AstroProv integrates with **FreeTSA**, a free RFC 3161 Time Stamping Authority.

1.  When a file is sealed, the SHA-256 hash of the generated JSON report is sent to FreeTSA.
2.  FreeTSA returns a `.tsr` (TimeStampToken) file, cryptographically signed by their trusted X.509 certificate.
3.  When `astroprov.verify` runs, it automatically detects the `.tsr` file and verifies it **offline** using the public certificates bundled in `astroprov/certs/`.

This establishes a true chain of trust: the data is authentic (Ed25519), and it existed at a specific point in time (FreeTSA).

## Security & Robustness

In version 1.3.0, the codebase underwent a comprehensive security and architectural audit:

*   **CLI Exit Codes:** Fixed a critical bug where `verify.py` always returned exit code `0` (success) even on failure. Now, all CLIs correctly return `0` (PASS) or `1` (FAIL), making them safe to use in automated CI/CD pipelines.
*   **Path Traversal Protection:** Added strict validation in `verify.py` and `processing.py` to ensure recorded paths in JSON reports cannot escape the project directory (preventing arbitrary file read vulnerabilities).
*   **Safe FITS Parsing:** Introduced `_safe_int` and `_safe_float` helpers in `inspect.py` and `seal.py`. Malformed or malicious FITS headers (e.g., `EXPTIME = "unknown"`) will no longer crash the application; they are handled gracefully with `[NO DATA]` messages.
*   **Strict Error Handling:** Removed broad `except Exception` blocks across the codebase. Specific exceptions (`OSError`, `ValueError`, `RuntimeError`) are now caught and propagated correctly.
*   **Private Key Protection:** The private Ed25519 key is explicitly restricted to `chmod 600` (owner read/write only) upon generation on Unix-like systems.
*   **Tamper-Test Verified:** A dedicated test suite (`tests/run_security_tests.py`) asserts that tampering with any of the 7 passport header fields instantly triggers a `FAIL` verdict.

In version 1.4.0, we added trusted third-party verification:

*   **Trusted Third-Party (TTP):** Integrated RFC 3161 timestamps via [FreeTSA](https://freetsa.org/) to prevent timestamp spoofing and backdating attacks. Proof of existence is verified fully offline using X.509 certificates bundled directly into the repository.

## License

Copyright (C) 2026 Aliaksei Chernik

AstroProv is free software: you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.

AstroProv is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
GNU General Public License for more details.

You should have received a copy of the GNU General Public License
along with this program. If not, see <https://www.gnu.org/licenses/>.

### Acknowledgements

*   The Siril preprocessing scripts in `siril_scripts/` (e.g., `OSC_Preprocessing_AstroProv_WithoutFlat.ssf`) are modified derivatives of the original Siril scripts (e.g., `OSC_Preprocessing_WithoutFlat.ssf`) created by **Cyril Richard** (C) 2020.
*   Modifications were made by Aliaksei Chernik. The modified scripts were renamed by adding `AstroProv` to the filenames to distinguish them from the originals. The final stacking command was removed from all scripts to allow AstroProv to perform cryptographic verification between the calibration/alignment and stacking phases.
*   See the original GPL-3.0 license terms for details.