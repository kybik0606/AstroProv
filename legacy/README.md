# Legacy Prototypes & Utilities

This folder contains early code and utility scripts from the development of AstroProv. 

**These files are deprecated and are NOT part of the active `astroprov` Python package.** They are kept here for historical reference and to document the evolution of the project. They are not included in the `pip install` build.

## Files

*   **`pipeline.py`**: The very first orchestrator script. It was replaced by the more advanced `auto_pipeline.py` (semi-automatic) and `full_auto_pipeline.py` (fully automatic) to integrate with Siril CLI.
*   **`target.py`**: An experimental heuristic for finding the final stacked FITS file based on header metadata and filename scoring. Replaced by a much simpler glob pattern (`*stacked.fit`) in the active pipelines.
*   **`compare_fits.py`**: A basic debugging utility to inspect and print the HDU structure and headers of FITS files.

*Please use the modules inside the `astroprov/` folder for all current operations.*