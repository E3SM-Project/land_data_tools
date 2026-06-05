# landcover_remote_sensing.py
# this module defines the landcover source data
#   including schema mapping files

# the goal is to be able to add different source data definitions here
#    and use a generic api to landcover.py

from . import landgen_io

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

# note that there are no modis tiles for these products for:
#    easter island (ll_limtis=(-27.363585, -26.944359, -109.599609, -109.160156))
#    sao pedro e sao paulo islands (ll_limtis=(0.74606, 1.044512, -29.53125, -29.179688))
#    random southern ocean ice island (ll_limtis=(-54.628738, -54.244919, 3.28125, 3.534031))

# todo: don't need LW above from MCD12Q1; get land/water mask from MOD44B data
#    the water cells in MOD44B are from MOD44W, and are the highest res land/water mask;
#    LW is from MOD44W, with water cell where 2 or more MOD44W cells are water

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


    