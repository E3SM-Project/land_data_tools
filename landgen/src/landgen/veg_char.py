# veg_char.py
# this module processes vegetation characteristics data for the landgen workflow

# run() function is the main entry point for this module, and will be called by process_single_year in land_type.py

import multiprocessing as mp
import logging
from pathlib import Path
from .shared_data import LtData
from . import landgen_io
from . import tools
import numpy as np
import os
import traceback
import time
import shutil
import tempfile

logger = logging.getLogger('landgen')

def _veg_char_process_star(args):
    """Module-level wrapper so imap_unordered can unpack the args tuple."""
    return veg_char_process(*args)

########## define some module-specific constants here

N_MONTH = 12

# Li et al. source only covers 2001-2020; requests outside this range are
# clamped to the nearest boundary year (LAI/SAI filenames embed the year).
SOURCE_YEAR_MIN = 2015 #2001
SOURCE_YEAR_MAX = 2016 #2020

def _clamp_source_year(year):
    """Clamp year to the [SOURCE_YEAR_MIN, SOURCE_YEAR_MAX] range covered by Li et al., logging a warning if clamped."""
    if year < SOURCE_YEAR_MIN:
        logger.warning(f"veg_char: requested year {year} is before source data range; using {SOURCE_YEAR_MIN}")
        return SOURCE_YEAR_MIN
    if year > SOURCE_YEAR_MAX:
        logger.warning(f"veg_char: requested year {year} is after source data range; using {SOURCE_YEAR_MAX}")
        return SOURCE_YEAR_MAX
    return year

########## define helper functions for veg_char run() here

##### veg_char_process()

## arguments
# year: the year for which to process the veg_char data
# prev_year: the previous year processed; canopy height (static) is only regridded when prev_year is None
# lai_path: directory (relative to source_data_path) holding the LAI yearly files
# lai_name: filename template with '{year}' (source NetCDF variable name is LAI)
# lai_var: source NetCDF variable name for LAI (usually LAI)
# sai_path: directory (relative to source_data_path) holding the SAI yearly files
# sai_name: filename template with '{year}' (source NetCDF variable name is SAI)
# sai_var: source NetCDF variable name for SAI (usually SAI)
# height_top_path: directory (relative to source_data_path) holding the static canopy height files
# height_top_name: filename (source NetCDF variable name is CANOPY_HEIGHT_TOP)
# height_top_var: source NetCDF variable name for top canopy height (usually CANOPY_HEIGHT_TOP)
# height_bot_path: directory (relative to source_data_path) holding the static canopy height files
# height_bot_name: filename (source NetCDF variable name is CANOPY_HEIGHT_BOT)
# height_bot_var: source NetCDF variable name for bottom canopy height (usually CANOPY_HEIGHT_BOT)
# com_config_dict: shared dictionary for common parameters for all modules
# out_grid_data: shared data structure for the landgen grid data
# ll_limits, row_indices: chunk spatial bounds and cell indices (see set_decomp_cell_idx_ll_limits)

## output

def veg_char_process(year, prev_year, lai_path, lai_name, lai_var, sai_path, sai_name, sai_var,
                     height_top_path, height_top_name, height_top_var,
                     height_bot_path, height_bot_name, height_bot_var,
                     com_config_dict, out_grid_data, ll_limits, row_indices):
    """Compute regridded LAI/SAI (and canopy height, first year only) for one spatial chunk.
    Each worker reads its own source data (simple starmap approach like management.py).
    Returns chunk LtData object with cell_idx, monthly_lai, monthly_sai, and
    (first year only) monthly_height_top/monthly_height_bot populated.
    """
    t0 = time.time()
    try:
        return _veg_char_process_impl(
            year, prev_year, lai_path, lai_name, lai_var, sai_path, sai_name, sai_var,
            height_top_path, height_top_name, height_top_var,
            height_bot_path, height_bot_name, height_bot_var,
            com_config_dict, ll_limits, row_indices, out_grid_data
        )
    except Exception:
        print(f"ERROR in veg_char_process chunk {ll_limits} year {year}:\n{traceback.format_exc()}", flush=True)
        raise
    finally:
        elapsed = time.time() - t0
        print(f"  chunk {ll_limits} year {year}: {elapsed:.1f}s", flush=True)

def _veg_char_process_impl(year, prev_year, lai_path, lai_name, lai_var, sai_path, sai_name, sai_var,
                           height_top_path, height_top_name, height_top_var,
                           height_bot_path, height_bot_name, height_bot_var,
                           com_config_dict, ll_limits, row_indices, out_grid_data):
    """Worker implementation: reads source data, regrids using modular workflow, returns chunk LtData.
    Each worker does its own I/O (simple starmap approach like management.py).
    Uses same workflow as management.py: write mesh once, then regrid each variable.
    """

    # each worker writes its temp files to a unique subdirectory to avoid collisions
    scratch_base = os.environ.get('SCRATCH') or os.environ.get('TMPDIR') or tempfile.gettempdir()
    tmp_dir = Path(tempfile.mkdtemp(dir=scratch_base, prefix=f'veg_char_{year}_'))

    os.chdir(tmp_dir)
    tools.redirect_uraster_logs(tmp_dir)

    source_data_path = Path(com_config_dict['source_data_path'])

    # LAI/SAI are one file per year (20 yearly files, 12 months each); the year is
    # baked into the filename rather than searched for in the time axis, so
    # read_netcdf_ll is called with year=None to return all 12 monthly slices.
    src_year = _clamp_source_year(year)

    n_chunk_cells = len(row_indices)

    chunk_lt_data = LtData()
    chunk_lt_data.cell_idx     = np.array(row_indices, dtype=np.int64)
    chunk_lt_data.monthly_lai  = np.zeros((n_chunk_cells, N_MONTH), dtype=np.float64)
    chunk_lt_data.monthly_sai  = np.zeros((n_chunk_cells, N_MONTH), dtype=np.float64)
    # monthly_height_top/monthly_height_bot are left as None (skipped by copy_from)
    # unless this is the first year processed (prev_year is None); lt_year_data
    # persists across years in land_type.py's loop, so the value set on the
    # first year carries forward without recomputation.

    try:
        mesh_file = tmp_dir / 'mesh.fgb'
        landgen_io.write_mesh_to_flatgeobuf(out_grid_data, mesh_file, row_indices)

        # --- regrid LAI, one month at a time ---
        src_file = source_data_path / lai_path / lai_name.format(year=src_year)
        src_data = landgen_io.read_netcdf_ll(None, src_file, [lai_var], ll_limits)
        for month in range(N_MONTH):
            src_tif = tmp_dir / f"{lai_var}_{month}.tif"
            landgen_io.write_latlon_to_geotiff(
                src_data[lai_var][month],
                src_data['lat'],
                src_data['lon'],
                ll_limits,
                src_tif
            )
            chunk_lt_data.monthly_lai[:, month] = landgen_io.regrid_to_mesh(
                mesh_file, {lai_var: src_tif},
                row_indices, out_grid_data,
                out_type='data'
            )

        # --- regrid SAI, one month at a time ---
        src_file = source_data_path / sai_path / sai_name.format(year=src_year)
        src_data = landgen_io.read_netcdf_ll(None, src_file, [sai_var], ll_limits)
        for month in range(N_MONTH):
            src_tif = tmp_dir / f"{sai_var}_{month}.tif"
            landgen_io.write_latlon_to_geotiff(
                src_data[sai_var][month],
                src_data['lat'],
                src_data['lon'],
                ll_limits,
                src_tif
            )
            chunk_lt_data.monthly_sai[:, month] = landgen_io.regrid_to_mesh(
                mesh_file, {sai_var: src_tif},
                row_indices, out_grid_data,
                out_type='data'
            )

        # --- canopy height top/bottom: static fields, regrid only on the first year processed ---
        if prev_year is None:
            chunk_lt_data.monthly_height_top = np.zeros((n_chunk_cells), dtype=np.float64)
            chunk_lt_data.monthly_height_bot = np.zeros((n_chunk_cells), dtype=np.float64)


            src_file = source_data_path / height_top_path / height_top_name.format(year=src_year)
            src_data = landgen_io.read_netcdf_ll(None, src_file, [height_top_var], ll_limits)

            src_tif = tmp_dir / f"{height_top_var}.tif"
            landgen_io.write_latlon_to_geotiff(
                src_data[height_top_var],
                src_data['lat'],
                src_data['lon'],
                ll_limits,
                src_tif
            )
            chunk_lt_data.monthly_height_top[:] = landgen_io.regrid_to_mesh(
                mesh_file, {height_top_var: src_tif},
                row_indices, out_grid_data,
                out_type='data'
            )

            src_file = source_data_path / height_bot_path / height_bot_name.format(year=src_year)
            src_data = landgen_io.read_netcdf_ll(None, src_file, [height_bot_var], ll_limits)

            src_tif = tmp_dir / f"{height_bot_var}.tif"
            landgen_io.write_latlon_to_geotiff(
                src_data[height_bot_var],
                src_data['lat'],
                src_data['lon'],
                ll_limits,
                src_tif
            )
            chunk_lt_data.monthly_height_bot[:] = landgen_io.regrid_to_mesh(
                mesh_file, {height_bot_var: src_tif},
                row_indices, out_grid_data,
                out_type='data'
            )

        return chunk_lt_data

    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


########## run()

## called by land_type.process_single_year() for each year, and this is where the multiprocessing happens for the veg_char module
## this sets up the pool and calls the veg_char_process() function for each chunk of data

def run(lt_year_data, year, prev_year, lai_path, lai_name, lai_var, sai_path, sai_name, sai_var,
                            height_top_path, height_top_name, height_top_var,
                            height_bot_path, height_bot_name, height_bot_var,
        com_config_dict, out_grid_data, decomp_box_size_degrees=10):

    print(f"Processing veg_char module with parameters:")

    in_slurm = os.environ.get('SLURM_JOB_ID') is not None

    omp_threads_int = (
        tools.parse_cpu_env('SRUN_CPUS_PER_TASK') or
        tools.parse_cpu_env('SLURM_CPUS_PER_TASK') or
        tools.parse_cpu_env('SLURM_CPUS_ON_NODE') or
        mp.cpu_count()
    )

    if in_slurm:
        logger.info(f"Running under SLURM (job {os.environ['SLURM_JOB_ID']}): using {omp_threads_int} workers "
                    f"(SRUN_CPUS_PER_TASK={os.environ.get('SRUN_CPUS_PER_TASK')}, "
                    f"SLURM_CPUS_PER_TASK={os.environ.get('SLURM_CPUS_PER_TASK')}, "
                    f"SLURM_CPUS_ON_NODE={os.environ.get('SLURM_CPUS_ON_NODE')})")
    else:
        logger.info(f"Running locally: using {omp_threads_int} workers (mp.cpu_count())")

    decomp_indices = []
    decomp_ll_limits = []
    landgen_io.set_decomp_cell_idx_ll_limits(
        out_grid_data, decomp_indices, decomp_ll_limits,
        decomp_box_size_degrees, com_config_dict['out_path'])

    data_chunks = []
    for row_indices, ll in zip(decomp_indices, decomp_ll_limits):
        if len(row_indices) == 0:
            continue  # skip empty (ocean-only) chunks
        data_chunks.append((
            year, prev_year, lai_path, lai_name, lai_var, sai_path, sai_name, sai_var,
                            height_top_path, height_top_name, height_top_var,
                            height_bot_path, height_bot_name, height_bot_var,
            com_config_dict, out_grid_data, ll, row_indices
        ))

    # Sort largest chunks first (most cells = slowest) so they are dispatched
    # immediately and don't create a long tail at the end of the job.
    data_chunks.sort(key=lambda t: len(t[-1]), reverse=True)  # row_indices is the last element

    n_chunks = len(data_chunks)
    print(f"  Submitting {n_chunks} veg_char chunks to pool of {omp_threads_int} workers")

    # monthly_height_top/monthly_height_bot are only populated by workers on the
    # first year (prev_year is None); copy_from silently skips unset (None) attrs
    # for later years, leaving the values set on the first year in place.
    updated_vars = ['monthly_lai', 'monthly_sai', 'monthly_height_top', 'monthly_height_bot']
    with mp.Pool(processes=omp_threads_int) as pool:
        for chunk_lt_data in pool.imap_unordered(_veg_char_process_star, data_chunks):
            lt_year_data.copy_from(chunk_lt_data, updated_vars)
            del chunk_lt_data

    return
