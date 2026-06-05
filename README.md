# landgen

A Python package that preprocesses source land data to a 1 km grid for use by `mksurfdata`, as part of the [E3SM Land Model (ELM)](https://e3sm.org) toolchain.

## Table of Contents

- [Version](#version)
- [Overview](#overview)
- [Requirements](#requirements)
- [Installation](#installation)
- [Configuration](#configuration)
- [Usage](#usage)
- [Modules](#modules)
- [Contributing](#contributing)
- [Authors](#authors)
- [License](#license)

---

## Version

Current development version 0.1.0, with an ~10km HEALPix grid

---

## Overview

`landgen` processes raw source datasets (land cover, soil, topography, human, atmosphere, etc.) and regrids them onto a target land grid (e.g., HEALPix 1 km). Output NetCDF files are consumed by `mksurfdata` to generate ELM surface data files. Processing is parallelized via Python `multiprocessing` and is designed to run on HPC clusters with SLURM.

---

## Requirements

- Linux (HPC cluster recommended)
- [Conda](https://docs.conda.io/) (for environment management)
- Python 3.x (managed via the conda environment)

All Python dependencies are specified in `landgen_env.yml`.

---

## Installation

### 1. Set up the conda environment

Run the `load_landgen_env.sh` script from the `landgen` directory. This creates (or updates) the `landgen_env` conda environment and installs `landgen` in editable mode, and activates the environment in the current shell:

```bash
source load_landgen_env.sh
```

Or step by step:

```bash
module load conda
conda env create -f landgen_env.yml   # first time
# conda env update -n landgen_env -f landgen_env.yml  # subsequent updates
conda activate landgen_env
pip install -e .
```

### 2. Verify installation

```bash
conda activate landgen_env
python -m landgen --help
```

---

## Configuration

Copy the provided template and edit it for your run:

```bash
cp config_template.json config.json
```

Key fields in `config.json`:

| Field | Description |
|---|---|
| `start_year` | First year to process |
| `end_year` | Last year to process (can be less than `start_year` to process backwards) |
| `source_data_path` | Root path to raw input datasets |
| `landgen_grid_path` | Path to the target land grid NetCDF file (relative to `source_data_path`) |
| `out_path` | Directory for output files |
| `decomp_box_size_degrees` | Spatial decomposition box size in degrees |
| `modules` | List of processing modules to run (see [Modules](#modules)) |

---

## Usage

### Interactive/local

```bash
conda activate landgen_env
pip install -e .
python -m landgen config.json
```

### SLURM batch job (recommended for HPC)

Edit `submit_landgen.sh` as needed, then submit:

```bash
source load_landgen_env.sh
sbatch submit_landgen.sh
```

The SLURM script uses a single node in exclusive mode and automatically sets the number of worker processes to half the available cores. Adjust `SRUN_CPUS_PER_TASK` in the script to change core usage.

---

## Modules

Each module is toggled via the `modules` list in `config.json`. Set `"active": true` to enable a module.

| Module | Output file | Description |
|---|---|---|
| `topography` | `landgen_topography.nc` | Terrain elevation and related fields |
| `land_type` | `landgen_land_type.nc` | Land cover, crop, urban, lake, ice, wetland, harvest, vegetation characteristics |
| `soil` | `landgen_soil.nc` | Soil properties |
| `human` | `landgen_human.nc` | Human datasets (e.g., gdp, population) |
| `atmosphere` | `landgen_atmosphere.nc` | Atmospheric forcing-related land properties |

---

## Contributing

This package is developed as part of E3SM. See the top-level [CONTRIBUTING.md](../../../../CONTRIBUTING.md) and the [E3SM development guide](https://e3sm.org/model/running-e3sm/developing-e3sm/) for contribution guidelines.

---

## Authors

- Alan Di Vittorio (avdivittorio@lbl.gov)
- Eva Sinha (eva.sinha@pnnl.gov)

---

## License

See the E3SM [LICENSE](../../../../LICENSE) file.
