# the main module for landgen

# year processing can go forward or backward in time, so set the start and end years appropriately in the config file
# config_path is the full path the .json config file, including the file name, e.g. /path/to/config.json

import importlib
import json
import os
from pathlib import Path
import sys
import logging
from datetime import datetime
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
    # the default interval is 1 minute (60 seconds)
    #    adjust as needed by changing'interval_sec': ### below in kwargs
    resource_log_name = f'resource_monitor_{timestamp}_{job_id}.log'
    resource_logger = tools.setup_logger('ClusterMonitor', out_path / resource_log_name)
    stop_event = threading.Event()
    resource_monitor_thread = threading.Thread(
            target=tools.monitor_cluster_resources,
            kwargs={'interval_sec': 60.0, 'stop_event': stop_event},
            daemon=True)
    resource_monitor_thread.start()

    # get the common parameters for all modules and store in a shared dictionary
    temp_dict = {
                'start_year': config.get('start_year', 2015),
                'end_year': config.get('end_year', 2015),
                'source_data_path': config.get('source_data_path', ''),
                'landgen_grid_path': config.get('landgen_grid_path', ''),
                'ocean_shapefile_path': config.get('ocean_shapefile_path', ''),
                'out_path': config.get('out_path', ''),
                'log_path': str(out_path / log_name),
            }
    com_config_dict = temp_dict

    # Note that the decomposition of the landgen mesh is done by each module/submodule, with specific
    #    decomp sizes set for each module/submodule by the user, to maximize efficiency for specific
    #    source data and processing.
    # default data chunks are based on 10x10 degree lat-lon boxes (648 chunks)
    #    15x15 degree box gives 288 chunks, 30x30 box gives 72 chunks
    # Note that chunks are not equal in size

    # the chunk data structures are lists of tuples with each tuple defining a chunk, and are paired in order
    # decomp_indices: indices within each chunk for the landgen grid file variables 
    # decomp_ll_limits = list(float) of [(min_lat, max_lat, min_lon, max_lon),... for each chunk]
    #    these are based on the vertices of the cells in decomp_indices to ensure full coverage
    # note that indices are 0-based in these arrays

    mesh_nc_path = Path(com_config_dict['source_data_path']) / com_config_dict['landgen_grid_path']

    # load all mesh cells from the NetCDF domain file and fill out_grid_data
    mesh = landgen_io.load_mesh_nc(mesh_nc_path)  # loads all cells (no indices/ll_limits filter)
    out_grid_data = shared_data.GridData()
    out_grid_data.allocate(n_cells=mesh['cellid'].shape[0], n_vertices=mesh['lon_v'].shape[1])
    out_grid_data.cell_id[:]              = mesh['cellid']
    out_grid_data.lon_cen[:]              = mesh['lon']
    out_grid_data.lat_cen[:]              = mesh['lat']
    out_grid_data.cell_area[:]           = mesh['area']
    out_grid_data.lon_vtx[:, :]          = mesh['lon_v']   # shape (n_cells, n_vertices)
    out_grid_data.lat_vtx[:, :]          = mesh['lat_v']   # shape (n_cells, n_vertices)
    # landfrac is initialised to 1 by allocate(); updated later by landcover? module
    ## todo: need to fill landfrac properly, either from landcover if it is active, or read from a file.

    try:
        for mod in modules:
            name = mod['name']
            params = mod.get('params', {})
            try:
                module = importlib.import_module(f'landgen.{name}')
                if hasattr(module, 'run'):
                    logger.info(f"Running module: {name}")
                    module.run(**params,
                               com_config_dict=com_config_dict,
                               out_grid_data=out_grid_data)
                else:
                    logger.warning(f"Module {name} does not have a 'run' function.")
            except ImportError as e:
                logger.error(f"Could not import module {name}: {e}; skipping.")

    except Exception as e:
        logger.exception(f"ERROR in landgen: {e}")
        raise

    finally:
        # remove uraster side logs only from run/submit cwd locations.
        # keep copies in out_path for diagnostics.
        uraster_log_names = ('extract.log', 'intersect.log', 'uraster.log', 'utility.log')
        cleanup_dirs = [Path.cwd()]
        submit_dir = os.environ.get('SLURM_SUBMIT_DIR')
        if submit_dir:
            cleanup_dirs.append(Path(submit_dir))
        for d in cleanup_dirs:
            for name in uraster_log_names:
                try:
                    (d / name).unlink(missing_ok=True)
                except Exception:
                    pass

        stop_event.set()
        resource_monitor_thread.join()
        resource_logger.info("Cluster resource monitor thread stopped.")
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        logger.info(f"landgen finished at {timestamp}")

    return
