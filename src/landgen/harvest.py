# harvest.py
# this module processes harvest and grazing data for the landgen workflow
# the output is a complete harvest distribution
#    and includes some data associated with particular harvests

# run() function is the main entry point for this module, and will be called by process_single_year in land_type.py

import multiprocessing as mp
from pathlib import Path
from . import shared_data
from . import landgen_io
# import normalize_cell # not created yet
import pandas as pd
import numpy as np
import os
import traceback
import time

########## define helper functions for harvest run() here

##### harvest_process()

## arguments
# lc_data: land cover data structure that is passed between modules
# year: the year for which to process the land cover data
# source_data_path: base path to the source data
# landgen_grid_path: path from source_data_path and the filename of the landgen grid
# out_path: base path for the output data; this is needed to read in the previous year's harvest data

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

def _harvest_process_star(args):
    """Unpack tuple args for pool.imap_unordered (lambdas can't be pickled)."""
    t0 = time.time()
    result = harvest_process(*args)
    elapsed = time.time() - t0
    year, ll_limits, _ = args
    print(f"  chunk {ll_limits} year {year}: {elapsed:.1f}s", flush=True)
    return result

def harvest_process(year, ll_limits, cell_ids):
    """Compute regridded harvest/grazing for one spatial chunk.
    Data comes from worker globals (set once via initializer, not per task).
    Returns (row_indices, harvest_results, grazing_results) — no proxy access.
    """
    try:
        return _harvest_process_impl(
            year, _g_grazing_names,
            _g_com_config_dict, ll_limits, cell_ids,
            _g_global_mesh_df, _g_cellid_to_idx,
            _g_harvest_data, _g_grazing_data,
        )
    except Exception:
        print(f"ERROR in harvest_process chunk {ll_limits} year {year}:\n{traceback.format_exc()}", flush=True)
        raise

def _harvest_process_impl(year, grazing_names,
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
        / f"harvest_{year}_{min_lat:.0f}_{min_lon:.0f}"
    )

    # convert HEALPix cell_ids to positional row indices into lt_year_data arrays
    row_indices = np.array([cellid_to_idx[int(cid)] for cid in cell_ids], dtype=np.int64)

    # --- regrid harvest variables ---
    harvest_results = []
    for i, varname in enumerate(landgen_io.LUH2_HARVEST_VARS):
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
        grazing_results.append((i, regridded))

    return row_indices, harvest_results, grazing_results


########## run()

## called by land_type.process_single_year() for each year, and this is where the multiprocessing happens for the landcover module
## this sets up the pool and calls the harvest_process() function for each chunk of data

def run(lt_year_data, year, prev_year, harvest_path, harvest_name, grazing_path, grazing_names,
        com_config_dict, out_grid_data, manager, grid_manager):

    print(f"Processing harvest module with parameters:")
    # todo: print the parameters here

    # load the global HEALPix mesh parquet once here so worker processes
    # don't each re-read the 37 MB file; pass the DataFrame into each chunk tuple
    global_parquet_path = (
        Path(com_config_dict['source_data_path'])
        / Path(com_config_dict['landgen_grid_path']).parent
        / 'merged_land_cells.parquet'
    )
    global_mesh_df = pd.read_parquet(global_parquet_path)
    print(f"  Loaded HEALPix mesh: {len(global_mesh_df)} cells from {global_parquet_path}")

    # read source data once here — workers reuse via initializer globals
    print(f"  Reading LUH2 harvest data for year {year}...")
    harvest_data = landgen_io.read_luh2_harvest(year, harvest_path, harvest_name)
    print(f"  Reading HYDE grazing data for year {year}...")
    grazing_data = landgen_io.read_hyde_grazing(year, grazing_path, grazing_names)

    # build a mapping from HEALPix cellid -> positional row index in lt_year_data arrays
    # the row order matches the order of cells in global_mesh_df (sorted by cellid)
    sorted_cellids = np.sort(global_mesh_df['cellid'].values)
    cellid_to_idx = {int(cid): idx for idx, cid in enumerate(sorted_cellids)}

    # number of available cpu cores (set by SBATCH during job submission)
    omp_threads_str = os.environ.get('OMP_NUM_THREADS')

    if omp_threads_str is not None:
        try:
            omp_threads_int = int(omp_threads_str)
            print(f"OMP_NUM_THREADS is set to: {omp_threads_int}")
        except ValueError:
            print(f"OMP_NUM_THREADS is set to an invalid integer value: {omp_threads_str}; falling back to 32")
            omp_threads_int = 32
    else:
        print("OMP_NUM_THREADS environment variable is not set.")
        # Default to 4 — safe for login nodes and small test runs.
        # On a full SLURM allocation, always set OMP_NUM_THREADS via the job script.
        omp_threads_int = 4
        print(f"Using {omp_threads_int} workers (default; set OMP_NUM_THREADS to override).")

    # set up the pool and call the harvest_process() function for each chunk of data
    # chunks are defined by the lat-lon limits and corresponding landgen grid cell ids for the chunk;
    #    these are created in land_type.process_single_year() and passed to this run() function as lists?
    # there are more chunks than cpus; the pool will manage this for efficiency because chunks vary in size
    # the results will be stored directly in the lt_year_data shared structure

    # Build data_chunks: one tuple per spatial chunk.
    # Use 5x5 degree boxes (1440 chunks) instead of 10x10 (360 chunks) to reduce
    # per-chunk variance: tropical land chunks at 10° can take 500+s while polar
    # chunks take 3s, creating severe load imbalance at the end of the job.
    # Smaller chunks keep all workers busy until the very end.
    decomp_box_size_degrees = 5
    chunk_ll_limits = landgen_io.calc_ll_limits(decomp_box_size_degrees)

    data_chunks = []
    for ll in chunk_ll_limits:
        min_lat, max_lat, min_lon, max_lon = ll
        mask = (
            (global_mesh_df['lat'] >= min_lat) & (global_mesh_df['lat'] < max_lat) &
            (global_mesh_df['lon'] >= min_lon) & (global_mesh_df['lon'] < max_lon)
        )
        chunk_cell_ids = global_mesh_df.loc[mask, 'cellid'].values
        if len(chunk_cell_ids) == 0:
            continue  # skip ocean-only or empty chunks
        data_chunks.append((
            year, ll, chunk_cell_ids,
        ))

    # Sort largest chunks first (most cells = slowest) so they are dispatched
    # immediately and don't create a long tail at the end of the job.
    data_chunks.sort(key=lambda t: len(t[2]), reverse=True)

    n_chunks = len(data_chunks)
    print(f"  Submitting {n_chunks} harvest chunks to pool of {omp_threads_int} workers")

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
                _harvest_process_star, data_chunks):
            for i, regridded in harvest_results:
                lt_year_data.set_harvest_frac(row_indices, i, regridded)
            for i, regridded in grazing_results:
                lt_year_data.set_grazing_frac(row_indices, i, regridded)
            done_count += 1
            if done_count % 50 == 0 or done_count == n_chunks:
                print(f"  Harvest progress: {done_count}/{n_chunks} chunks done", flush=True)

    return
