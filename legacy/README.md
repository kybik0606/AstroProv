# Legacy Prototypes & Utilities

This folder contains early code and utility scripts from the development
of AstroProv.

**These files are deprecated and are NOT part of the active `astroprov`
Python package.** They are kept here for historical reference and to
document the evolution of the project. They are not included in the
`pip install` build.

## Files

*   **`pipeline_v0.py`**: The very first orchestrator script.
    Superseded by `auto_pipeline.py` / `full_auto_pipeline.py`
    (now also legacy) and finally by the unified `astroprov/cli/pipeline.py`.

*   **`auto_pipeline.py`**: Semi-automatic pipeline. Calibration and
    alignment in Siril CLI, then a pause for manual frame selection in
    the Siril GUI, then stacking and signing. Replaced by
    `astroprov/cli/pipeline.py --manual`.

*   **`full_auto_pipeline.py`**: Fully automatic pipeline. Calibration,
    alignment, stacking and signing with no manual steps. Replaced by
    `astroprov/cli/pipeline.py` (default, without `--manual`).

*   **`target.py`**: An experimental heuristic for finding the final
    stacked FITS file based on header metadata and filename scoring.
    Replaced by a much simpler glob pattern (`*stacked.fit`) in the
    active pipelines.

*   **`compare_fits.py`**: A basic debugging utility to inspect and print
    the HDU structure and headers of FITS files.

*   **`seal_collection.py`**: Early CLI for sealing a FITS file with raw source data. 
    Replaced by `astroprov/cli/seal.py --raw <dir>` (unified with standalone sealing).

*Please use the modules inside the `astroprov/` folder for all current
operations.*