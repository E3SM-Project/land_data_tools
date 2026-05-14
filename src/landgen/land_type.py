# land_type.py
# this module processes land type data for the landgen workflow
# the output is a complete land type distribution
#    and includes some data associated with particular land types

# run() function is the main entry point for this module, and will be called by landgen.py

import multiprocessing as mp
import importlib
from pathlib import Path
from . import shared_data
from . import landgen_io
from .shared_data import LtData
import numpy as np
import pandas as pd

########## define helper functions for land_type here


##### _process_single_year()
def _process_single_year(lt_year_data, year, prev_year, out_fname, lc_rs_path, lc_rs_name, crop_path, urban_path,
                         lake_path, ice_path, wetland_path, harvest_path, harvest_name, grazing_path, grazing_names, assoc_path, com_config_dict, out_grid_data,
                         manager, grid_manager, decomp_indices, decomp_ll_limits):
    """Process land type data for a single year."""

    # arguments
    # lt_year_data: the shared data structure for the land type data for this year
    # year: the year for which to process the land type data
    # prev_year: the previous year for which land type data were processed

    # other arguments are described below for the run() function

    # data chunks are based on 5x5 degree lat-lon boxes (1440 chunks total, ~720 non-ocean)
    # 10x10 gives 360 non-ocean chunks but with severe load imbalance (3s-500s per chunk)
    # 5x5 reduces per-chunk variance significantly while keeping chunk count manageable
    # todo: add this decomp box size to the config file and to com_config_dict
    decomp_box_size_degrees = 5
    # get the lat-lon limits for the landgen grid
    # ll_limits = list(float) of [(min_lat, max_lat, min_lon, max_lon),(min_lat, max_lat, min_lon, max_lon),... for each chunk]
    chunk_ll_limits = landgen_io.calc_ll_limits(decomp_box_size_degrees)


    # todo: set the multiprocessing chunk info here for all run functions
    # need the lat/lon limits for the chunk, and the corresponding landgen grid cell ids for the chunk
    # do this by lat-lon because all source raw data are on lat-lon grids, but at different resolutions 
    #    and since the source data are several and varied,
    #    it will be faster to read in just the corresponding landgren grid cell info,
    #    even though it is non-sequential
    # the chunks won't be equal size on the landgen grid, but will be consistent,
    #    and will be varied in size based on input source raw data res
    # so put the lat/lon limits and cell ids in corresponding chunks and use the process queue or pool for efficiency
    # arguments for each run function below will include data chunk list with the lat/lon limits and cell ids for the chunk
    #    or the entire list is created here
    # the chunked data are a list of tuples with each argument; 
    #    an example is that the static arguments here will be repeated in each tuple,
    #  and the lat/lon and cell ids will be different for each chunk
    # this info will have to be passed to the run functions below




    # Process landcover
    # derive prev_fname from out_fname and prev_year by inserting the year before the file extension
    # e.g. landgen_land_type.nc -> landgen_land_type_2009.nc
    if prev_year is not None:
        stem, suffix = out_fname.rsplit('.', 1)
        prev_fname = f"{stem}_{prev_year}.{suffix}"
    else:
        prev_fname = None
    # each module's run function calls the multiple processes because these modules need to be done sequentially
    landcover = importlib.import_module('landgen.landcover')
    lc_data = landcover.run(lt_year_data, year, prev_year, prev_fname, lc_rs_path, lc_rs_name,
                            com_config_dict, out_grid_data, decomp_indices, decomp_ll_limits, manager, grid_manager)

    # Process crop data - adjust lc crop area
    try:
        crop = importlib.import_module('landgen.crop')
        lc_data = crop.run(lt_year_data, year, prev_year, crop_path, com_config_dict, out_grid_data, decomp_indices, decomp_ll_limits,
                           manager, grid_manager)
    except ImportError:
        print(f"  Skipping crop module (not yet implemented)")

    # Process urban data - adjust lc urban area
    try:
        urban = importlib.import_module('landgen.urban')
        lc_data = urban.run(lt_year_data, year, prev_year, urban_path, com_config_dict, out_grid_data, decomp_indices, decomp_ll_limits,
                            manager, grid_manager)
    except ImportError:
        print(f"  Skipping urban module (not yet implemented)")

    # Process lake data - adjust lc lake area
    try:
        lake = importlib.import_module('landgen.lake')
        lc_data = lake.run(lt_year_data, year, prev_year, lake_path, com_config_dict, out_grid_data, decomp_indices, decomp_ll_limits,
                           manager, grid_manager)
    except ImportError:
        print(f"  Skipping lake module (not yet implemented)")

    # Process ice data - adjust lc ice area
    try:
        ice = importlib.import_module('landgen.ice')
        lc_data = ice.run(lt_year_data, year, prev_year, ice_path, com_config_dict, out_grid_data, decomp_indices, decomp_ll_limits,
                          manager, grid_manager)
    except ImportError:
        print(f"  Skipping ice module (not yet implemented)")

    # Process wetland data - adjust lc wetland area
    # (may not be needed as the main source is currently the modis cover data;
    #  can allow for this in the future)
    #wetland = importlib.import_module('wetland')
    #lc_data = wetland.run(lt_year_data, year, prev_year, wetland_path, com_config_dict, out_grid_data, decomp_ll_limits, cell_ids, manager, grid_manager)

    #todo: update this with more efficient decomp and generalized reading and chunking
    # Process harvest/grazing data - adjust harvest/grazing area
    harvest = importlib.import_module('landgen.harvest')
    lc_data = harvest.run(lt_year_data, year, prev_year, harvest_path, harvest_name, grazing_path, grazing_names,
                          com_config_dict, out_grid_data, decomp_indices, decomp_ll_limits, manager, grid_manager)

    # Normalize cell
    try:
        normalize_cell = importlib.import_module('landgen.normalize_cell')
        lc_data = normalize_cell.fill_land(lt_year_data, out_grid_data, decomp_indices, decomp_ll_limits, manager, grid_manager)       # fill_land
        lc_data = normalize_cell.reconcile_ocean(lt_year_data, out_grid_data, decomp_indices, decomp_ll_limits, manager, grid_manager)  # reconcile_ocean
    except ImportError:
        print(f"  Skipping normalize_cell module (not yet implemented)")

    # Process veg-associated data
    try:
        veg_assoc = importlib.import_module('landgen.veg_assoc')
        lc_data = veg_assoc.run(lt_year_data, year, prev_year, assoc_path, com_config_dict, out_grid_data, decomp_indices, decomp_ll_limits,
                                manager, grid_manager)
    except ImportError:
        print(f"  Skipping veg_assoc module (not yet implemented)")

    # Ensure consistency
    try:
        consistency = importlib.import_module('landgen.consistency')
        lc_data = consistency.run(lt_year_data, year, out_grid_data, decomp_ll_limits, manager, grid_manager)
    except ImportError:
        print(f"  Skipping consistency module (not yet implemented)")

    return

########## run()

## arguments
## these first ones are module-specific parameters that are set in the config file
# active: true = module is run, false = module is skipped
# out_fname: output filename for the module
# the rest of the params set in the config file
# com_config_dict: the shared dictionary for the common parameters for all modules
# out_grid_data: the shared data structure for the landgen grid data
# manager: the multiprocessing manager for the shared data structures
# grid_manager: the multiprocessing manager for the shared data structure for the landgen grid data
# decomp_indices: the list of cell index chunks for parallel processing
# decomp_ll_limits: the list of lat/lon limits for each chunk for parallel processing

# Note that chunks are not equal in size

## output

def run(active, out_fname, lc_rs_path, lc_rs_name, crop_path, urban_path, lake_path, ice_path,
        wetland_path, harvest_path, harvest_name, grazing_path, grazing_names, assoc_path,
        com_config_dict, out_grid_data, manager, grid_manager, decomp_indices, decomp_ll_limits):
    if active is False:
        print(f"Skipping land_type module")
        return

    # set up the land_type module shared data structure
    # this holds only one year of data, so write it each year
    lt_year_data = LtData()
    # get the actual number of land cells from the HEALPix parquet mesh file
    global_parquet_path = (
        Path(com_config_dict['source_data_path'])
        / Path(com_config_dict['landgen_grid_path']).parent
        / 'merged_land_cells.parquet'
    )
    _mesh_df = pd.read_parquet(global_parquet_path, columns=['cellid'])
    n_cells = len(_mesh_df)
    print(f"  Allocating LtData for {n_cells} land cells")
    lt_year_data.allocate(n_cells=n_cells)

    print(f"Processing land_type module with parameters:")
    # todo: print the parameters here

    # extract common parameters from shared config dict
    start_year = com_config_dict['start_year']
    end_year   = com_config_dict['end_year']
    out_path   = com_config_dict['out_path']

    # processing code for land_type
    years = np.arange(start_year, end_year + 1)
    output_file = Path(out_path) / out_fname

    prev_year = None

    # 1. Loop over desired years
    for year in years:
        # 2. Process single year
        print(f"  Processing year: {year}")
        _process_single_year(lt_year_data, year, prev_year, out_fname, lc_rs_path, lc_rs_name, crop_path, urban_path,
                             lake_path, ice_path, wetland_path, harvest_path, harvest_name, grazing_path, grazing_names, assoc_path, com_config_dict, out_grid_data,
                             manager, grid_manager, decomp_indices, decomp_ll_limits)

        # write this year's lt_year_data to a per-year NetCDF file
        cell_ids = out_grid_data.get_cell_id()
        from landgen.harvest import LUH2_HARVEST_VARS  # noqa: PLC0415
        landgen_io.write_lt_year_data_to_netcdf(
            lt_year_data,
            cell_ids,
            year,
            out_path,
            out_fname,
            LUH2_HARVEST_VARS,
            list(grazing_names.keys()),
        )

        prev_year = year



    # free the module-specific shared data structure
    lt_year_data = None
    return
        
