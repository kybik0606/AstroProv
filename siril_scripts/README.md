# Siril Preprocessing Scripts

This folder contains modified Siril scripts used by the AstroProv pipeline.

## Preprocessing_astroprov.ssf

**Original Author:** Cyril Richard (C) 2020  
**Original Script:** `Preprocessing_WithoutFlat.ssf` (v1.1)  
**License:** GPL-3.0-or-later

**Modifications:**
The original script was modified to remove the final `stack` command. 
Instead, the script performs calibration (`calibrate`) and alignment (`register`) only. 
The stacking process is orchestrated separately by the `astroprov` Python pipeline after 
the user manually selects the best frames in the Siril GUI. 

This allows AstroProv to calculate cryptographic hashes and generate provenance 
passports for the exact frames selected by the user before the final stacking occurs.