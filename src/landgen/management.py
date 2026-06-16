# management.py
# this module processes harvest and grazing data for the landgen workflow

# run() function is the main entry point for this module, and will be called by process_single_year in land_type.py

import multiprocessing as mp
import logging
from pathlib import Path
from . import shared_data
from . import landgen_io
from . import tools
# import normalize_cell # not created yet
import pandas as pd
import numpy as np
import os
import traceback
import time

logger = logging.getLogger('landgen')

########## define some module-specific constants here

# Default harvest variable names from LUH2 transitions.nc
LUH2_HARVEST_VARS = [
    'primf_harv',   # wood harvest area from primary forest land
    'primn_harv',   # wood harvest area from primary non forest land
    'secmf_harv',   # wood harvest area from secondary mature forest land
    'secyf_harv',   # wood harvest area from secondary young forest land
    'secnf_harv',   # wood harvest area from secondary non forest land
    'primf_bioh',   # wood harvest biomass carbon from primary forest land
    'primn_bioh',   # wood harvest biomass carbon from primary non forest land
    'secmf_bioh',   # wood harvest biomass carbon from secondary mature forest land
    'secyf_bioh',   # wood harvest biomass carbon from secondary young forest land
    'secnf_bioh',   # wood harvest biomass carbon from secondary non forest land
]

########## define helper functions for management run() here

##### management_process()

## arguments
# lc_data: land cover data structure that is passed between modules
# year: the year for which to process the land cover data
# source_data_path: base path to the source data
# landgen_grid_path: path from source_data_path and the filename of the landgen grid
# out_path: base path for the output data; this is needed to read in the previous year's management data

## output

## module-level globals set once per worker process via pool initializer.
## Using fork + initializer means these large arrays are copy-on-write shared
## across all workers — no re-import overhead, no per-task pickling of large data.
## IMPORTANT: no manager proxy objects here — those cause deadlocks when forked.
_g_global_mesh_df  = None
_g_cellid_to_idx   = None
_g_com_config_dict = None
_g_harvest_data    = None
_g_grazing_data    = None
_g_grazing_names   = None

def _worker_init(global_mesh_df, cellid_to_idx, com_config_dict,
                 harvest_data, grazing_data, grazing_names):
    """Pool initializer: store read-only data as process-local globals.
    Called once per worker at pool startup (not once per task).
    All objects are plain Python/numpy — no manager proxies.
    """
    global _g_global_mesh_df, _g_cellid_to_idx, _g_com_config_dict
    global _g_harvest_data, _g_grazing_data, _g_grazing_names
    _g_global_mesh_df  = global_mesh_df
    _g_cellid_to_idx   = cellid_to_idx
    _g_com_config_dict = com_config_dict
    _g_harvest_data    = harvest_data
    _g_grazing_data    = grazing_data
    _g_grazing_names   = grazing_names

def _management_process_star(args):
    """Unpack tuple args for pool.imap_unordered (lambdas can't be pickled)."""
    t0 = time.time()
    result = management_process(*args)
    elapsed = time.time() - t0
    year, ll_limits, _ = args
    print(f"  chunk {ll_limits} year {year}: {elapsed:.1f}s", flush=True)
    return result

def management_process(year, ll_limits, cell_ids):
    """Compute regridded harvest/grazing for one spatial chunk.
    Data comes from worker globals (set once via initializer, not per task).
    Returns (row_indices, harvest_results, grazing_results) — no proxy access.
    """
    try:
        return _management_process_impl(
            year, _g_grazing_names,
            _g_com_config_dict, ll_limits, cell_ids,
            _g_global_mesh_df, _g_cellid_to_idx,
            _g_harvest_data, _g_grazing_data,
        )
    except Exception:
        print(f"ERROR in management_process chunk {ll_limits} year {year}:\n{traceback.format_exc()}", flush=True)
        raise

def _management_process_impl(year, grazing_names,
                    com_config_dict, ll_limits, cell_ids,
                    global_mesh_df, cellid_to_idx,
                    harvest_data, grazing_data):
    """Pure-compute worker: reads source data, regrids, and returns arrays.
    No manager proxies, no locks — all writes happen in the main process.
    """

    # each worker writes its temp files to a unique subdirectory to avoid collisions
    min_lat, max_lat, min_lon, max_lon = ll_limits
    tmp_dir = (
        Path(com_config_dict['out_path'])
        / 'tmp'
        / f"management_{year}_{min_lat:.0f}_{min_lon:.0f}"
    )

    # convert HEALPix cell_ids to positional row indices into lt_year_data arrays
    row_indices = np.array([cellid_to_idx[int(cid)] for cid in cell_ids], dtype=np.int64)

    # --- regrid harvest variables ---
    harvest_results = []

    # --- regrid and store harvest variables into lt_year_data.harvest_frac ---
    # LUH2_HARVEST_VARS order matches the n_harvest=10 dimension in LtData:
    #   index 0: primf_harv, 1: primn_harv, 2: secmf_harv, 3: secyf_harv, 4: secnf_harv
    # 5: primf_bioh, 6: primn_bioh, 7: secmf_bioh, 8: secyf_bioh, 9: secnf_bioh
    #  Note that the biomass carbon variables (primf_bioh, etc) are not currently used in mksurfdat, but we regrid them here for completeness and potential future use.
    for i, varname in enumerate(LUH2_HARVEST_VARS):
        regridded = landgen_io.regrid_to_landgen_grid(
            harvest_data[varname],
            harvest_data['lat'],
            harvest_data['lon'],
            cell_ids, ll_limits,
            global_mesh_df,
            tmp_dir / varname,
            varname,
        )
        harvest_results.append((i, regridded))

    # --- regrid grazing variables ---
    # HYDE3.5 data is in km² per source grid cell. After area-weighted regridding
    # to HEALPix the result is still in km². Divide by the (constant) HEALPix cell
    # area to convert to a dimensionless fraction (0-1).
    # Calculate cell area once (constant for all HEALPix cells)
    cell_area_km2 = landgen_io.get_cell_area_km2(global_mesh_df)

    grazing_results = []
    for i, category in enumerate(grazing_names.keys()):
        regridded = landgen_io.regrid_to_landgen_grid(
            grazing_data[category],
            grazing_data['lat'],
            grazing_data['lon'],
            cell_ids, ll_limits,
            global_mesh_df,
            tmp_dir / category,
            category,
        )
        regridded = regridded / cell_area_km2  # km² → fraction
        np.clip(regridded, 0.0, 1.0, out=regridded)    # clamp rounding artefacts
        grazing_results.append((i, regridded))

    return row_indices, harvest_results, grazing_results


########## run()

## called by land_type.process_single_year() for each year, and this is where the multiprocessing happens for the landcover module
## this sets up the pool and calls the management_process() function for each chunk of data

def run(lt_year_data, year, prev_year, harvest_path, harvest_name, grazing_path, grazing_names,
        com_config_dict, out_grid_data, decomp_indices, decomp_ll_limits, manager):

    print(f"Processing management module with parameters:")
    # todo: print the parameters here

    # load the global HEALPix mesh parquet once here so worker processes
    # don't each re-read the 37 MB file; pass the DataFrame into each chunk tuple
    global_mesh_df = landgen_io.load_global_mesh_parquet(com_config_dict)

    # read source data once here — workers reuse via initializer globals
    print(f"  Reading LUH2 harvest data for year {year}...")
    harvest_data = landgen_io.read_luh2_harvest(year, harvest_path, harvest_name)
    print(f"  Reading HYDE grazing data for year {year}...")
    grazing_data = landgen_io.read_hyde_grazing(year, grazing_path, grazing_names)

    # build a mapping from HEALPix cellid -> positional row index in lt_year_data arrays
    cellid_to_idx = landgen_io.build_cellid_to_idx_map(global_mesh_df)

    # Determine the number of worker processes to use.
    # Priority: SRUN_CPUS_PER_TASK -> SLURM_CPUS_PER_TASK -> SLURM_CPUS_ON_NODE -> mp.cpu_count()
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

    # set up the pool and call the management_process() function for each chunk of data
    # chunks are defined by the lat-lon limits and corresponding landgen grid cell ids for the chunk;
    #    these are created in land_type.process_single_year() and passed to this run() function as lists?
    # there are more chunks than cpus; the pool will manage this for efficiency because chunks vary in size
    # the results will be stored directly in the lt_year_data shared structure

    # get the manager locks for the shared data structures
    # using data-specific locks, watch out for deadlocks.  
    man_lock  = manager.Lock()
    #grid_lock = grid_manager.lock()


    # Build data_chunks from the pre-computed decomp_indices / decomp_ll_limits
    # passed in from landgen.py via land_type.py.  These were produced by
    # set_decomp_cell_idx_ll_limits(), which computes tight vertex bounding boxes
    # per chunk — exactly the ll_limits that regrid_to_landgen_grid needs so the
    # raster slice fully covers every polygon in the chunk.
    data_chunks = []
    for cell_ids, ll in zip(decomp_indices, decomp_ll_limits):
        if len(cell_ids) == 0:
            continue  # skip empty (ocean-only) chunks
        data_chunks.append((year, ll, cell_ids))

    # Sort largest chunks first (most cells = slowest) so they are dispatched
    # immediately and don't create a long tail at the end of the job.
    data_chunks.sort(key=lambda t: len(t[2]), reverse=True)

    n_chunks = len(data_chunks)
    print(f"  Submitting {n_chunks} management chunks to pool of {omp_threads_int} workers")

    # Use fork-based Pool with an initializer that sets worker globals once at startup.
    # - fork: workers inherit parent memory (numpy arrays are copy-on-write) — no
    #         per-task pickling of large data, no expensive Python re-import overhead.
    # - initializer: sets plain numpy/dict globals BEFORE any task runs, so no
    #         manager proxy objects are ever present in the forked workers.
    # - Workers return results; the main process writes to lt_year_data — no proxy
    #         connections from worker processes, so no manager deadlock.
    fork_ctx = mp.get_context('fork')
    with fork_ctx.Pool(
            processes=omp_threads_int,
            initializer=_worker_init,
            initargs=(global_mesh_df, cellid_to_idx, com_config_dict,
                      harvest_data, grazing_data, grazing_names)) as pool:
        done_count = 0
        for row_indices, harvest_results, grazing_results in pool.imap_unordered(
                _management_process_star, data_chunks):
            for i, regridded in harvest_results:
                lt_year_data.set_harvest_frac(row_indices, i, regridded)
            for i, regridded in grazing_results:
                lt_year_data.set_grazing_frac(row_indices, i, regridded)
            done_count += 1
            if done_count % 50 == 0 or done_count == n_chunks:
                print(f"  Managementprogress: {done_count}/{n_chunks} chunks done", flush=True)

    return
