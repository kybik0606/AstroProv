# Siril Preprocessing Scripts

This folder contains modified Siril scripts used by the AstroProv pipeline.

## Available Scripts

We provide four different preprocessing scripts to cover the most common astronomical imaging workflows:

1.  **`OSC_Preprocessing_AstroProv_WithoutFlat.ssf`** 
    *For Color (OSC) cameras with Darks, but without Flats.*
    Performs dark calibration, debayering, and alignment.
2.  **`OSC_Preprocessing_AstroProv_WithoutDBF.ssf`** 
    *For Color (OSC) cameras without any calibration frames (Lights only).*
    Performs debayering and alignment only.
3.  **`Mono_Preprocessing_AstroProv_WithoutFlat.ssf`** 
    *For Monochrome cameras with Darks, but without Flats.*
    Performs dark calibration and alignment.
4.  **`Mono_Preprocessing_AstroProv_WithoutDBF.ssf`** 
    *For Monochrome cameras without any calibration frames (Lights only).*
    Performs alignment only.

## Why were these scripts modified?

**Original Author:** Cyril Richard (C) 2020  
**Original Scripts:** `Preprocessing_WithoutFlat.ssf`, `Mono_Preprocessing_WithoutFlat.ssf`, etc. (v1.1)  
**License:** GPL-3.0-or-later

The original scripts were modified to remove the final `stack` command. Instead, these scripts perform calibration (`calibrate`) and alignment (`register`) only. 

The stacking process is orchestrated separately by the `astroprov` Python pipeline. This allows AstroProv to calculate cryptographic hashes and generate provenance passports for the exact frames selected by the user *before* the final stacking occurs.

## How AstroProv interacts with these scripts

AstroProv does not blindly trust the files present in the directory. The `full_auto_pipeline.py` module **parses the text of the `.ssf` script** to detect which calibration frames are actually being used (by looking for `-dark=`, `-bias=`, and `-flat=` flags in the `calibrate` command). 

This ensures that the final FITS passport only records the frames that genuinely contributed to the stack, preventing false metadata.

***
*This modified version is distributed under the same license as the original: GPL-3.0-or-later.*