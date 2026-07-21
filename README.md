# landgen

A Python package that preprocesses source land data to an unstructured grid for use by global models. Landgen has been developed to provide complete, self-consistent land data for `mksurfdata`, as part of the [E3SM Land Model (ELM)](https://e3sm.org) toolchain.

## Table of Contents

- [Version](#version)
- [Overview](#overview)
- [Requirements](#requirements)
- [Installation](#installation)
- [Configuration](#configuration)
- [Usage](#usage)
- [Outputs](#outputs)
- [Visualizing output data](#visualizing)
- [Contributing](#contributing)
- [Authors](#authors)
- [License](#license)

---

## Version

Current development version 0.1.0, testing with an ~10km HEALPix grid

---

## Overview

`landgen` processes raw source datasets (land type, soil, topography, human, atmosphere, etc.) and regrids them onto a target land grid (e.g., HEALPix ~1 km). Output NetCDF files are consumed by `mksurfdata` to generate ELM surface data files. Processing is parallelized via Python `multiprocessing` and is designed to run on HPC clusters with SLURM.

---

## Requirements

- Linux/Unix (HPC cluster recommended)
- [Conda](https://docs.conda.io/) (for environment management)
- Python 3.x (managed via the conda environment)

All Python dependencies are specified in the conda environment defined by `landgen_env.yml`.

---

## Installation

### 1. Set up the conda environment

Note that this needs to be done only upon first cloning the repository or after updating a local copy of the repository to ensure that all dependencies are included in the environment.

Run the `load_landgen_env.sh` script from the `landgen` directory. This creates (or updates) the `landgen_env` conda environment and installs `landgen` in editable mode:

```bash
./load_landgen_env.sh
```

Or step by step:

```bash
module load conda
conda env create -f landgen_env.yml   # first time
# conda env update -n landgen_env -f landgen_env.yml  # subsequent updates
conda activate landgen_env
pip install -e .
```

The step by step version activates the environment in the current shell, but the environment does not need to be activated in any interactive shell because `landgen` cannot be run directly from the command line due to its high resource usage. Running `landgen` must be done via the `sbatch` SLURM command (see [Usage](#usage) below). Furthermore, code development does not require the environment to be active, only that the installation of `landgen` in editable mode has been included in the environment (via `pip install -e .`). This is why the `load_landgen_env.sh` script is not sourced in the first example.

See `load_landgen_env.sh` for commands related to adding python packages to the environment and updating the environment and its associated `landgen_env.yml` definition file.

### 2. Verify installation

```bash
conda activate landgen_env
python -m landgen --help
```

---

## Configuration

Copy the provided input file template and edit it for your run:

```bash
cp config_template.json config.json
```

Key fields in `config.json`:

| Field | Description |
|---|---|
| `start_year` | First year to process |
| `end_year` | Last year to process (can be less than `start_year` to process backwards) |
| `source_data_path` | Full root path to directory holding the raw input datasets |
| `landgen_grid_path` | Path to the target land grid NetCDF file (including file name), relative to `source_data_path`. See [Grid definition](#grid) for more details. |
| `out_path` | Full path to directory for output files |
| `decomp_box_size_degrees` | Spatial decomposition box size in degrees |
| `modules` | List of processing modules to run (see [Modules](#modules) for details) |

### Grid definition



### Modules

| Module Name | Output file | Description |
|---|---|---|
| `topography` | `landgen_topography.nc` | Terrain elevation and related fields |
| `land_type` | `landgen_land_type.nc` | Land cover, crop, urban, lake, ice, wetland, management, vegetation characteristics |
| `soil` | `landgen_soil.nc` | Soil properties |
| `human` | `landgen_human.nc` | Human datasets (e.g., gdp, population) |
| `atmosphere` | `landgen_atmosphere.nc` | Atmospheric forcing-related land properties |

Each module in the `config.json` `modules` list has a unique set of input parameter fields, with a few common ones required for each module. The common required fields are:

| Required module input fields | Sub-fields | Description |
|---|---|---|
| `name` | none | Module name that matches the corresponding python file (e.g., entry "name": "topography" matches topography.py) |
| `params` | `active` <br> `out_fname` | Set `"active": true` to enable a module. <br> `out_fname` is the generic name of the output netcdf file associated with this module. If a module outputs multiple years, there will be a file for each year with the year appended before the `.nc` extension <br> Module-specific fields are defined as sub-fields within `params` and can be single values or sets of values (or nested sets).|

Additional module-specific input parameters (in `params`) are tabulated below for each module.

| `topography` `params` | Sub-fields | Description |
|---|---|---|
| `TBD` | `TBD` | Terrain elevation and related fields |

| `land_type` `params` | Sub-fields | Description |
|---|---|---|
| `sumbod_run` | `landcover` <br> `crop` <br> `urban` <br> `lake` <br> `ice` <br> `wetland` <br> `management` <br> `veg_char` | Set submodule name to `true` to enable it |
| `sumbod_dyn` | `landcover` <br> `crop` <br> `urban` <br> `lake` <br> `ice` <br> `wetland` <br> `management` <br> `veg_char` | Set submodule name to `true` to enable multi-year processing |
| `lc_rs_path` | none | Full path to directory holding source `landcover` data files |
| `lc_rs_name` | none | Name of source `landcover` data. Used to determine how to process the land cover data. Currently, `modis` is the only supported value and the files are downloaded as needed and not stored in `lc_rs_path` because they are so large. |
| `crop_path` | none | Full path to directory holding source `crop` data files |
| `urban_path` | none | Full path to directory holding source `urban` data files |
| `lake_path` | none | Full path to directory holding source `lake` data files |
| `ice_path` | none | Full path to directory holding source `ice` data files |
| `wetland_path` | none | Full path to directory holding source `wetland` data files |
| `harvest_path` | none | Full path to directory holding source `management` harvest data files |
| `harvest_name` | none | Name of harvest data file |
| `grazing_path` | none | Full path to directory holding source `management` grazing data files |
| `grazing_names` | `pasture` <br> `rangeland` | Names of grazing data files. |
| `veg_char_path` | none | Full path to directory holding source `veg_char` data files |



| `soil` `params` | Sub-fields | Description |
|---|---|---|
| `TBD` | `TBD` | Soil properties |

| `human` `params` | Sub-fields | Description |
|---|---|---|
| `TBD` | `TBD` | Human datasets (e.g., gdp, population) |


| `atmosphere` `params` | Sub-fields | Description |
|---|---|---|
| `TBD` | `TBD` | Atmospheric forcing-related land properties |


---

## Usage

`landgen` cannot be run locally in interactive shell mode due to its high resource usage.


### SLURM batch job (recommended for HPC)

First ensure that the environment and code are up to date:
```bash
./load_landgen.sh
```

Edit `submit_landgen.sh` as needed, then from within the same directory where `submit_landgen.sh` is located submit:

```bash
sbatch submit_landgen.sh
```

`submit_landgen.sh` assumes that it has been called from the directory in which it is located in order to find the default `config.json` input file. If either of these is not the case (calling location or default input file), then the user must modify the `SCRIPT_DIR` definition and/or the `INPUT_FILE` definition lines in `submit_landgen.sh`.

`submit_landgen.sh` uses a single node in exclusive mode and sets the number of worker processes to `SRUN_CPUS_PER_TASK` as defined in the script. Adjust `SRUN_CPUS_PER_TASK` in the script to change the number of cores used.

All [outputs](#outputs) are written to `out_path` as defined in the input file (see [Configuration](#configuration))

---

## Outputs

Each module has a specified netcdf output data file (see [Modules](#modules) for setting output file names). If a module output multiple years, each year will be written to its own file. The output files contain processed data on the grid specified by `landgen_grid_path`. These output data represent a self-consistent set of land variables for use by global models.

Will need to create the list of variables for each module (see the confluence page for the mksurfdata api). Maybe do this via agent after all the ouput data structurs are developed.

## Visualizing output data

`plot_landgen.py` is both a library for use within `landgen` and a standalone script that can be called independently to plot data from the module output netcdf files. First load the environement and landgen code into the working shell:

```bash
source load_landgen_env.sh
```

Then run plot_landgen:

```bash
python -m landgen.plot_landgen <file_path> <out_path> <year> [options]
```

| Argument | Description |
|---|---|
| `-h`, `--help` | Show help message and exit |
| `file_path` | Path to the input landgen NetCDF file (including file name) |
| `out_path` | Output directory for plot images |
| `year` | Calendar year to plot |
| `--varnames` | One or more variable names to plot; default plots all non-geometry variables |
| `--layers` | Layer selector: single integer, JSON list (e.g. `[0,1,2]`), or JSON dict by variable (e.g. `{"pct_pft":[0,1]}`); default plots all layers |
| `--plot-type` | `scatter` (colored points, default) or `rendered` (filled cell polygons) |
| `--file-type` | Output image format; only `png` is currently supported |
| `--colormap` | Matplotlib colormap name; default `viridis` |
| `--ll-limits` | Spatial subset as `MIN_LAT MAX_LAT MIN_LON MAX_LON`; default uses full extent |
| `--scale-limits` | Colorscale bounds as `VMIN VMAX`; default uses data min/max |

## Contributing

This package is developed as part of the E3SM project. 

Need to add specific contribution guide for landgen and mksurfdata. Also becauase this dev is separate from E3SM, the contribution guidelines do not necessarily apply.

---

## Authors

- Alan Di Vittorio (avdivittorio@lbl.gov)
- Eva Sinha (eva.sinha@pnnl.gov)

---

## License

Actually the e3sm license will not explicitly apply when we put this in a separate repo. So need to set up a new one.

See the E3SM [LICENSE](../../../../LICENSE) file. Need to point to or copy the license file.
