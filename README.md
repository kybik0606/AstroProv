![Version](https://img.shields.io/badge/version-v1.4.1-blue)
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
*   `astroprov/cli/`: Command-line tools (pipelines orchestrating Siril and standalone sealing).

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

AstroProv provides four main workflows:

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

### 3. Standalone Sealing (Single FITS file)
You can cryptographically seal any existing FITS file without running the full processing pipeline. The tool automatically detects metadata from standard FITS headers.

```bash
python -m astroprov.cli.seal "path/to/your/image.fits" --object "M31"
```

### 4. Universal Collection Sealing (FITS + Raw Sources)
If you processed your data in third-party software but want to cryptographically link your final stack to the original raw frames, use this mode. It builds a Merkle Tree of your raw lights, darks, and flats, and embeds it into the passport.

```bash
python -m astroprov.cli.seal_collection "path/to/your/stack.fit" --raw "path/to/raw_folder" --object "M31"
```

## Detailed CLI Reference

While the pipelines automate the entire process, AstroProv also provides granular modular tools for advanced users. All tools output clear, copy-pasteable status messages.

### Scanning Source Data (`snapshot.py`)
Before processing, AstroProv scans your raw directories (`lights`, `darks`, `bias`, `flats`) to calculate SHA-256 hashes and build a Merkle Tree for each category. This creates a foundational manifest.

```bash
python -m astroprov.snapshot "test_data/M101"
```

Expected Output:

```text
  [*] Object: M101
  [*] Starting source data scan...
  lights: 38/38 done                                        
  darks: 20/20 done                                        
  bias: 30/30 done                                      
  [OK] Manifest created
    - ID: 4a6c2c78-4ec5-4b9e-846b-a4a1aac20b1d
    - File: 4a6c2c78-4ec5-4b9e-846b-a4a1aac20b1d.json
    - lights: 38 files (Merkle: 67facef9ec8f...)
    - darks: 20 files (Merkle: d614009d205b...)
    - bias: 30 files (Merkle: 34ff15fb0d54...)
  [INFO] The manifest records discovered files, not proof of their actual participation in the stack.
```

### Creating Frame Mapping (`mapping.py`)
Siril renames and processes frames, making it difficult to track which original NINA frame corresponds to which processed file. This module uses FITS headers (`DATE-OBS`, `EXPTIME`, `OBJECT`) to match them. It safely handles frames that were rejected during stacking.

```bash
python -m astroprov.mapping "test_data/M101"
```

Expected Output:

```text
  [WARN] Frame mismatch: 38 originals, 37 processed.
    Some frames may have been rejected by Siril.
  [WARN] Missing frame: 2026-08-25_22-56-11__23.80_120.00s_0037.fits
  [*] Backup saved: astroprov_frame_mapping.backup.json
  [OK] Mapping updated: astroprov_frame_mapping.json
    - Total originals: 38
    - Successfully matched: 37
    - Lost (rejected): 1
  [OK] Source hashes verified.
```

### Generating Post-Stack Report (`processing.py`)
Once stacking is complete, this module reads the `.seq` file and the final FITS header to cross-check frame counts. It then generates a comprehensive JSON report that links the sequence, the mapping, and the output FITS file. This report is what gets signed later.

```bash
python -m astroprov.processing "test_data/M101/process/r_pp_light_.seq" "test_data/M101/process/r_pp_light__stacked.fit" --mapping "test_data/M101/astroprov_frame_mapping.json"
```

Expected Output:

```text
  [OK] Post-stack report saved: processing-20261031T165717Z.json
    - Output SHA-256: 061182fc64d31ba7...
  [*] Sequence Summary:
    - Total frames: 37
    - Selected: 37
    - Excluded: 0
    - Excluded indices: []
  [*] FITS Cross-check:
    [OK] Frame count consistent with .seq
  [*] Mapping Validation:
    - Matched: 37/37
    - Selected/Excluded: 37/0
    - Consistent: False
  [*] Pre-stack Link: Not linked
  [WARN] Frame mapping validation found missing entries, hash mismatches, or other inconsistencies.
```

> *(Note: The `[WARN]` is expected here because the mapping contains 38 original frames, but Siril rejected 1 frame, resulting in 37 processed frames in the `.seq` file)*

### Reading Raw Passport Data (`fits_prov.py`)
This is a low-level utility to read the `PROVENANCE` HDU directly from a FITS file without needing external reports. It also recalculates the "pure data hash" (ignoring the passport HDU) to prove the image pixels haven't changed.

```bash
python -m astroprov.fits_prov "test_data/M101/process/r_pp_light__stacked.fit"
```

Expected Output:

```text
Reading passport from test_data/M101/process/r_pp_light__stacked.fit...

Passport found:
  version: 1.0
  id: 33d5ddaf-a252-4f92-ad9c-92e4e17f0cd1
  schema: astroprov-standalone-v1
  merkle_root: 67facef9ec8fc924ef399659fc1744bbcfedced14111f83ef5d9e4474d2fcee9
  date: 2026-10-31T16:22:48.916544+00:00
  tool: AstroProv
  lights_count: 38
  darks_count: 0
  biases_count: 0
  flats_count: 0
  exptime: 120.0
  pubkey_fingerprint: 748201ad33df4602
  report_hash: 5e58f404899eb7175aab3cc0bd7b6cd365cddd479d0db4d0e76697c46de0fd50
  signature: e8aa53444466bac63f7d...0249308305 (length: 128)

Computing the pure data hash (without passport)...
  Data SHA-256: 061182fc64d31ba7444e7e49e813251de1e661cc45d7c0a4b76cef7c253547f5
```

### Verifying Integrity (`verify.py`)
The verifier checks the FITS file structure, Ed25519 signatures, FreeTSA timestamps, and (depending on the passport type) the raw source data or pipeline reports. Its output adapts based on how the file was originally sealed.

#### A. Standard / Standalone Verification
Checks the FITS file itself and its signature. Use this if you only have the final FITS file and no raw sources or pipeline reports.

```bash
python -m astroprov.verify "test_data/_demo_seal.fit"
```

Expected Output:

```text
ASTROPROV FITS PROVENANCE VERIFICATION

  [*] Output file: _demo_seal.fit
  [*] Report: processing-standalone-52b6c7cd.json

  [*] Verification Results:

  [OK] Passport found in FITS. ID: standalone-52b6c7cd
  [OK] Report type and stage verified
  [OK] Standalone passport verified
  [OK] JSON report hash matches the one recorded in FITS
  [OK] Ed25519 signature is valid (FP: 498bccc54d73684b)
  [OK] Trusted timestamp (RFC 3161) is valid
  [OK] Output FITS (data without passport): SHA-256 matches

  [OK] VERDICT: PASS — chain verified.
       Integrity, consistency, and Ed25519 signature are all valid.
```

#### B. Deep Verification (Raw Sources)
If you have the original raw frames and sealed them using `seal_collection`, pass the `--raw-dir` flag. The verifier recalculates the Merkle Tree of your raw lights, darks, and flats to ensure they haven't been modified.

```bash
python -m astroprov.verify "test_data/M101/process/r_pp_light__stacked.fit" --raw-dir "test_data/M101"
```

Expected Output:

```text
ASTROPROV FITS PROVENANCE VERIFICATION

  [*] Output file: r_pp_light__stacked.fit
  [*] Verifying raw source data in M101...
  lights: 38/38 done                                        
  darks: 20/20 done                                        
  bias: 30/30 done                                      

  [*] Report: processing-collection-061182fc.json

  [*] Verification Results:

  [OK] Passport found in FITS. ID: collection-061182fc
  [OK] Report type and stage verified
  [OK] Passport ID in FITS matches ID in JSON report
  [OK] JSON report hash matches the one recorded in FITS
  [OK] Ed25519 signature is valid (FP: 22d298b576b6728d)
  [OK] Trusted timestamp (RFC 3161) is valid
  [OK] Output FITS (data without passport): SHA-256 matches
  [OK] [bias] Source data matches (30 files)
  [OK] [darks] Source data matches (20 files)
  [OK] [lights] Source data matches (38 files)

  [OK] VERDICT: PASS — chain verified.
       Integrity, consistency, and Ed25519 signature are all valid.
```

#### C. Pipeline Verification
If the file was created via `full_auto_pipeline` or `auto_pipeline`, the verifier automatically checks `.seq`, mapping, and pre-stack reports. (The pipeline runs this automatically at the end, but you can run it manually later).

```bash
python -m astroprov.verify "test_data/M101/process/r_pp_light__stacked.fit"
```

Expected Output:

```text
ASTROPROV FITS PROVENANCE VERIFICATION

  [*] Output file: r_pp_light__stacked.fit
  Frame check: 37/37 done

  [WARN] Processing report has warnings:
    - Frame mapping validation found missing entries, hash mismatches, or other inconsistencies.

  [*] Report: processing-20261031T171047Z.json

  [*] Verification Results:

  [OK] Passport found in FITS. ID: 291e256f-3ae6-4a83-8e71-7d1b0b030158
  [OK] Report type and stage verified
  [OK] Passport ID in FITS matches ID in JSON report
  [OK] JSON report hash matches the one recorded in FITS
  [OK] Ed25519 signature is valid (FP: 748201ad33df4602)
  [OK] Trusted timestamp (RFC 3161) is valid
  [OK] Output FITS (data without passport): SHA-256 matches
  [OK] Sequence .seq: SHA-256 matches
  [OK] Mapping: SHA-256 matches
  [OK] Pre-stack report: SHA-256 matches
  [OK] Pre-stack report has status validation_failed (expected when frames are rejected)
  [OK] Pre-stack report link verified
  [OK] Hashes of all 37 original/processed frame pairs verified
  [OK] Frame selection is consistent: 37 selected, 0 excluded
  [OK] FITS frame count consistent: 37
  [OK] Mapping validation: only missing frames (expected)

  [OK] VERDICT: PASS — chain verified.
       Integrity, consistency, and Ed25519 signature are all valid.
```

### Viewing Human-Readable Passport (`inspect.py`)
This tool extracts the `PROVENANCE` HDU and formats it into a clean, human-readable report. It includes the passport ID, Merkle roots, calibration frame counts, and total exposure time.

```bash
python -m astroprov.inspect "test_data/M101/process/r_pp_light__stacked.fit"
```

Expected Output:

```text
[*] ASTROPROV PASSPORT: r_pp_light__stacked.fit

  [*] General Info:
    - Passport ID:   33d5ddaf-a252-4f92-ad9c-92e4e17f0cd1
    - Created:       2026-10-31T16:22:48.916544+00:00
    - Tool:          AstroProv
    - Format ver:    1.0

  [*] Source Data Merkle Root:
    - SHA-256:       67facef9ec8fc924ef399659fc1744bbcfedced14111f83ef5d9e4474d2fcee9

  [*] Source Data Composition (FITS passport):
    - Lights:  38
    - Darks:   0
    - Biases:  0
    - Flats:   0
    - Total:   38 files

  [*] Exposure:
    - Single frame:    120.0 s
    - Frames in stack: 37
    - Total:           1 h 14 min (4440 s)

  [INFO] Integrity check: use `python -m astroprov.verify <file>`
```

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

## Copyright

```text
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
```

## Acknowledgements

*   The Siril preprocessing scripts in `siril_scripts/` (e.g., `OSC_Preprocessing_AstroProv_WithoutFlat.ssf`) are modified derivatives of the original Siril scripts (e.g., `OSC_Preprocessing_WithoutFlat.ssf`) created by **Cyril Richard** (C) 2020.
*   Modifications were made by Aliaksei Chernik. The modified scripts were renamed by adding `AstroProv` to the filenames to distinguish them from the originals. The final stacking command was removed from all scripts to allow AstroProv to perform cryptographic verification between the calibration/alignment and stacking phases.
*   See the original GPL-3.0 license terms for details.
