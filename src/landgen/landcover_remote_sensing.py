# landcover_remote_sensing.py
# this module defines the landcover source data
#   including schema mapping files

# the goal is to be able to add different source data definitions here
#    and use a generic api to landcover.py

import .landgen_io as landgen_io

#### set some parameters for the landcover remote sensing data here

#------- modis -----------------------------------
modis_name = 'modis'
modis_years = list(range(2001, 2024+1)) # 2001-2024
# MCD12Q1 is the land cover type product, MOD44B is the vegetation continuous fields product
modis_products = ['MCD12Q1.061', 'MOD44B.061']
# MCD12Q1: LC_Type1 is the IGBP land cover type, LW is the land/water mask; not using QC
modis_lc_variable_names = ['LC_Type1', 'LW']
# MOD44B: water cell has value==200; not using Quality
modis_vcf_variable_names = ['Percent_Tree_Cover', 'Percent_NonTree_Vegetation', 'Percent_NonVegetated']  

# todo: dictionary for modis to elm land type mapping

#------- use_lc_rs -------------------------------
def use_lc_rs(year, lc_rs_name):
    """Return True if remote sensing data identified by lc_rs_name is available for year."""
    if lc_rs_name == modis_name:
        return year in modis_years
    else:
        raise ValueError(f"use_lc_rs: unknown lc_rs_name '{lc_rs_name}'")


#------- read ------------------------------------
def read_to_geotiff(year, lc_rs_name, lc_rs_path, cell_indices, ll_limits):
    """Read land cover data from remote sensing source identified by lc_rs_name for the given year."""

    if lc_rs_name == modis_name:
        # read modis cover data - lc_rs_path is the temp dir because the data are downloaded as needed
        # reading the igbp cover data and writing the geotiffs for this chunk
        lc_files = landgen_io.read_modis_ll_to_geotiff(year, lc_rs_path, modis_products[0], modis_lc_variable_names, ll_limits=ll_limits)
        # reading the vcf data and writing the geotiffs for this chunk
        vcf_files = landgen_io.read_modis_ll_to_geotiff(year, lc_rs_path, modis_products[1], modis_vcf_variable_names, ll_limits=ll_limits)
        out_files = {**lc_files, **vcf_files}
    else:
        raise ValueError(f"read: unknown lc_rs_name '{lc_rs_name}'")

    return out_files


    