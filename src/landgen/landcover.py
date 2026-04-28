# landcover.py
# this module processes land cover data for the landgen workflow
# the output is a complete land cover distribution
#    and includes some data associated with particular land covers

# run() function is the main entry point for this module, and will be called by process_single_year in land_type.py

import multiprocessing as mp
#import importlib
from pathlib import Path
from . import shared_data
#import landcover_remote_sensing # not created yet
#import transitions # not created yet
#import normalize_cell # not created yet
import pandas as pd
import os

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

def landcover_process(lt_year_data, year, prev_year, prev_fname, lc_rs_path, lc_rs_name,
                            com_config_dict, out_grid_data, ll_limits, cell_ids,
                            man_lock, grid_lock, lt_lock):

    print(f"Processing landcover module year {year} with parameters:")
    # todo: landcover_remote_sensing, transitions, normalize_cell not yet implemented
    print(f"  landcover_process: not yet implemented, skipping for year {year}")
    return





########## run()

## called by land_type.process_single_year() for each year, and this is where the multiprocessing happens for the landcover module
## this sets up the pool and calls the landcover_process() function for each chunk of data

def run(lt_year_data, year, prev_year, prev_fname, lc_rs_path, lc_rs_name,
                            com_config_dict, out_grid_data, ll_limits, cell_ids,
                            manager, grid_manager):



    print(f"Processing landcover module with parameters:")
    # todo: landcover_remote_sensing, transitions, normalize_cell not yet implemented
    print(f"  landcover module: not yet implemented, skipping for year {year}")

    return
