# the main module for landgen

# year processing can go forward or backward in time, so set the start and end years appropriately in the config file
# config_path is the full path the .json config file, including the file name, e.g. /path/to/config.json

import multiprocessing as mp
import importlib
import json
import os
import sys
import logging
from datetime import datetime
from pathlib import Path
import pandas as pd
from . import shared_data
from . import landgen_io
from . import tools
import threading



def load_config(config_path):
    with open(config_path, 'r') as f:
        return json.load(f)

def main(config_path):
    # todo: need to deal with landfrac data structure
    landfrac = None
    config = load_config(config_path)

    # set up the shared logger before anything else so all modules can use it
    out_path = Path(config.get('out_path', '.'))
    out_path.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    job_id    = os.environ.get('SLURM_JOB_ID', 'local')
    log_name  = f'landgen_{timestamp}_{job_id}.log'
    logger = tools.setup_logger('landgen', out_path / log_name)
    logger.info(f"landgen started at {timestamp} — config: {config_path}")
    logger.info(f"log file: {out_path / log_name}")
    modules = config.get('modules', [])

    # set up the cluster resource logger
    # log every 5 minutes (300 seconds); adjust as needed
    resource_log_name = f'resource_monitor_{timestamp}_{job_id}.log'
    resource_logger = tools.setup_logger('ClusterMonitor', out_path / resource_log_name)
    stop_event = threading.Event()
    resource_monitor_thread = threading.Thread(
            target=tools.monitor_cluster_resources,
            args=(300.0, stop_event),
            daemon=True)
    resource_monitor_thread.start()

    # get the common parameters for all modules and store in a shared dictionary
    temp_dict = {
                'start_year': config.get('start_year', 2015),
                'end_year': config.get('end_year', 2015),
                'source_data_path': config.get('source_data_path', ''),
                'landgen_grid_path': config.get('landgen_grid_path', ''),
                'out_path': config.get('out_path', ''),
                'decomp_box_size_degrees': config.get('decomp_box_size_degrees', 10)
            }
    manager = mp.Manager()
    com_config_dict = manager.dict(temp_dict)

    ## todo: delete if not using grid manager
    # for now just set up a structure for use by the master proc
    # the only thing to be updated in this structure is the landfrac
    # if using the manager, need to create set/get functions for the variables
    # create the shared landgen out grid shared data structure
    #grid_manager = shared_data.GridManager()
    #grid_manager.start()
    #out_grid_data = grid_manager.GridData()
    #out_grid_data.allocate()

    # do the decomposition of the landgen mesh here for all modules
    # these are passed by reference to the run() functions for each module
    # default data chunks are based on 10x10 degree lat-lon boxes (648 chunks)
    #    15x15 degree box gives 288 chunks, 30x30 box gives 72 chunks
    # Note that chunks are not equal in size

    # these are lists of tuples with each tuple defining a chunk, and are paired in order
    # decomp_indices: indices within each chunk for the landgen grid file variables 
    # decomp_ll_limits = list(float) of [(min_lat, max_lat, min_lon, max_lon),... for each chunk]
    #    these are based on the vertices of the cells in decomp_indices to ensure full coverage
    # the chunk_file is written, but not used; it is for diagnostics
    # note that indices are 0-based in these arrays
    decomp_indices   = []
    decomp_ll_limits = []
    mesh_nc_path = Path(temp_dict['source_data_path']) / temp_dict['landgen_grid_path']
    chunk_file = landgen_io.set_decomp_cell_idx_ll_limits(mesh_nc_path, decomp_indices, decomp_ll_limits,
                                                          out_dir=temp_dict['out_path'])

    ## todo: read in the grid file and set the values in out_grid_data - may not need this?
    # actually, get this info and store it for now; probably don't need the manager and lock stuff, though
    out_grid_data = shared_data.GridData()
    out_grid_data.allocate(n_cells=sum(len(t) for t in decomp_indices))

    # load all mesh cells from the NetCDF domain file and fill out_grid_data
    mesh = landgen_io.load_mesh_nc(mesh_nc_path)  # loads all cells (no indices/ll_limits filter)
    out_grid_data.cell_id[:]              = mesh['cellid']
    out_grid_data.lon_xy[:]              = mesh['lon']
    out_grid_data.lat_xy[:]              = mesh['lat']
    out_grid_data.cell_area[:]           = mesh['area']
    out_grid_data.lon_vtx[:, :]          = mesh['xv']   # shape (n_cells, n_vertices)
    out_grid_data.lat_vtx[:, :]          = mesh['yv']   # shape (n_cells, n_vertices)
    # landfrac is initialised to 1 by allocate(); updated later by topography module

    ## todo: need to figure out how to write the large output file without storing the entire grid in memory ? 

    try:
        for mod in modules:
            name = mod['name']
            params = mod.get('params', {})
            try:
                module = importlib.import_module(f'landgen.{name}')
                if hasattr(module, 'run'):
                    logger.info(f"Running module: {name}")
                    run_list = [*params.values(), com_config_dict, out_grid_data, manager, \
                                 decomp_indices, decomp_ll_limits]
                    module.run(*run_list)
                else:
                    logger.warning(f"Module {name} does not have a 'run' function.")
            except ImportError as e:
                logger.error(f"Could not import module {name}: {e}")

    except Exception as e:
        logger.exception(f"ERROR in landgen: {e}")
        raise

    finally:
        manager.shutdown()
        stop_event.set()
        resource_monitor_thread.join()
        resource_logger.info("Cluster resource monitor thread stopped.")
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        logger.info(f"landgen finished at {timestamp}")

    return
