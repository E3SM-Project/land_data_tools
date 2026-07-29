# land_type.py
# this module processes land type data for the landgen workflow
# the output is a complete land type distribution
#    and includes some data associated with particular land types

# run() function is the main entry point for this module, and will be called by landgen.py

import importlib
import logging
from pathlib import Path
from .shared_data import LtData
import numpy as np
from . import landgen_io
from . import tools

logger = logging.getLogger('landgen')

########## define helper functions for land_type here


##### _process_single_year()
def _process_single_year(lt_year_data, year, prev_year, submod_run, submod_dyn, out_fname,
                         submod_sources, submod_decomp_box_size_degrees, com_config_dict, out_grid_data,
                         manager):
    """Process land type data for a single year."""

    # arguments
    # lt_year_data: the shared data structure for the land type data for this year
    # year: the year for which to process the land type data
    # prev_year: the previous year for which land type data were processed

    # other arguments are described below for the run() function

## todo: need to figure out how to use static data that has already been processed for the first year
# maybe: if prev_year is not None and a submodule is static (submod_dyn==false) then use data from previous year file

## todo change the order below, such that static data are processed first

    # Process landcover
    if submod_run['landcover']:
        # derive prev_fname from out_fname and prev_year by inserting the year before the file extension
        # e.g. landgen_land_type.nc -> landgen_land_type_2009.nc
        if prev_year is not None:
            stem, suffix = out_fname.rsplit('.', 1)
            prev_fname = f"{stem}_{prev_year}.{suffix}"
        else:
            prev_fname = None
        lc_src = submod_sources.get('landcover', {})
        lc_rs_src = next(iter(lc_src.values()), {})
        lc_rs_path = lc_rs_src.get('path', '')
        lc_rs_name = lc_rs_src.get('name', '')
        # each module's run function calls the multiple processes because these modules need to be done sequentially
        landcover = importlib.import_module('landgen.landcover')
        landcover.run(lt_year_data, year, prev_year, prev_fname, lc_rs_path, lc_rs_name,
                            com_config_dict, out_grid_data, manager,
                            decomp_box_size_degrees=submod_decomp_box_size_degrees.get('landcover', 10))

    if submod_run['crop']:
        # Process crop data - adjust lc crop area
        crop_src = submod_sources.get('crop', {})
        crop_data_src = next(iter(crop_src.values()), {})
        crop_path = crop_data_src.get('path', '')
        crop = importlib.import_module('landgen.crop')
        lc_data = crop.run(lt_year_data, year, prev_year, crop_path, com_config_dict, out_grid_data,
                        manager,
                        decomp_box_size_degrees=submod_decomp_box_size_degrees.get('crop', 10))

    if submod_run['urban']:
        # Process urban data - adjust lc urban area
        urban_src = submod_sources.get('urban', {})
        urban_data_src = next(iter(urban_src.values()), {})
        urban_path = urban_data_src.get('path', '')
        urban = importlib.import_module('landgen.urban')
        lc_data = urban.run(lt_year_data, year, prev_year, urban_path, com_config_dict, out_grid_data,
                            manager,
                            decomp_box_size_degrees=submod_decomp_box_size_degrees.get('urban', 10))

    if submod_run['lake']:
        # Process lake data - adjust lc lake area
        lake_src = submod_sources.get('lake', {})
        lake_data_src = next(iter(lake_src.values()), {})
        lake_path = lake_data_src.get('path', '')
        lake = importlib.import_module('landgen.lake')
        lc_data = lake.run(lt_year_data, year, prev_year, lake_path, com_config_dict, out_grid_data,
                            manager,
                            decomp_box_size_degrees=submod_decomp_box_size_degrees.get('lake', 10))

    if submod_run['ice']:
        # Process ice data - adjust lc ice area
        ice_src = submod_sources.get('ice', {})
        ice_data_src = next(iter(ice_src.values()), {})
        ice_path = ice_data_src.get('path', '')
        ice = importlib.import_module('landgen.ice')
        lc_data = ice.run(lt_year_data, year, prev_year, ice_path, com_config_dict, out_grid_data,
                            manager,
                            decomp_box_size_degrees=submod_decomp_box_size_degrees.get('ice', 10))

    #if submod_run['wetland']:
        # Process wetland data - adjust lc wetland area
        # (may not be needed as the main source is currently the modis cover data;
        #  can allow for this in the future)
        #wetland_src = submod_sources.get('wetland', {})
        #wetland_data_src = next(iter(wetland_src.values()), {})
        #wetland_path = wetland_data_src.get('path', '')
        #wetland = importlib.import_module('landgen.wetland')
        #lc_data = wetland.run(lt_year_data, year, prev_year, wetland_path, com_config_dict, out_grid_data,
        #                    manager,
        #                    decomp_box_size_degrees=submod_decomp_box_size_degrees.get('wetland', 10))
        pass

    if submod_run['management']:
        #todo: update this with more efficient decomp and generalized reading and chunking
        # Process harvest/grazing data - adjust harvest/grazing area
        mgmt_src = submod_sources.get('management', {})
        harvest_src = mgmt_src.get('harvest', {})
        harvest_path = harvest_src.get('path', '')
        harvest_name = harvest_src.get('name', '')
        grazing_src = mgmt_src.get('grazing', {})
        grazing_path = grazing_src.get('path', '')
        grazing_names = grazing_src.get('names', {})
        management = importlib.import_module('landgen.management')
        lc_data = management.run(lt_year_data, year, prev_year, harvest_path, harvest_name, grazing_path,
                        grazing_names, com_config_dict, out_grid_data,
                        decomp_box_size_degrees=submod_decomp_box_size_degrees.get('management', 10))

    # Normalize cell
    #normalize_cell = importlib.import_module('landgen.normalize_cell')
    #lc_data = normalize_cell.fill_land(lt_year_data, out_grid_data,
    #                manager)       # fill_land
    #lc_data = normalize_cell.reconcile_ocean(lt_year_data, out_grid_data,
    #               manager)  # reconcile_ocean

    if submod_run['veg_char']:
        # Process veg-associated data
        veg_char_src = submod_sources.get('veg_char', {})
        veg_char_data_src = next(iter(veg_char_src.values()), {})
        veg_char_path = veg_char_data_src.get('path', '')
        veg_char = importlib.import_module('landgen.veg_char')
        lc_data = veg_char.run(lt_year_data, year, prev_year, veg_char_path, com_config_dict, out_grid_data,
                            manager,
                            decomp_box_size_degrees=submod_decomp_box_size_degrees.get('veg_char', 10))

    # Ensure consistency
    #consistency = importlib.import_module('landgen.consistency')
    #lc_data = consistency.run(lt_year_data, year, out_grid_data, decomp_indices, decomp_ll_limits, manager)

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

# Note that chunks are not equal in size

## output

def run(active, submod_run, submod_dyn, out_fname,
        submod_sources, submod_decomp_box_size_degrees,
        com_config_dict, out_grid_data, manager):
    if active is False:
        logger.info("Skipping land_type module")
        return

    # set up the land_type module shared data structure
    # this holds only one year of data, so write it each year
    lt_year_data = LtData()
    # get the actual number of land cells from out_grid_data
    n_cells = out_grid_data.num_cells
    print(f"  Allocating LtData for {n_cells} land cells")
    lt_year_data.allocate(n_cells=n_cells)

    #lt_manager = LtManager()
    #lt_manager.start()
    #lt_year_data = lt_manager.LtData()
    #lt_year_data.allocate()

    logger.info("Processing land_type module")
    # todo: print the parameters here

    # extract common parameters from shared config dict
    start_year = com_config_dict['start_year']
    end_year   = com_config_dict['end_year']
    out_path   = com_config_dict['out_path']

    # processing code for land_type
    years = np.arange(start_year, end_year + 1)
    #output_file = Path(out_path) / out_fname

    prev_year = None

    # 1. Loop over desired years
    for year in years:
        # 2. Process single year
        logger.info(f"Processing year: {year}")
        _process_single_year(lt_year_data, year, prev_year, submod_run, submod_dyn, out_fname,
                            submod_sources, submod_decomp_box_size_degrees, com_config_dict, out_grid_data, manager)

        # no - would have to read in while file to reverse the order - append this year's data to the output file
        # these data may need to be appended chunk by chunk if memory is an issue, but try writing the whole year at once first
        # todo: can write each year, then combine at end in proper order 

        # set timevars in shared_data for each data class
        # Variables to write to output NetCDF:
        #   pct_pft: landcover percentages [n_cells, n_pfts]
        #   pct_ocean: ocean percentage [n_cells]
        #   harvest_frac: harvest fractions from LUH2 [n_cells, n_harvest=10]
        #   grazing_frac: grazing fractions from HYDE3.5 [n_cells, n_grazing=2]
        # Variables with time dimension (for annual concatenation with ncrcat):
        #   All of the above vary by year
        varnames = ['pct_pft', 'pct_ocean', 'harvest_frac', 'grazing_frac']
        timevars = ['pct_pft', 'harvest_frac', 'grazing_frac']

        # insert _<year> before the extension (or at the end if no extension)
        out_fname_p = Path(out_fname)
        out_fname_year = f"{out_fname_p.stem}_{year}{out_fname_p.suffix}"

        landgen_io.write_module_netcdf(out_grid_data, lt_year_data, out_path, out_fname_year,
                        year=year, timevars=timevars, varnames=varnames, ll_limits=None)

        prev_year = year

    # todo: combine the annual files into one file in the correct time order
    # can use xarray.open_mfdataset(sorted_files) or ncrcat 
    # actually, write individual year files


    ## todo: this is temporary for testing? or maybe not?
    # just plot the start year for now
    #plot_fname_year = f"{out_fname_year.stem}_{start_year}{out_fname_year.suffix}"
    ncdf_path = Path(out_path) / out_fname_year
    print_layers = [0, 1]
    tools.plot_module_netcdf(ncdf_path, out_path, start_year, varnames=varnames, layers=print_layers,
                       plot_type='scatter', file_type='png',
                       colormap='viridis', ll_limits=None)

    # free the module-specific shared data structure
    lt_year_data = None
    #lt_manager.shutdown()
    return
        
