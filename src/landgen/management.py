# management.py
# this module processes harvest and grazing data for the landgen workflow

# run() function is the main entry point for this module, and will be called by process_single_year in land_type.py

import multiprocessing as mp
import logging
from pathlib import Path
from .shared_data import LtData
from . import landgen_io
from . import tools
# import normalize_cell # not created yet
import numpy as np
import os
import traceback
import time
import shutil

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

def management_process(year, harvest_path, harvest_name, grazing_path, grazing_names,
                      com_config_dict, out_grid_data, ll_limits, row_indices):
    """Compute regridded harvest/grazing for one spatial chunk.
    Each worker reads its own source data (simple starmap approach like landcover.py).
    Returns chunk LtData object with cell_idx, harvest_frac, and grazing_frac populated.
    """
    t0 = time.time()
    try:
        return _management_process_impl(
            year, harvest_path, harvest_name, grazing_path, grazing_names,
            com_config_dict, ll_limits, row_indices, out_grid_data
        )
    except Exception:
        print(f"ERROR in management_process chunk {ll_limits} year {year}:\n{traceback.format_exc()}", flush=True)
        raise
    finally:
        elapsed = time.time() - t0
        print(f"  chunk {ll_limits} year {year}: {elapsed:.1f}s", flush=True)

def _management_process_impl(year, harvest_path, harvest_name, grazing_path, grazing_names,
                             com_config_dict, ll_limits, row_indices, out_grid_data):
    """Worker implementation: reads source data, regrids using modular workflow, returns chunk LtData.
    Each worker does its own I/O (simple starmap approach like landcover.py).
    Uses same workflow as landcover.py: write mesh once, then regrid each variable.
    """

    # each worker writes its temp files to a unique subdirectory to avoid collisions
    min_lat, max_lat, min_lon, max_lon = ll_limits
    tmp_dir = (
        Path(com_config_dict['out_path'])
        / 'tmp'
        / f"management_{year}_{min_lat:.0f}_{min_lon:.0f}"
    )
    tmp_dir.mkdir(parents=True, exist_ok=True)

    # Read source data (each worker reads its own copy)
    harvest_data = landgen_io.read_netcdf_ll(year, Path(harvest_path) / harvest_name, LUH2_HARVEST_VARS, ll_limits)
    grazing_data = {}
    for stem, grazing_name in grazing_names.items():
        grazing_data[stem] = landgen_io.read_netcdf_ll(year, Path(grazing_path) / grazing_name, [stem], ll_limits)

    # Create chunk-sized LtData object
    n_chunk_cells = len(row_indices)
    n_harvest = len(LUH2_HARVEST_VARS)
    n_grazing = len(grazing_names)
    
    chunk_lt_data = LtData()
    chunk_lt_data.allocate(n_cells=n_chunk_cells, n_harvest=n_harvest, n_grazing=n_grazing)
    chunk_lt_data.cell_idx = row_indices  # Map chunk positions to global indices

    try:
        # Write mesh once per chunk (same approach as landcover.py)
        mesh_file = tmp_dir / 'mesh.geojson'
        landgen_io.write_mesh_to_geojson(out_grid_data, mesh_file, row_indices)

        # --- regrid harvest variables ---
        # LUH2_HARVEST_VARS order matches the n_harvest=10 dimension in LtData:
        #   index 0: primf_harv, 1: primn_harv, 2: secmf_harv, 3: secyf_harv, 4: secnf_harv
        # 5: primf_bioh, 6: primn_bioh, 7: secmf_bioh, 8: secyf_bioh, 9: secnf_bioh
        #  Note that the biomass carbon variables (primf_bioh, etc) are not currently used in mksurfdat, but we regrid them here for completeness and potential future use.
        for i, varname in enumerate(LUH2_HARVEST_VARS):
            # Write source data to GeoTIFF
            src_tif = tmp_dir / f"{varname}.tif"
            landgen_io.write_latlon_to_geotiff(
                harvest_data[varname],
                harvest_data['lat'],
                harvest_data['lon'],
                ll_limits,
                src_tif
            )
            # Regrid using modular function
            regridded = landgen_io.regrid_to_mesh(
                mesh_file, {varname: src_tif},
                row_indices, out_grid_data,
                out_type='data'
            )
            chunk_lt_data.harvest_frac[:, i] = regridded

        # --- regrid grazing variables ---
        # HYDE3.5 data is in km² per source grid cell. After area-weighted regridding
        # to HEALPix the result is still in km². Divide by the (constant) HEALPix cell
        # area to convert to a dimensionless fraction (0-1).
        # Extract from out_grid_data (more direct than via DataFrame)
        cell_area_km2 = out_grid_data.cell_area[row_indices[0]] / 1_000_000  # m² → km²

        # Use stems as keys (e.g., 'pasture' not 'pasture.nc') to match grazing_data dict
        grazing_stems = [Path(name).stem for name in grazing_names.keys()]
        for i, stem in enumerate(grazing_stems):
            # Write source data to GeoTIFF
            src_tif = tmp_dir / f"{stem}.tif"
            landgen_io.write_latlon_to_geotiff(
                grazing_data[stem][stem],
                grazing_data[stem]['lat'],
                grazing_data[stem]['lon'],
                ll_limits,
                src_tif
            )
            # Regrid using modular function
            regridded = landgen_io.regrid_to_mesh(
                mesh_file, {stem: src_tif},
                row_indices, out_grid_data,
                out_type='data'
            )
            regridded = regridded / cell_area_km2  # km² → fraction
            np.clip(regridded, 0.0, 1.0, out=regridded)    # clamp rounding artefacts
            chunk_lt_data.grazing_frac[:, i] = regridded

        return chunk_lt_data

    finally:
        # Clean up temp files
        shutil.rmtree(tmp_dir, ignore_errors=True)


########## run()

## called by land_type.process_single_year() for each year, and this is where the multiprocessing happens for the landcover module
## this sets up the pool and calls the management_process() function for each chunk of data

def run(lt_year_data, year, prev_year, harvest_path, harvest_name, grazing_path, grazing_names,
        com_config_dict, out_grid_data, decomp_indices, decomp_ll_limits):

    print(f"Processing management module with parameters:")
    # todo: print the parameters here

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

    # Build data_chunks from the pre-computed decomp_indices / decomp_ll_limits
    # passed in from landgen.py via land_type.py.  These were produced by
    # set_decomp_cell_idx_ll_limits(), which computes tight vertex bounding boxes
    # per chunk — exactly the ll_limits that write_latlon_to_geotiff needs so the
    # raster slice fully covers every polygon in the chunk.
    #
    # ASSUMPTION: decomp_indices contains 0-based row indices into out_grid_data arrays
    # (not arbitrary cell ID values). This requires that the mesh file's cellid values
    # are sequential (0, 1, 2, ..., n-1) matching their row positions. If cellid values
    # are non-sequential or non-contiguous after subsetting, array indexing operations
    # (e.g., out_grid_data.cell_id[idx], out_grid_data.lon_vtx[idx]) will produce
    # incorrect results or index-out-of-bounds errors.
    data_chunks = []
    for row_indices, ll in zip(decomp_indices, decomp_ll_limits):
        if len(row_indices) == 0:
            continue  # skip empty (ocean-only) chunks
        # Each tuple contains all args for management_process (starmap approach)
        data_chunks.append((
            year, harvest_path, harvest_name, grazing_path, grazing_names,
            com_config_dict, out_grid_data, ll, row_indices
        ))

    # Sort largest chunks first (most cells = slowest) so they are dispatched
    # immediately and don't create a long tail at the end of the job.
    data_chunks.sort(key=lambda t: len(t[8]), reverse=True)  # row_indices is at index 8

    n_chunks = len(data_chunks)
    print(f"  Submitting {n_chunks} management chunks to pool of {omp_threads_int} workers")

    # Use simple Pool.starmap() approach (like landcover.py).
    # Workers read their own data — simpler code, more portable.
    # Trade-off: more I/O per chunk vs simpler implementation.
    with mp.Pool(processes=omp_threads_int) as pool:
        chunk_list_results = pool.starmap(management_process, data_chunks)

    # Merge chunk results into global lt_year_data using copy_from
    for chunk_lt_data in chunk_list_results:
        lt_year_data.copy_from(chunk_lt_data, ['harvest_frac', 'grazing_frac'])

    return
