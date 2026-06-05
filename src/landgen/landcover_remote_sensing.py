# landcover_remote_sensing.py
# this module defines the landcover source data
#   including schema mapping files

# the goal is to be able to add different source data definitions here
#    and use a generic api to landcover.py

import json
import logging
from pathlib import Path

import numpy as np
import rasterio
from osgeo import ogr
from rasterio.features import rasterize
from rasterio.warp import transform_geom

from . import landgen_io

logger = logging.getLogger('landgen')


# exclude the caspian sea from the ocean mask because it is inlcluded in the hydrolakes data
# for now keep the kerch straight and sea of azov; these will be dealt with later, maybe in mksurfdata
# these are vertices to exclude sea of azov and kerch straight in one polygon:
#    [ [34.4, 45.2], [34.4, 46.5], 37.6, 47.5], [40.1, 47.5], [38.3, 45.2], [36.8, 45.2], [36.7, 45.16], [36.4, 45.1], [35.5, 45.2], [34.4, 45.2] ]
# coordinates are in [lon,lat] order
_OCEAN_EXCLUSION_POLYGONS_LONLAT = [
    {
        'name': 'Caspian Sea',
        'geometry': {
            'type': 'Polygon',
            'coordinates': [[
                [46.0, 36.0],
                [55.8, 36.0],
                [55.8, 47.6],
                [46.0, 47.6],
                [46.0, 36.0],
            ]],
        },
    }
]

#### set some parameters for the landcover remote sensing data here

#------- modis -----------------------------------
modis_name = 'modis'
modis_years = list(range(2001, 2024+1)) # 2001-2024
# MCD12Q1 is the land cover type product, MOD44B is the vegetation continuous fields product
modis_products = ['MCD12Q1.061', 'MOD44B.061', 'MOD44W.061']  # specify the modis products to use; adjust as needed
# MCD12Q1: LC_Type1 is the IGBP land cover type, LW is the land/water mask; not using QC
modis_lc_variable_names = ['LC_Type1']
# MOD44B: water cell has value==200; not using Quality
modis_vcf_variable_names = ['Percent_Tree_Cover', 'Percent_NonTree_Vegetation', 'Percent_NonVegetated']
# MOD44W: use for ocean mask: 0=shallow ocean, 6=moderate ocean, 7=deep ocean
# the ocean data are static, so only need to read for one year; use the same for all years
# use year 2000 because it is most consistent with the source static data
# this can have only one variable name, even though it is a list
modis_wat_variable_names = ['seven_class']
water_year = 2000

# note that there are no modis tiles for these products for:
#    easter island (ll_limtis=(-27.363585, -26.944359, -109.599609, -109.160156))
#    sao pedro e sao paulo islands (ll_limtis=(0.74606, 1.044512, -29.53125, -29.179688))
#    random southern ocean ice island (ll_limtis=(-54.628738, -54.244919, 3.28125, 3.534031))

# the water cells in MOD44B are from MOD44W, and are the highest res land/water mask;
# LW is from MOD44W, with water cell where 2 or more MOD44W cells are water


# todo: dictionary for modis to elm land type mapping

#------- use_lc_rs -------------------------------
def use_lc_rs(year, lc_rs_name):
    """Return True if remote sensing data identified by lc_rs_name is available for year."""
    if lc_rs_name == modis_name:
        return year in modis_years
    else:
        raise ValueError(f"use_lc_rs: unknown lc_rs_name '{lc_rs_name}'")


### maybe pass com_confg_dict to this read to get more control
# e.g., the water data only need to be read once

#------- read ------------------------------------
def read_to_geotiff(year, lc_rs_name, lc_rs_path, cell_indices, ll_limits):
    """Read land cover data from remote sensing source identified by lc_rs_name for the given year."""

    if lc_rs_name == modis_name:
        # read modis cover data - lc_rs_path is the temp dir because the data are downloaded as needed
        # reading the igbp cover data and writing the geotiffs for this chunk
        lc_files = landgen_io.read_modis_ll_to_geotiff(year, lc_rs_path, modis_products[0], 
                modis_lc_variable_names, ll_limits=ll_limits)
        # reading the vcf data and writing the geotiffs for this chunk
        vcf_files = landgen_io.read_modis_ll_to_geotiff(year, lc_rs_path, modis_products[1], 
                modis_vcf_variable_names, ll_limits=ll_limits)
        # reading the water mask data and writing the geotiffs for this chunk
        # todo: only do this once, but how to determine when? and read only one year
        wat_files = landgen_io.read_modis_ll_to_geotiff(water_year, lc_rs_path, modis_products[2], 
                modis_wat_variable_names, ll_limits=ll_limits)
        # Present a generic key to downstream code while preserving MODIS variable naming internally.
        water_files = {}
        if modis_wat_variable_names[0] in wat_files:
            water_files['water_class'] = wat_files[modis_wat_variable_names[0]]
        out_files = {**lc_files, **vcf_files, **water_files}
    else:
        raise ValueError(f"read: unknown lc_rs_name '{lc_rs_name}'")

    return out_files


def set_ocean(lc_rs_name, water_class_tif, source_data_path, ocean_shapefile_path):
    """Write GeoTIFF binary ocean mask.

    Pixels are set to 100 only when both conditions are true:
      1) original water_class values identify ocean
      2) any part of the pixel intersects the ocean polygon shapefile

    All other pixels are set to 0. The input TIFF is replaced in place.

    Args:
        lc_rs_name (str): Remote-sensing source identifier.
        water_class_tif (str|Path): Path to water_class GeoTIFF.
        source_data_path (str|Path): Root source-data path from config.
        ocean_shapefile_path (str|Path): Shapefile path relative to source_data_path.

    Returns:
        Path: Path to the rewritten TIFF (same as input path).
    """

    tif_path = Path(water_class_tif)
    if not tif_path.exists():
        raise FileNotFoundError(f"set_ocean: water_class GeoTIFF not found: {tif_path}")

    with rasterio.open(tif_path) as src:
        arr = src.read(1)
        profile = src.profile.copy()
        out_shape = arr.shape
        transform = src.transform
        target_crs = src.crs

    # ocean shapefile should apply to all rs data
    if not ocean_shapefile_path:
        raise ValueError(
            "set_ocean: ocean_shapefile_path is empty; set this in config.json "
            "as a path relative to source_data_path"
        )

    shp_path = Path(source_data_path) / Path(ocean_shapefile_path)
    if not shp_path.exists():
        raise FileNotFoundError(f"set_ocean: ocean shapefile not found: {shp_path}")

    ds = ogr.Open(str(shp_path))
    if ds is None:
        raise RuntimeError(f"set_ocean: could not open ocean shapefile: {shp_path}")
    layer = ds.GetLayer(0)
    layer_srs = layer.GetSpatialRef()
    src_crs_wkt = layer_srs.ExportToWkt() if layer_srs is not None else None

    geometries = []
    layer.ResetReading()
    for feat in layer:
        geom = feat.GetGeometryRef()
        if geom is None:
            continue
        gjson = json.loads(geom.ExportToJson())
        if src_crs_wkt and target_crs is not None:
            gjson = transform_geom(src_crs_wkt, target_crs.to_string(), gjson)
        geometries.append(gjson)
    ds = None

    if not geometries:
        raise RuntimeError(f"set_ocean: no geometries found in {shp_path}")

    ocean_touch_mask = rasterize(
        geometries,
        out_shape=out_shape,
        transform=transform,
        fill=0,
        default_value=1,
        all_touched=True,
        dtype=np.uint8,
    ).astype(bool)

    exclusion_geometries = []
    for feature in _OCEAN_EXCLUSION_POLYGONS_LONLAT:
        exclusion_geom = feature['geometry']
        if target_crs is not None:
            exclusion_geom = transform_geom('EPSG:4326', target_crs.to_string(), exclusion_geom)
        exclusion_geometries.append(exclusion_geom)

    exclusion_mask = rasterize(
        exclusion_geometries,
        out_shape=out_shape,
        transform=transform,
        fill=0,
        default_value=1,
        all_touched=True,
        dtype=np.uint8,
    ).astype(bool)

    ocean_touch_mask &= ~exclusion_mask

    # --------- modis -----------------------------------
    # 1) original seven_class value is in {0, 5, 6, 7}, see above for class definitions
    if lc_rs_name == modis_name:
        class_mask = np.isin(arr, [0, 5, 6, 7])
    else:
        raise ValueError(f"set_ocean: unknown lc_rs_name '{lc_rs_name}'")

    out_arr = np.where(class_mask & ocean_touch_mask, 100, 0).astype(np.uint8)

    profile.update(dtype=rasterio.uint8, count=1, nodata=0)
    with rasterio.open(tif_path, 'w', **profile) as dst:
        dst.write(out_arr, 1)

    logger.info(
        f"set_ocean: rewrote {tif_path} using ocean polygons from {shp_path}; "
        f"excluded={','.join(f['name'] for f in _OCEAN_EXCLUSION_POLYGONS_LONLAT)}; "
        f"ocean_pixels={int(np.count_nonzero(out_arr == 100))}, total_pixels={out_arr.size}"
    )
    return tif_path


    