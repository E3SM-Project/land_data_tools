# landcover.py
# this module processes land cover data for the landgen workflow
# the output is a complete land cover distribution
#    and includes some data associated with particular land covers

# run() function is the main entry point for this module, and will be called by process_single_year in land_type.py

import multiprocessing as mp
import logging
from pathlib import Path
from . import shared_data
from . import landgen_io
import landcover_remote_sensing as lc_rs
import transitions # not created yet
import normalize_cell # not created yet
import os

logger = logging.getLogger('landgen')
resource_logger = logging.getLogger('ClusterMonitor')

########## define helper functions for landcover run() here

##### landcover_process()

## arguments
# lc_data: land cover data structure that is passed between modules
# year: the year for which to process the land cover data
# prev_year: the previous year for which land cover data were processed
# source_data_path: base path to the source data
# landgen_grid_path: path from source_data_path and the filename of the landgen grid
# lc_rs_path: path from source_data_path and the filename of the land cover remote sensing data (if _use_lc_rs is True)
# out_path: base path for the output data; this is needed to read in the previous year's land cover data (if _use_lc_rs is False)
# prev_out_fname: the output filename for the previous year's land cover data (if _use_lc_rs is False)

## output

def landcover_process(year, prev_year, prev_fname, lc_rs_path, lc_rs_name,
                            com_config_dict, out_grid_data, cell_indices, ll_limits,
                            man_lock, grid_lock):

    # todo: need to sort out printing from multiple processes
    #print(f"Processing landcover module year {year} with parameters:")
    # todo: print the parameters here?

    # Determine a scratch base directory for worker-local temporary files.
    # Priority: $SCRATCH (NERSC/HPC) -> $TMPDIR -> system default (usually /tmp).
    # Each worker gets its own subdirectory to avoid cross-worker file collisions.
    scratch_base = os.environ.get('SCRATCH') or os.environ.get('TMPDIR') or tempfile.gettempdir()
    tmp_dir = Path(tempfile.mkdtemp(dir=scratch_base, prefix=f'landcover_{year}_'))
    #print(f"  landcover_process: worker temp dir: {tmp_dir}")

    try:

        # first write the mesh file for this chunk
            # first write the mesh file for this chunk
        mesh_file = Path(tmp_dir) / 'mesh.geojson'
        landgen_io.write_mesh_to_geojson(out_grid_data, mesh_file, cell_indices)

        ## todo, if actually doing it this way, add a list of variables argument to the allocation function to allocate just a subset!!!
        # create a local data structure just for this chunk
        # set the cell indices in this chunk for later copying into the shared lt_year_data structure
        lt_chunk_data = shared_data.LtData()
        lt_chunk_data.allocate(n_cells=len(cell_indices))
        lt_chunk_data.cell_idx[:] = cell_indices

        # todo: probably need to add lai path to land_type params and pass it through to here

        lc_rs_data = None
        prev_lt_data = None
        climate_data = None
        lai_data = None
        elm_data = None

        # todo: these data below will be read in based on ll_limits

        if lc_rs.use_lc_rs(year, lc_rs_name):
            # read modis cover data - use tmp_dir because the data are downloaded (not stored)
            # reading both the igbp cover data and the veg continuous fields data
            lc_rs_geotiffs = lc_rs.read_to_geotiff(year, lc_rs_name, tmp_dir, cell_indices, ll_limits)
        else:
            # read and process previous year's landgen land type data and transitions to calculate this year's land cover distribution
            if prev_year is not None:
                # read previous year's landgen land type data
                prev_out_file = Path(com_config_dict['out_path']) / prev_fname
                if prev_out_file.exists():
                    #print(f"Reading previous year landgen land type data from {prev_out_file}")
                    # todo: define this in a helper function
                    prev_lt_data = read_prev_lt(prev_year, prev_out_file)
                else:
                    raise FileNotFoundError(
                        f"Previous year output file does not exist: {prev_out_file}")

                #Calculate this year's land cover distribution using the previous year's data and the transitions
                # these calculations are based on landgen land type outputs
                #temp_lt_data = transitions.run(prev_lt_data, year, prev_year)

                ## todo: is this necessary? maybe we should leave this as output classes; these are also on landgen grid
                ## can we check for appropriate types using the climate data below back to 1900, without converting to orignal lc classes?
                # convert this year's land cover distribution to the lc rs classes
                #lc_rs_data = lc_rs.convert_landgen_to_lc_rs(temp_lt_data, lc_rs_name)
            else:
                raise ValueError(f"Previous year is None for year {year}, and use_lc_rs is False; cannot process land cover data.")

## todo: deal with generic elm pfts from modis data first, at modis resolution,
# and then convert to landgen grid and split tree/grass/shrub based on climate and lai data
# this is because the modis data are finer than 1km res, so we can do more explicit processing
# the finer vcf data should be applied to the lc data to do this.

        # get climate data (1900-2020, four historical periods; and cmip 6 future scenarios, 1km) need to pick the correct period
        # if year < 1900, then use the 1900 climate data
        # todo: probably define this here becase these are specific data 
        #climate_data = read_climate_data(year, com_config_dict['source_data_path'], lai_path)

        # get lai data for splitting tree/grass/shrub; this is based on li et al 1km lai data
        #    these data do have short timeseries? then need to select appropriate year
        #todo: this can be in a utils module because other modules need to read these source data 
        #lai_data = read_lai_data(year, com_config_dict['source_data_path'], lai_path)

        #######
        # todo: use uraster to convert lc_rs_data and climate data and lai data to the landgen grid

        # regrid each lc_rs variable to the landgen mesh grid
        # lc_rs_data: dict {varname: 1D np.ndarray (n_cells,)}; stack into 2D array (n_vars, n_cells)
        lc_rs_data = {}
        for varname, tif_path in lc_rs_geotiffs.items():
            lc_rs_data[varname] = landgen_io.regrid_to_mesh(
                mesh_file, {varname: tif_path}, cell_indices, out_grid_data,
                out_type='data', remap_method=3
            )
        # 2D array: rows = variables (same order as lc_rs_geotiffs), cols = cells
        #import numpy as np
        #lc_rs_varnames = list(lc_rs_data.keys())
        #lc_rs_array = np.stack([lc_rs_data[v] for v in lc_rs_varnames], axis=0)  # (n_vars, n_cells)

        # convert lc_rs_data to the elm land types; this is igbp to generic elm land type mapping
        #    also use the veg continuous fields data; can set modis to elm mapping file name here and read it based on lc_rs_name
        #elm_data = lc_rs.convert_lc_rs_to_elm(lc_rs_data, lc_rs_name)

        # split tree/grass/shrub pfts based on cliamte data and li et al 1km lai data
        #elm_data = split_tree_grass_shrub(elm_data, climate_data, lai_data)

        # normalize cell by adjusting the land cover distribution to fill the cell land area and reconciling with ocean data (landfrac)
        #elm_data = normalize_cell.fill_land(elm_data, landfrac)       # fill_land
        #elm_data = normalize_cell.reconcile_ocean(elm_data, landfrac)  # reconcile_ocean

        # now put elm data into lt_year_data

        # put some lc_rs data into lt_chunk_data for testing
        # map lc_rs varnames -> lt_chunk_data fields
        # LC_Type1: IGBP land cover class per cell -> stored in pct_pft row 0 as a placeholder
        #           (full pft distribution is computed later in convert_lc_rs_to_elm)
        # LW: MODIS land/water mask -> used to set pct_ocean (water=0, land=1 in MODIS LW)
        # VCF variables stored for later use in split_tree_grass_shrub
        if 'LC_Type1' in lc_rs_data:
            lt_chunk_data.pct_pft[:, 0] = lc_rs_data['LC_Type1']
        if 'LW' in lc_rs_data:
            # MODIS LW: 0=water, 1=land; pct_ocean = fraction that is water = 1 - LW
            lt_chunk_data.pct_ocean[:] = 1.0 - lc_rs_data['LW']
        if 'Percent_Tree_Cover' in lc_rs_data:
            lt_chunk_data.pct_pft[:, 1] = lc_rs_data['Percent_Tree_Cover']

        return lt_chunk_data

    finally:
        # clean up worker temp dir regardless of success or failure
        shutil.rmtree(tmp_dir, ignore_errors=True)



########## run()

## called by land_type.process_single_year() for each year, and this is where the multiprocessing happens for the landcover module
## this sets up the pool and calls the landcover_process() function for each chunk of data

def run(lt_year_data, year, prev_year, prev_fname, lc_rs_path, lc_rs_name,
                            com_config_dict, out_grid_data, decomp_indices, decomp_ll_limits,
                            manager):



    logger.info(f"Processing landcover module")
    # todo: print the parameters here

    # number of available cpu cores (set by shell run script during job submission)
    cpus_avail = os.environ.get('SRUN_CPUS_PER_TASK')

    if cpus_avail is not None:
        try:
            # Convert the string value to an integer
            cpus_avail_int = int(cpus_avail)
            logger.info(f"SRUN_CPUS_PER_TASK is set to: {cpus_avail_int}")
        except ValueError:
            logger.warning(f"SRUN_CPUS_PER_TASK is set to an invalid integer value: {cpus_avail}")
    else:
        logger.warning("SRUN_CPUS_PER_TASK environment variable is not set.")
        # If not set, set to total cores on the node
        cpus_avail_int = mp.cpu_count()
        logger.warning(f"Using total cores: {cpus_avail_int}, but this may fail if "
                       "SBATCH --cpus-per-task is set to a lower number or SBATCH --exclusive is not set")

    # set up the pool and call the landcover_process() function for each chunk of data
    # chunks are defined by the lat-lon limits and corresponding landgen grid cell ids for the chunk;
    #    these are created in land_type.process_single_year() and passed to this run() function
    # there are more chunks than cpus; the pool will manage this for efficiency because chunks vary in size
    # the results will be stored directly in the lt_year_data shared structure

    # get the manager locks for the shared data structures
    # using data-specific locks, watch out for deadlocks.  
    man_lock  = manager.Lock()
    grid_lock = manager.Lock()

## todo: figure out the data to pass here
# each chunk is a tuple of the arguments for landcover_process, residing in a list
# each tuple includes the lat/lon limits and cell ids for the chunk, and the static arguments that are repeated for each chunk
# e.g.: data_chunks = [(lt_year_data, year, prev_year, prev_fname, lc_rs_path, lc_rs_name,
#          com_config_dict, out_grid_data, ll_limits1, cell_ids1, man_lock, grid_lock, lt_lock),
#          (lt_year_data, year, prev_year, prev_fname, lc_rs_path, lc_rs_name,
#           com_config_dict, out_grid_data, ll_limits2, cell_ids2, man_lock, grid_lock, lt_lock), etc]

## first try the return/copy approach to get this working
# have the process function return an LtData object for the chunk, and then copy the data into the shared lt_year_data structure in this run() function
# this is simpler to code and avoids potential issues with multiple processes writing to the same shared data structure, but may be less efficient because of the copying step
## an alternative is to redefine lt_year_data as a numpy array of individual cell data structures of numpy arrays for each variable,
#.   define this as shared memory,
#.   and then each worker can write directly to the appropriate cells in the shared lt_year_data structure based on the cell ids for the chunk

    ## todo: check that this is correct
    # sort decomp_indices and decomp_ll_limits together, largest chunks first,
    # so the pool receives the most expensive work items early (improves load balancing)
    sorted_pairs = sorted(zip(decomp_indices, decomp_ll_limits),
                          key=lambda pair: len(pair[0]), reverse=True)
    decomp_indices, decomp_ll_limits = zip(*sorted_pairs) if sorted_pairs else ([], [])

    # create the list of chunk data
    data_chunks = []
    for cidx in range(len(decomp_indices)):
        # this list includes only cells in the landgen grid file
        data_chunks.append((
            year, prev_year, prev_fname, lc_rs_path, lc_rs_name,
            com_config_dict, out_grid_data, decomp_indices[cidx], decomp_ll_limits[cidx],
            man_lock, grid_lock
        ))

    logger.info(f"Submitting {len(data_chunks)} landcover chunks to pool of {cpus_avail_int} workers")

    resource_logger.info(f"In landcover submodule run():\n"
        f"Submitting {len(data_chunks)} landcover chunks to pool of {cpus_avail_int} workers")

    # submit the chunks to the pool
    with mp.Pool(processes=cpus_avail_int) as pool:
        chunk_list_results = pool.starmap(landcover_process, data_chunks)

    # copy the results into the shared lt_year_data structure
    for chunk_result in chunk_list_results:
        lt_year_data.copy_from(chunk_result)

    return
