# topography.py
# this module processes topography data for the landgen workflow
# this needs to be run first, as the landfrac data are needed for each of the other modules
# if these data are already available, then this module will simply read in the landfrac data
# the output is the complete set of topography data needed for landgen and elm

# run() function is the main entry point for this module, and will be called by landgen.py

import multiprocessing as mp
import importlib
import logging
import sys
from pathlib import Path
from . import shared_data
from .shared_data import TopoData

logger = logging.getLogger('landgen')


########## define helper functions for land_type run() here





########## run()

## arguments
## these first ones are module-specific parameters that are set in the config file
# active: true = module is run, false = module is skipped
# out_fname: output filename for the module
# decomp_box_size_degrees: the size of the decomposition box in degrees for parallel processing
# com_config_dict: the shared dictionary for the common parameters for all modules
# out_grid_data: the shared data structure for the landgen grid data

## output

def run(active, out_fname, decomp_box_size_degrees=10, com_config_dict=None, out_grid_data=None):
    if active is False:
        logger.info("Skipping topography module")
        return

    # set up the topography module shared data structure
    topo_out_data = TopoData()
    # get the actual number of land cells from out_grid_data
    n_cells = out_grid_data.num_cells
    print(f"  Allocating TopoData for {n_cells} land cells")
    topo_out_data.allocate(n_cells=n_cells)

    logger.info("Processing topography module")
    # todo: print the parameters here


    # topography data processing

    # mp pool and parallel processing code will happen in this module or its submoduels


    # free the module-specific shared data structure
    topo_out_data = None
    return
