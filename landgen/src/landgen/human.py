# human.py
# this module processes human data for the landgen workflow
# the output is the complete set of human data needed for landgen and elm
# population denstiy data is a timeseries
# gdp data are static

# run() function is the main entry point for this module, and will be called by landgen.py

import multiprocessing as mp
import logging
from pathlib import Path
from .shared_data import HumanData
from . import landgen_io
from . import tools
import numpy as np
import os
import traceback
import time
import shutil
import tempfile

logger = logging.getLogger('landgen')

def _human_process_star(args):
    """Module-level wrapper so imap_unordered can unpack the args tuple."""
    return human_process(*args)


########## define helper functions for human run() here

##### human_process()

## arguments
# year: the year for which to process the human data
# pop_dens_path: path from source_data_path to the population density file
# pop_dens_name: population density filename
# pop_dens_var: population density variable name in the file
# com_config_dict: the shared dictionary for the common parameters for all modules
# out_grid_data: the shared data structure for the landgen grid data
# ll_limits: (min_lat, max_lat, min_lon, max_lon) tight vertex bounding box of this chunk
# row_indices: 0-based row indices into out_grid_data arrays for the cells in this chunk

## output
# chunk HumanData object with cell_idx and pop_dens populated

def human_process(year, pop_dens_path, pop_dens_name, pop_dens_var,
                  com_config_dict, out_grid_data, ll_limits, row_indices):
    """Compute regridded population density for one spatial chunk.
    Each worker reads its own source data (simple starmap approach like landcover.py).
    Returns chunk HumanData object with cell_idx and pop_dens populated.
    """
    t0 = time.time()
    try:
        return _human_process_impl(
            year, pop_dens_path, pop_dens_name, pop_dens_var,
            com_config_dict, ll_limits, row_indices, out_grid_data
        )
    except Exception:
        print(f"ERROR in human_process chunk {ll_limits} year {year}:\n{traceback.format_exc()}", flush=True)
        raise
    finally:
        elapsed = time.time() - t0
        print(f"  chunk {ll_limits} year {year}: {elapsed:.1f}s", flush=True)

def _human_process_impl(year, pop_dens_path, pop_dens_name, pop_dens_var,
                        com_config_dict, ll_limits, row_indices, out_grid_data):
    """Worker implementation: reads source data, regrids using modular workflow, returns chunk HumanData.
    Uses same workflow as landcover.py: write mesh once, then regrid each variable.
    """

    # each worker writes its temp files to a unique subdirectory to avoid collisions
    scratch_base = os.environ.get('SCRATCH') or os.environ.get('TMPDIR') or tempfile.gettempdir()
    tmp_dir = Path(tempfile.mkdtemp(dir=scratch_base, prefix=f'human_{year}_'))

    # Change CWD to the worker-local tmp_dir so that uraster's internally-generated
    # files (intermediate data files, logs) land here rather than in the shared job CWD,
    # preventing collisions between parallel workers.
    os.chdir(tmp_dir)

    # Redirect uraster's auto-created log files into the per-worker tmp_dir
    tools.redirect_uraster_logs(tmp_dir)

    # Read source data for this year and chunk (each worker reads its own copy)
    # HYDE3.5 _FillValue (-9999) cells (ocean/no data) are decoded to NaN by xarray
    source_data_path = Path(com_config_dict['source_data_path'])
    pop_dens_data = landgen_io.read_netcdf_ll(year, source_data_path / pop_dens_path / pop_dens_name,
                                              [pop_dens_var], ll_limits)

    # Create chunk-sized HumanData object
    n_chunk_cells = len(row_indices)
    chunk_human_data = HumanData()
    chunk_human_data.cell_idx = np.array(row_indices, dtype=np.int64)
    chunk_human_data.pop_dens = np.zeros(n_chunk_cells, dtype=np.float64)

    try:
        # Write mesh once per chunk (same approach as landcover.py)
        mesh_file = tmp_dir / 'mesh.fgb'
        landgen_io.write_mesh_to_flatgeobuf(out_grid_data, mesh_file, row_indices)

        # --- regrid population density ---
        # Population density is already per km², so the uraster mean needs no area conversion.
        # NaN (ocean) pixels are excluded, giving persons per km² of land.
        src_tif = tmp_dir / f"{pop_dens_var}.tif"
        landgen_io.write_latlon_to_geotiff(
            pop_dens_data[pop_dens_var],
            pop_dens_data['lat'],
            pop_dens_data['lon'],
            ll_limits,
            src_tif
        )
        print(f"  [PID {os.getpid()}] regrid_to_mesh START: human var={pop_dens_var} ll_limits={ll_limits}", flush=True)
        regridded = landgen_io.regrid_to_mesh(
            mesh_file, {pop_dens_var: src_tif},
            row_indices, out_grid_data,
            out_type='data'
        )
        print(f"  [PID {os.getpid()}] regrid_to_mesh DONE:  human var={pop_dens_var} ll_limits={ll_limits}", flush=True)
        # cells with no valid source pixels come back from uraster as NaN; set to 0
        chunk_human_data.pop_dens[:] = np.nan_to_num(regridded, nan=0.0)

        return chunk_human_data

    finally:
        # Clean up temp files
        shutil.rmtree(tmp_dir, ignore_errors=True)


########## run()

## arguments
## these first ones are module-specific parameters that are set in the config file
# active: true = module is run, false = module is skipped
# out_fname: output filename for the module; the year is inserted before the extension
# sources: dictionary of source data (path, name, variable) for each human variable
#    currently only 'pop_dens' (HYDE3.5 population density)
# decomp_box_size_degrees: the size of the decomposition box in degrees for parallel processing
## these are general parameters for all modules that are set in the config file
# com_config_dict: the shared dictionary for the common parameters for all modules
#    (start_year, end_year, source_data_path, landgen_grid_path, out_path)
# out_grid_data: the shared data structure for the landgen grid data

## output
# one netcdf file per year in out_path, e.g. landgen_human_2010.nc

def run(active, out_fname, sources=None, decomp_box_size_degrees=10, com_config_dict=None, out_grid_data=None):
    if active is False:
        logger.info("Skipping human module")
        return

    # set up the human module shared data structure
    # this holds only one year of data, so write it each year
    human_year_data = HumanData()
    n_cells = out_grid_data.num_cells
    print(f"  Allocating HumanData for {n_cells} land cells")
    human_year_data.allocate(n_cells=n_cells)
    
    # extract common parameters from shared config dict
    start_year        = com_config_dict['start_year']
    end_year          = com_config_dict['end_year']
    out_path          = com_config_dict['out_path']

    # extract population density source
    if not sources or 'pop_dens' not in sources:
        raise KeyError("human.run: 'sources' with a 'pop_dens' entry must be set in the human module params in the config file")
    pop_dens_src  = sources['pop_dens']
    pop_dens_path = pop_dens_src.get('path', '')
    pop_dens_name = pop_dens_src.get('name', '')
    pop_dens_var  = pop_dens_src.get('variable', '')

    logger.info("Processing human module")
    logger.info(f"  pop_dens source: {Path(pop_dens_path) / pop_dens_name} (variable {pop_dens_var})")
    logger.info(f"  years: {start_year}-{end_year}, decomp_box_size_degrees: {decomp_box_size_degrees}")

    # Determine the number of worker processes to use.
    # Priority: SRUN_CPUS_PER_TASK -> SLURM_CPUS_PER_TASK -> SLURM_CPUS_ON_NODE -> mp.cpu_count()
    omp_threads_int = (
        tools.parse_cpu_env('SRUN_CPUS_PER_TASK') or
        tools.parse_cpu_env('SLURM_CPUS_PER_TASK') or
        tools.parse_cpu_env('SLURM_CPUS_ON_NODE') or
        mp.cpu_count()
    )
    logger.info(f"human: using {omp_threads_int} workers")

    # compute the decomp once; it does not change between years
    decomp_indices = []
    decomp_ll_limits = []
    landgen_io.set_decomp_cell_idx_ll_limits(
        out_grid_data, decomp_indices, decomp_ll_limits,
        decomp_box_size_degrees, out_path)

    years = np.arange(start_year, end_year + 1)
    updated_vars = ['pop_dens']

    with mp.Pool(processes=omp_threads_int) as pool:
        for year in years:
            logger.info(f"human: processing year {year}")
            human_year_data.pop_dens[:] = 0.0

            data_chunks = []
            for row_indices, ll in zip(decomp_indices, decomp_ll_limits):
                if len(row_indices) == 0:
                    continue  # skip empty (ocean-only) chunks
                data_chunks.append((
                    int(year), pop_dens_path, pop_dens_name, pop_dens_var,
                    com_config_dict, out_grid_data, ll, row_indices
                ))

            # Sort largest chunks first (most cells = slowest) so they are dispatched
            # immediately and don't create a long tail at the end of the job.
            data_chunks.sort(key=lambda t: len(t[7]), reverse=True)  # row_indices is at index 7

            print(f"  Submitting {len(data_chunks)} human chunks for year {year} to pool of {omp_threads_int} workers")
            for chunk_human_data in pool.imap_unordered(_human_process_star, data_chunks):
                human_year_data.copy_from(chunk_human_data, updated_vars)
                del chunk_human_data

            # write output for this year, with the year inserted before the extension
            out_fname_p = Path(out_fname)
            out_fname_year = f"{out_fname_p.stem}_{year}{out_fname_p.suffix}"
            landgen_io.write_module_netcdf(out_grid_data, human_year_data, out_path, out_fname_year,
                            year=int(year), timevars=updated_vars, varnames=updated_vars, ll_limits=None)

    # free the module-specific shared data structure
    human_year_data = None
    return
