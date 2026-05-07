# the main module for landgen

# year processing can go forward or backward in time, so set the start and end years appropriately in the config file
# config_path is the full path the .json config file, including the file name, e.g. /path/to/config.json

import multiprocessing as mp
import importlib
import json
import sys
from pathlib import Path
import pandas as pd
from . import shared_data
from . import landgen_io

def load_config(config_path):
    with open(config_path, 'r') as f:
        return json.load(f)

def main(config_path):
	# todo: need to deal with landfrac data structure
	landfrac = None
	config = load_config(config_path)
	modules = config.get('modules', [])

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

	# create the shared landgen out grid shared data structure
	grid_manager = shared_data.GridManager()
	grid_manager.start()
	out_grid_data = grid_manager.GridData()

	# read the HEALPix mesh to determine n_cells and populate cell_id
	global_parquet_path = (
		Path(temp_dict['source_data_path'])
		/ Path(temp_dict['landgen_grid_path']).parent
		/ 'merged_land_cells.parquet'
	)
	_mesh_df = pd.read_parquet(global_parquet_path, columns=['cellid'])
	n_cells = len(_mesh_df)
	out_grid_data.allocate(n_cells=n_cells)
	out_grid_data.set_cell_id(_mesh_df['cellid'].values)

	## todo: read in the grid file and set the remaining values in out_grid_data (lon, lat, area, etc.)

	# these are lists of tuples with each tuple defining a chunk, and are paired in order
	# decomp_indices: indices within each chunk for the landgen grid file variables
	# decomp_ll_limits = list(float) of [(min_lat, max_lat, min_lon, max_lon),... for each chunk]
	#    these are based on the vertices of the cells in decomp_indices to ensure full coverage
	mesh_nc_path = Path(temp_dict['source_data_path']) / temp_dict['landgen_grid_path']
	decomp_indices   = []
	decomp_ll_limits = []
	landgen_io.set_decomp_cell_idx_ll_limits(mesh_nc_path, decomp_indices, decomp_ll_limits,
										   out_dir=temp_dict['out_path'])

	## todo: need to figure out how to write the large output file without storing the entire grid in memory

	try:
		for mod in modules:
			name = mod['name']
			params = mod.get('params', {})
			try:
				module = importlib.import_module(f'landgen.{name}')
				if hasattr(module, 'run'):
					print(f"Running module: {name}")
					run_list = [*params.values(), com_config_dict, out_grid_data, manager, grid_manager,\
								 decomp_indices, decomp_ll_limits]
					module.run(*run_list)
				else:
					print(f"Module {name} does not have a 'run' function.")
			except ImportError as e:
				print(f"Could not import module {name}: {e}")

	except Exception as e:
		print(f"ERROR in landgen: {e}")
		raise

	finally:
		# free the shared memory
		com_config_dict = None
		out_grid_data = None
		manager.shutdown()
		grid_manager.shutdown()
	return
