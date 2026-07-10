# landgen_io.py
# Utility functions for reading harvest data from LUH2 and grazing data from HYDE3.5

import logging
import xarray as xr
import numpy as np
from pathlib import Path

logger = logging.getLogger('landgen')
import rasterio
from rasterio.transform import from_bounds
import json
from uraster.classes.uraster import uraster as URaster
import earthaccess
import glob
import os
import math
import re
from pyproj import Proj
from osgeo import gdal


#--------------------------------------------------------------------------
def set_decomp_cell_idx_ll_limits(nc_path_name, decomp_indices, decomp_ll_limits,
                              chunk_size_degrees=10, out_dir=None):
    """
    Populate chunk_indices and chunk_ll_limits in-place from the domain NetCDF.
    Also write a companion .npz file mapping chunk lat-lon limits to cell indices.

    Approx. cell-centre lat/lon assigns each cell to a chunk box.  Vertex coordinates
    xv/yv determine the tight actual bounding box of the cells in that chunk,
    which is what callers (e.g. write_latlon_to_geotiff) need for source-data
    slicing — the fixed chunk box may extend into ocean where no source data exist.

    Chunks with no land cells are skipped, so len(chunk_indices) may be less
    than the total number of chunk boxes.

    Based on HealPix mesh, but any mesh with the same variables in the same format should work.

    Args:
        nc_path_name (str|Path):      Path (and name) to the domain NetCDF file containing
                                 'lat', 'lon' (~ cell centres), 'xv', 'yv'
                                 (vertex coordinates, shape n_cells x n_vertices).
        chunk_indices (list):    Output — populated in-place.  Each element is a
                                 1D tuple of HEALPix cellid values (int64)
                                 identifying the cells in that chunk.
        chunk_ll_limits (list):  Output — populated in-place.  Each element is a
                                 (min_lat, max_lat, min_lon, max_lon) tuple of the
                                 tight vertex bounding box for that chunk's cells.
                                 Matched by position to chunk_indices.
        chunk_size_degrees (int): Size of the decomposition boxes in degrees.
        out_dir (str|Path|None): Directory to write the companion .spatial_index.npz file.
                                 If None, the file is written alongside nc_path_name.
    """
    nc_path = Path(nc_path_name)
    if not nc_path.exists():
        raise FileNotFoundError(f"set_decomp_cell_idx_ll_limits: Mesh NetCDF file not found: {nc_path}")

    # read the full file once — this is a one-time setup call
    # note that lat and lon are approximate cell centers
    ds      = xr.open_dataset(nc_path, decode_times=False)
    lat     = ds['lat'].values.astype(np.float64)    # (n_cells,)
    lon     = ds['lon'].values.astype(np.float64)    # (n_cells,)
    xv      = ds['xv'].values.astype(np.float64)     # (n_cells, n_vertices)
    yv      = ds['yv'].values.astype(np.float64)     # (n_cells, n_vertices)
    ds.close()

    decomp_indices.clear()
    decomp_ll_limits.clear()

    index = {}
    for min_lat, max_lat, min_lon, max_lon in calc_ll_limits(chunk_size_degrees):
        mask    = (lat >= min_lat) & (lat < max_lat) & (lon >= min_lon) & (lon < max_lon)
        indices = np.where(mask)[0]
        if indices.size == 0:
            continue  # skip ocean-only boxes

        # tight bounding box from actual vertex extents of the cells in this chunk
        xv_chunk = xv[indices]   # (n_chunk_cells, n_vertices)
        yv_chunk = yv[indices]
        decomp_indices.append(tuple(indices.tolist()))

        decomp_ll_limits.append((
            float(yv_chunk.min()), float(yv_chunk.max()),
            float(xv_chunk.min()), float(xv_chunk.max()),
        ))

        # companion file stores NC row indices (used by load_mesh_nc via ds.isel())
        key = f"{min_lat:.0f}_{max_lat:.0f}_{min_lon:.0f}_{max_lon:.0f}"
        index[key] = indices

    # write companion file
    index_name = Path(nc_path).stem + '.spatial_index.npz'
    if out_dir is not None:
        out_path = Path(out_dir) / index_name
    else:
        out_path = Path(nc_path).parent / index_name
    np.savez_compressed(out_path, **index)
    
    logger.info(f"set_decomp_cell_idx_ll_limits: built {len(decomp_indices)} chunks from {nc_path}")
    logger.info(f"set_decomp_cell_idx_ll_limits: chunks include {sum(len(t) for t in decomp_indices)} cells")
    logger.info(f"set_decomp_cell_idx_ll_limits: check against total cells in {nc_path} to ensure all are included")
    logger.info(f"set_decomp_cell_idx_ll_limits: wrote {len(index)} chunks to {out_path}")

    return out_path

#--------------------------------------------------------------------------
def load_mesh_nc(nc_path_name, indices=None, ll_limits=None):
    """
    Load HEALPix mesh data from a NetCDF domain file.

    Args:
        nc_path_name (str|Path):      Path (and name) to the domain NetCDF file.
        indices (tuple|None): 1D tuple of NC indices to load,
                                   as returned in chunk_indices by
                                   set_decomp_cell_idx_ll_limits().  If None,
                                   check for ll_limits.
        ll_limits (tuple|None): (min_lat, max_lat, min_lon, max_lon) tuple
                                to select cells by mapping file.  If also None,
                                no lat/lon filtering is applied. This option
                                uses the companion .npz spatial mapping file
                                created by set_decomp_cell_idx_ll_limits().

    Returns:
        dict with keys 'cellid', 'xv', 'yv', 'lon', 'lat', 'area' (all np.ndarray).
    """
    nc_path = Path(nc_path_name)
    if not nc_path.exists():
        raise FileNotFoundError(f"load_mesh_nc: Mesh NetCDF file not found: {nc_path}")

    ds = xr.open_dataset(nc_path, decode_times=False)

    if indices is not None:
        cell_dim = ds['lat'].dims[0]
        subset   = ds.isel({cell_dim: indices})
    elif ll_limits is not None:
        min_lat, max_lat, min_lon, max_lon = ll_limits
        key      = f"{min_lat:.0f}_{max_lat:.0f}_{min_lon:.0f}_{max_lon:.0f}"
        idx_path = nc_path.with_suffix('.spatial_index.npz')
        if not idx_path.exists():
            raise FileNotFoundError(
                f"load_mesh_nc: Spatial index not found: {idx_path}. "
                f"Run set_decomp_cell_idx_ll_limits('{nc_path}', decomp_indices, decomp_ll_limits, chunk_size_degrees) first."
            )
        indices_ll  = np.load(idx_path)[key]
        if indices_ll.size == 0:
            ds.close()
            raise ValueError(f"load_mesh_nc: No mesh cells found within ll_limits {ll_limits}.")
        cell_dim = ds['lat'].dims[0]
        subset   = ds.isel({cell_dim: indices_ll})
    else:
        subset = ds

    cellid = subset['cellid'].values.astype(np.int64)
    xv     = subset['xv'].values.astype(np.float64)
    yv     = subset['yv'].values.astype(np.float64)
    lon    = subset['lon'].values.astype(np.float64)
    lat    = subset['lat'].values.astype(np.float64)
    area   = subset['area'].values.astype(np.float64)
    ds.close()

    logger.info(f"load_mesh_nc: loaded {len(cellid)} cells from {nc_path}")
    return {'cellid': cellid, 'xv': xv, 'yv': yv, 'lon': lon, 'lat': lat, 'area': area}



#--------------------------------------------------------------------------
def calc_ll_limits(size_degrees):
    """Calculate and return a list of tuples (min_lat, max_lat, min_lon, max_lon)
    for each size_degrees x size_degrees chunk covering the globe.

    Latitude  spans -90 to  90 degrees.
    Longitude spans -180 to 180 degrees.

    Returns:
        list of tuples: [(min_lat, max_lat, min_lon, max_lon), ...]
    """
    ll_limits = []
    if 90 % size_degrees != 0 or 180 % size_degrees != 0:
        raise ValueError(
            f"calc_ll_limits: size_degrees ({size_degrees}) must evenly divide "
            f"both 90 (latitude half-range) and 180 (longitude half-range)."
        )
    lat = -90.0
    while lat < 90.0:
        max_lat = min(lat + size_degrees, 90.0)
        lon = -180.0
        while lon < 180.0:
            max_lon = min(lon + size_degrees, 180.0)
            ll_limits.append((lat, max_lat, lon, max_lon))
            lon = max_lon
        lat = max_lat
    return ll_limits

#--------------------------------------------------------------------------
def _get_year_idx(time_values, year, ncfile, time_units=None):
    """
    Find the time index in a NetCDF time axis for the requested calendar year.
    Handles two common CF-convention unit patterns:

      'years since <epoch>'  -- time_values are year offsets from epoch
      'days since <epoch>'   -- time_values are day offsets from epoch;
                                converted to fractional years via / 365.25

    If time_units is None, time_values are assumed to already be calendar years.
    Raises ValueError if the closest available year is more than 1 year away.

    Args:
        time_values (np.ndarray): 1D array of numeric time values.
        year (int):               Requested calendar year.
        ncfile (Path):            File path, used only in error messages.
        time_units (str|None):    CF-convention units string, e.g.
                                  'years since 850-01-01 0:0:0' or
                                  'days since 1-5-1 00:00:00'.

    Returns:
        int: Index into time_values.
    """
    units_lower = time_units.strip().lower() if time_units else ''

    if units_lower.startswith('years since'):
        # time_values are year offsets from epoch_year
        # e.g. 'years since 850-01-01' -> epoch_year=850; value 1160 -> year 2010
        epoch_str  = time_units.strip().split('since', 1)[1].strip()
        epoch_year = int(epoch_str.split('-')[0])
        search_value = year - epoch_year
        # convert matched offset back to calendar year for error message
        def _to_cal(offset): return offset + epoch_year

    elif units_lower.startswith('days since'):
        # time_values are day offsets from epoch_year
        # e.g. 'days since 1-5-1 00:00:00' -> epoch_year=1
        # convert both the time axis and the requested year to fractional years from epoch
        epoch_str  = time_units.strip().split('since', 1)[1].strip()
        epoch_year = int(epoch_str.split('-')[0])
        # convert time_values (days) to fractional calendar years
        time_as_years = time_values / 365.25 + epoch_year
        search_value  = float(year)           # search in calendar-year space
        # re-use time_as_years for the distance search below
        year_idx     = int(np.argmin(np.abs(time_as_years - search_value)))
        actual_year  = time_as_years[year_idx]
        if abs(actual_year - year) > 1:
            raise ValueError(
                f"_get_year_idx: Requested year {year} not found in {ncfile} "
                f"(closest available: {actual_year:.0f})"
            )
        return year_idx

    else:
        # assume time_values are already calendar years
        search_value = float(year)
        def _to_cal(offset): return offset

    year_idx    = int(np.argmin(np.abs(time_values - search_value)))
    actual_year = _to_cal(time_values[year_idx])

    if abs(actual_year - year) > 1:
        raise ValueError(
            f"_get_year_idx: Requested year {year} not found in {ncfile} "
            f"(closest available: {actual_year:.0f})"
        )
    return year_idx


#--------------------------------------------------------------------------
def _get_modis_tile_idx(lon, lat):
    """
    Calculate the MODIS tile indices (h, v) for a given longitude and latitude.
    The MODIS Sinusoidal grid divides the globe into 36 horizontal (h) and 18 vertical (v) tiles,
    each approximately 10 degrees in size. 
    The tile indices start at (h=0, v=0) in the upper left corner (90N, 180W) and increase to the right and downward.

    Returns:
        tuple: (h, v) tile indices for the given longitude and latitude.
    """
    # Standard MODIS Sinusoidal parameters
    R = 6371007.181
    W = 2 * math.pi * R
    T = W / 36
    
    # Project Lat/Lon to Sinusoidal
    modis_grid = Proj(f'+proj=sinu +R={R} +nadgrids=@null +wktext')
    x, y = modis_grid(lon, lat)
    
    # Calculate tile indices
    h = int((x + W / 2) / T)
    v = int((W / 4 - y) / T)
    return h, v


#--------------------------------------------------------------------------
def get_modis_tile_idxs_ll(ll_limits):
    """
    Get the MODIS tile indices for a lat/lon bounding box.

    Args:
        ll_limits (tuple/list): 4-element (min_lat, max_lat, min_lon, max_lon).

    Returns:
        a cvs string of indices in modis name format: 'h##v##, ...' for the tiles that intersect the bounding box.
    """
    if ll_limits is None or len(ll_limits) != 4:
        raise ValueError(
            f"get_modis_tile_idxs_ll: ll_limits must be a 4-element (min_lat, max_lat, min_lon, max_lon), got {ll_limits}"
        )

    min_lat, max_lat, min_lon, max_lon = [float(x) for x in ll_limits]
    if not (-90.0 <= min_lat <= 90.0 and -90.0 <= max_lat <= 90.0):
        raise ValueError(
            f"get_modis_tile_idxs_ll: invalid latitude bounds {ll_limits}. "
            "Expected (min_lat, max_lat, min_lon, max_lon) with lat in [-90, 90]."
        )
    if not (-180.0 <= min_lon <= 180.0 and -180.0 <= max_lon <= 180.0):
        raise ValueError(
            f"get_modis_tile_idxs_ll: invalid longitude bounds {ll_limits}. "
            "Expected (min_lat, max_lat, min_lon, max_lon) with lon in [-180, 180]."
        )
    if min_lat > max_lat or min_lon > max_lon:
        raise ValueError(
            f"get_modis_tile_idxs_ll: non-monotonic bounds {ll_limits}. "
            "Expected min <= max for both lat and lon."
        )

    corners = [(min_lon, min_lat), (min_lon, max_lat), (max_lon, min_lat), (max_lon, max_lat)]
    tile_idxs = [_get_modis_tile_idx(lon, lat) for lon, lat in corners]
    min_h = min(h for h, v in tile_idxs)
    max_h = max(h for h, v in tile_idxs)
    min_v = min(v for h, v in tile_idxs)
    max_v = max(v for h, v in tile_idxs)
    tile_idxs = [(h, v) for h in range(min_h, max_h + 1) for v in range(min_v, max_v + 1)]
    tile_strs = sorted(f"h{h:02d}v{v:02d}" for h, v in set(tile_idxs))
    return ", ".join(tile_strs)


#--------------------------------------------------------------------------
def _granule_hv_tag(granule):
    """Extract MODIS h##v## tile tag from an earthaccess granule-like object."""
    texts = [str(granule)]
    try:
        links = granule.data_links() if hasattr(granule, 'data_links') else []
        texts.extend([str(x) for x in links])
    except Exception:
        pass
    for txt in texts:
        m = re.search(r'h\d{2}v\d{2}', txt, flags=re.IGNORECASE)
        if m:
            return m.group(0).lower()
    return None


#--------------------------------------------------------------------------
def read_modis_ll_to_geotiff(year, dir_path, product, variable_names=None, ll_limits=None):
    """
    Download MODIS HDF tiles for a given year, mosaic them, and convert each
    requested variable to a GeoTIFF file in dir_path.

    Args:
        year (int): Year to extract.
        dir_path (str or Path): Directory for downloaded HDF files and output GeoTIFFs.
                                If called from a worker process, use a unique per-worker
                                temp directory to avoid file collisions.
                                The caller is responsible for removing this directory
                                after processing is complete.
        product (str): MODIS product identifier (e.g. 'MCD12Q1.061').
        variable_names (list): Variable (subdataset) names to convert. Must be provided.
        ll_limits (tuple/list or None): 4-element (min_lat, max_lat, min_lon, max_lon).
                                        When given, only the lat/lon rows/columns that overlap this region are read.
                                        The slice is inclusive — any grid cell whose coordinate falls within
                                        [min_lat, max_lat] x [min_lon, max_lon] is included.
    Returns:
        dict: {varname: Path} mapping each variable name to the Path of the GeoTIFF
              written to dir_path.
    """

    ###### need an Earthdata username and password to download MODIS data;
    # user must set up a .netrc file in their home directory (~/.netrc) with their credentials on one line, e.g.:
    # machine urs.earthdata.nasa.gov login <myusername> password <mypassword>
    # and set the file permissions to user read/write only (chmod 600 ~/.netrc) to protect their credentials

    from pymodis import downmodis, modis_mosaic, modis_convert  # noqa: PLC0415

    dir_path = Path(dir_path)
    output_dir = dir_path   # GeoTIFFs are written to the same directory as the downloaded HDF files

    # Validate and unpack ll_limits as (min_lat, max_lat, min_lon, max_lon).
    # Earthaccess uses a different ordering for bounding_box (lon, lat, lon, lat).
    if ll_limits is not None:
        min_lat, max_lat, min_lon, max_lon = ll_limits
        if not (-90.0 <= float(min_lat) <= 90.0 and -90.0 <= float(max_lat) <= 90.0):
            raise ValueError(
                f"read_modis_ll_to_geotiff: invalid latitude bounds in ll_limits {ll_limits}. "
                "Expected (min_lat, max_lat, min_lon, max_lon)."
            )
        if not (-180.0 <= float(min_lon) <= 180.0 and -180.0 <= float(max_lon) <= 180.0):
            raise ValueError(
                f"read_modis_ll_to_geotiff: invalid longitude bounds in ll_limits {ll_limits}. "
                "Expected (min_lat, max_lat, min_lon, max_lon)."
            )
        if float(min_lat) > float(max_lat) or float(min_lon) > float(max_lon):
            raise ValueError(
                f"read_modis_ll_to_geotiff: non-monotonic ll_limits {ll_limits}. "
                "Expected min <= max for both lat and lon."
            )
    else:
        min_lat = max_lat = min_lon = max_lon = None

    # latest_date and earliest_date are in 'YYYY-MM-DD' format
    latest_date = f"{year}-12-31"
    earliest_date = f"{year}-01-01"

    # Parse product short name and version from 'MCD12Q1.061' -> ('MCD12Q1', '061')
    product_parts = product.split('.')
    short_name = product_parts[0]
    version = product_parts[1] if len(product_parts) > 1 else '061'

    # Download HDF granules via NASA Earthdata Cloud (earthaccess).
    # The old LP DAAC HTTPS server (e4ftl01.cr.usgs.gov) decommissioned MODIS
    # directories in 2024; earthaccess is the current NASA-recommended approach.
    # Credentials are read from ~/.netrc (machine urs.earthdata.nasa.gov).
    logger.info(
        f"read_modis_ll_to_geotiff: searching Earthdata for {product}  year={year}  "
        f"ll_limits(min_lat,max_lat,min_lon,max_lon)={ll_limits}"
    )

    try:
        earthaccess.login(strategy='netrc')
    except Exception as e:
        raise RuntimeError(
            f"read_modis_ll_to_geotiff: Earthdata login failed. "
            f"Ensure ~/.netrc contains credentials for urs.earthdata.nasa.gov: {e}"
        ) from e

    search_kwargs = {
        'short_name': short_name,
        'version': version,
        'temporal': (earliest_date, latest_date),
    }
    if ll_limits is not None:
        # earthaccess bounding_box: (min_lon, min_lat, max_lon, max_lat)
        # Round to 6 decimal places to avoid scientific notation (e.g. 3.68e-14)
        # which the CMR API rejects as an invalid bounding_box value.
        search_kwargs['bounding_box'] = (
            round(float(min_lon), 6),
            round(float(min_lat), 6),
            round(float(max_lon), 6),
            round(float(max_lat), 6),
        )
        logger.info(
            "read_modis_ll_to_geotiff: Earthaccess bounding_box(min_lon,min_lat,max_lon,max_lat)="
            f"{search_kwargs['bounding_box']}"
        )

    granules = earthaccess.search_data(**search_kwargs)

    # CMR bbox filtering can return zero granules for very small boxes even when
    # intersecting MODIS tiles exist. Retry by tile tags as a robust fallback.
    if not granules and ll_limits is not None:
        target_tiles = {
            t.strip().lower()
            for t in get_modis_tile_idxs_ll(ll_limits).split(',')
            if t.strip()
        }
        logger.warning(
            "read_modis_ll_to_geotiff: no granules from bbox search; retrying tile-filtered search "
            f"for tiles={sorted(target_tiles)}, {product}, "
            f"bounding_box(min_lon,min_lat,max_lon,max_lat)={search_kwargs['bounding_box']}"
        )
        broad_granules = earthaccess.search_data(
            short_name=short_name,
            version=version,
            temporal=(earliest_date, latest_date),
        )
        granules = [
            g for g in broad_granules
            if (_granule_hv_tag(g) in target_tiles)
        ]
        logger.info(
            f"read_modis_ll_to_geotiff: tile-filtered search found, {product}, "
            f"{len(granules)} granule(s) from {len(broad_granules)} broad candidates"
            f"bounding_box(min_lon,min_lat,max_lon,max_lat)={search_kwargs['bounding_box']}"
        )

    if not granules:
        logger.warning(
            f"read_modis_ll_to_geotiff: no granules found for {product} "
            f"temporal={earliest_date}..{latest_date}  bounding_box={search_kwargs.get('bounding_box')}  "
            f"ll_limits={ll_limits}; skipping this chunk for {product}"
        )
        return {}
    logger.info(f"read_modis_ll_to_geotiff: found {len(granules)} granules for {product}; downloading to {dir_path}")

    try:
        downloaded = earthaccess.download(granules, local_path=str(dir_path))
    except Exception as e:
        raise RuntimeError(
            f"read_modis_ll_to_geotiff: MODIS download failed for product '{product}', year {year}: {e}"
        ) from e

    # Collect downloaded HDF files for this specific product/version only.
    # The same temp directory is reused across product calls, so we must not
    # mix, for example, MCD12Q1 tiles into a MOD44B read.
    hdf_files = []
    for f in sorted(dir_path.glob('*.[hH][dD][fF]')):
        f_name_upper = f.name.upper()
        if not f_name_upper.startswith(f"{short_name.upper()}."):
            continue
        if len(product_parts) > 1 and f".{version.upper()}." not in f_name_upper:
            continue
        hdf_files.append(str(f))
    if not hdf_files:
        raise RuntimeError(
            f"read_modis_ll_to_geotiff: no HDF files for {product} found in {dir_path} after download"
        )
    logger.info(
        f"read_modis_ll_to_geotiff: {len(hdf_files)} HDF tile(s) for {product} to process"
    )

    # Build per-variable GeoTIFFs using GDAL directly
    # gdal.BuildVRT mosaics the per-tile subdatasets; gdal.Warp reprojects and
    # clips to ll_limits.  This avoids pymodis's requirement for .hdf.xml sidecars.

    out = {}
    for var in variable_names:
        var_norm = ''.join(ch for ch in var.lower() if ch.isalnum())
        # Find the matching HDF4_EOS subdataset path in each tile
        sds_paths = []
        for hdf_file in hdf_files:
            ds_hdf = gdal.Open(hdf_file)
            if ds_hdf is None:
                raise RuntimeError(
                    f"read_modis_ll_to_geotiff: GDAL could not open {hdf_file}"
                )
            matched = None
            for sds_name, sds_desc in ds_hdf.GetSubDatasets():
                sds_name_norm = ''.join(ch for ch in sds_name.lower() if ch.isalnum())
                sds_desc_norm = ''.join(ch for ch in sds_desc.lower() if ch.isalnum())
                if var_norm in sds_name_norm or var_norm in sds_desc_norm:
                    matched = sds_name
                    break
            ds_hdf = None  # close
            if matched is None:
                raise RuntimeError(
                    f"read_modis_ll_to_geotiff: variable '{var}' not found in {hdf_file}"
                )
            sds_paths.append(matched)

        output_tif = output_dir / f"{var}_{year}.tif"
        warp_kwargs = dict(
            format='GTiff',
            dstSRS='EPSG:4326',
            resampleAlg=gdal.GRA_NearestNeighbour,
        )
        if ll_limits is not None:
            min_lat, max_lat, min_lon, max_lon = ll_limits
            warp_kwargs['outputBounds'] = (min_lon, min_lat, max_lon, max_lat)
            warp_kwargs['outputBoundsSRS'] = 'EPSG:4326'

        if len(sds_paths) == 1:
            # Single tile — Warp directly from the subdataset path
            gdal.Warp(str(output_tif), sds_paths[0], **warp_kwargs)
        else:
            # Multiple tiles — build an in-memory VRT mosaic, then Warp
            vrt_path = str(output_dir / f"{var}_{year}_mosaic.vrt")
            vrt = gdal.BuildVRT(vrt_path, sds_paths)
            vrt.FlushCache()
            vrt = None  # close before Warp reads it
            try:
                gdal.Warp(str(output_tif), vrt_path, **warp_kwargs)
            finally:
                Path(vrt_path).unlink(missing_ok=True)

        if not output_tif.exists():
            raise RuntimeError(
                f"read_modis_ll_to_geotiff: GeoTIFF not created for {product} '{var}' at {output_tif}"
            )
        logger.info(f"read_modis_ll_to_geotiff: wrote {product} {output_tif}")
        out[var] = output_tif

    return out





###todo: finish and test read_netcdf general function


#--------------------------------------------------------------------------
def read_netcdf_ll(year, file_path_name, variable_names=None, ll_limits=None):
    """
    Read variables from a NetCDF file for a given year.

    Args:
        year (int): Year to extract.
        file_path_name (str or Path): Full path to the NetCDF file.
        variable_names (list or None): Variables to extract. Must be provided.
        ll_limits (tuple/list or None): 4-element (min_lat, max_lat, min_lon, max_lon).
                                        When given, only the lat/lon rows/columns that
                                        overlap this region are read.  The slice is
                                        inclusive — any grid cell whose coordinate falls
                                        within [min_lat, max_lat] x [min_lon, max_lon]
                                        is included, so cells that straddle the boundary
                                        are never dropped.

    Returns:
        dict: {varname: 2D np.ndarray shape (lat, lon)} for the requested year,
              plus 'lat' and 'lon' coordinate arrays (possibly subsetted).
    """

    ncfile = Path(file_path_name)
    if not ncfile.exists():
        raise FileNotFoundError(f"NetCDF file not found: {ncfile}")

    ds = xr.open_dataset(ncfile, decode_times=False)

    if variable_names is None:
        # Raise an error
        # consider reading all variables in file instead of raising an error?
        raise KeyError(f"read_netcdf_ll: Variable names must be provided in the json input file for {ncfile}. "
                       f"Available variables: {list(ds.data_vars)}")

    time_units = ds['time'].attrs.get('units', None)
    # _get_year_idx() handles both 'years since' and 'days since' patterns,
    #    as well as the case of no time units (assumed calendar years)
    # add cases to _get_year_idx as needed if other time unit patterns are encountered in source data
    year_idx = _get_year_idx(ds['time'].values, year, ncfile, time_units=time_units)
    logger.info(f"read_netcdf_ll: reading year {year} (time index {year_idx}) from {ncfile}")

    ####todo: this currently assumes that the variables lat/lon are ~cell centers
    # need to allow for other variable names to represent these coordinates 

    # if ll_limits=None, do not subset the data; otherwise subset by ll_limits
    if ll_limits is not None:
        min_lat, max_lat, min_lon, max_lon = ll_limits
        lat_vals = ds['lat'].values
        lon_vals = ds['lon'].values

        # add a one-cell buffer so cells that straddle the boundary are included
        lat_step = float(abs(lat_vals[1] - lat_vals[0])) if lat_vals.size > 1 else 0.0
        lon_step = float(abs(lon_vals[1] - lon_vals[0])) if lon_vals.size > 1 else 0.0

        lat_mask = (lat_vals >= min_lat - lat_step) & (lat_vals <= max_lat + lat_step)
        lon_mask = (lon_vals >= min_lon - lon_step) & (lon_vals <= max_lon + lon_step)

        lat_idx = np.where(lat_mask)[0]
        lon_idx = np.where(lon_mask)[0]

        if lat_idx.size == 0 or lon_idx.size == 0:
            raise ValueError(
                f"read_netcdf_ll: No grid cells found within ll_limits {ll_limits} in {ncfile}. "
                f"lat range: [{lat_vals.min():.2f}, {lat_vals.max():.2f}], "
                f"lon range: [{lon_vals.min():.2f}, {lon_vals.max():.2f}]"
            )

        lat_dim = ds['lat'].dims[0]
        lon_dim = ds['lon'].dims[0]
        ds = ds.isel({lat_dim: lat_idx, lon_dim: lon_idx})

    out = {'lat': ds['lat'].values, 'lon': ds['lon'].values}
    for v in variable_names:
        if v not in ds:
            raise KeyError(f"read_netcdf_ll: Variable '{v}' not found in {ncfile}. "
                           f"Available variables: {list(ds.data_vars)}")
        out[v] = ds[v].isel(time=year_idx).values  # shape: (lat, lon)

    ds.close()
    return out

#--------------------------------------------------------------------------
def write_latlon_to_geotiff(data_2d, lat, lon, ll_limits, tmp_path):
    """
    Slice a 2D (lat, lon) source array to a lat-lon bounding box and write
    it to a GeoTIFF file for use as uraster input.

    Args:
        data_2d (np.ndarray): 2D array shape (n_lat, n_lon), global coverage.
        lat (np.ndarray):     1D latitude coordinate array, degrees, south-to-north.
        lon (np.ndarray):     1D longitude coordinate array, degrees, west-to-east.
        ll_limits (tuple):    (min_lat, max_lat, min_lon, max_lon) for this chunk.
        tmp_path (str|Path):  Full path of the GeoTIFF file to write.

    Returns:
        Path: Path to the written GeoTIFF.
    """
    min_lat, max_lat, min_lon, max_lon = ll_limits

    # find row/col indices that fall within the bounding box
    # add a 1-cell buffer on each side to avoid edge interpolation artefacts
    lat_step = float(lat[1] - lat[0])
    lon_step = float(lon[1] - lon[0])
    # 1-cell buffer for floating-point safety — ensures the outermost raster
    # pixels are not accidentally clipped by strict inequality comparisons.
    # Chunk boundary artifacts are avoided upstream by using tight vertex
    # bounding boxes (from set_decomp_cell_idx_ll_limits) as ll_limits, so a
    # large buffer is not needed here.
    buffer_cells = 1
    lat_mask = (lat >= min_lat - buffer_cells * abs(lat_step)) & (lat <= max_lat + buffer_cells * abs(lat_step))
    lon_mask = (lon >= min_lon - buffer_cells * abs(lon_step)) & (lon <= max_lon + buffer_cells * abs(lon_step))

    lat_idx = np.where(lat_mask)[0]
    lon_idx = np.where(lon_mask)[0]

    if lat_idx.size == 0 or lon_idx.size == 0:
        raise ValueError(
            f"write_latlon_to_geotiff: No source grid cells found within ll_limits {ll_limits}. "
            f"lat range: [{lat.min():.2f}, {lat.max():.2f}], "
            f"lon range: [{lon.min():.2f}, {lon.max():.2f}]"
        )

    # slice the data
    chunk_lat = lat[lat_idx]
    chunk_lon = lon[lon_idx]
    chunk_data = data_2d[np.ix_(lat_idx, lon_idx)].astype(np.float32)

    # rasterio uses (west, south, east, north) bounds
    # Use min/max rather than first/last element so this is correct for both
    # south-to-north (e.g. some grids) and north-to-south (HYDE3.5, LUH2) lat arrays.
    west  = float(chunk_lon.min()) - abs(lon_step) / 2.0
    east  = float(chunk_lon.max()) + abs(lon_step) / 2.0
    south = float(chunk_lat.min()) - abs(lat_step) / 2.0
    north = float(chunk_lat.max()) + abs(lat_step) / 2.0

    n_rows, n_cols = chunk_data.shape
    transform = from_bounds(west, south, east, north, n_cols, n_rows)

    tmp_path = Path(tmp_path)
    with rasterio.open(
        tmp_path,
        'w',
        driver='GTiff',
        height=n_rows,
        width=n_cols,
        count=1,
        dtype=np.float32,
        crs='EPSG:4326',
        transform=transform,
        nodata=np.nan,
        ) as dst:
        # rasterio band 1 is row-ordered north-to-south; flip if lat is south-to-north
        if chunk_lat[0] < chunk_lat[-1]:
            dst.write(np.flipud(chunk_data), 1)
        else:
            dst.write(chunk_data, 1)

    logger.info(f"write_latlon_to_geotiff: wrote {n_rows}x{n_cols} chunk to {tmp_path}")
    return tmp_path


#--------------------------------------------------------------------------
def write_mesh_to_geojson(out_grid_data, tmp_path, cell_indices=None):
    """
    Write mesh cells from a GridData object to a GeoJSON file.

    Builds polygon geometries directly from the vertex coordinate arrays
    (lon_vtx, lat_vtx) stored in out_grid_data

    Args:
        out_grid_data (GridData): Populated grid geometry object whose arrays
                                  have been filled (e.g. via load_mesh_nc).
        tmp_path (str|Path):      Full path of the output GeoJSON file to write.
                                  Parent directories are created if needed.
        cell_indices (tuple|array-like|None): 0-based indices into the
                                  out_grid_data arrays selecting which cells to
                                  write.  Pass None to write all cells.

    Returns:
        Path: Path to the written GeoJSON file.
    """
    if cell_indices is None:
        idx = np.arange(out_grid_data.num_cells, dtype=np.intp)
    else:
        idx = np.asarray(cell_indices, dtype=np.intp)

    if idx.size == 0:
        raise ValueError("write_mesh_to_geojson: cell_indices is empty — no cells to write")

    cell_ids = out_grid_data.cell_id[idx]   # (n,)
    lon_vtx  = out_grid_data.lon_vtx[idx]   # (n, n_vertices)
    lat_vtx  = out_grid_data.lat_vtx[idx]   # (n, n_vertices)

    features = []
    for i in range(len(idx)):
        # GeoJSON polygon ring: list of [lon, lat] pairs, closed (first == last)
        ring = [[float(lon_vtx[i, v]), float(lat_vtx[i, v])]
                for v in range(lon_vtx.shape[1])]
        ring.append(ring[0])   # close the ring

        features.append({
            'type': 'Feature',
            'geometry': {
                'type': 'Polygon',
                'coordinates': [ring],
            },
            'properties': {'cellid': int(cell_ids[i])},
        })

    geojson = {'type': 'FeatureCollection', 'features': features}
    tmp_path = Path(tmp_path)
    tmp_path.parent.mkdir(parents=True, exist_ok=True)
    with open(tmp_path, 'w') as f:
        json.dump(geojson, f)

    logger.info(f"write_mesh_to_geojson: wrote {len(features)} cells to {tmp_path}")
    return tmp_path

#--------------------------------------------------------------------------
def regrid_to_mesh(mesh_file_path, var_file_path, cell_indices, out_grid_data,
                   out_type='path', remap_method=3):
    """
    Regrid a single raster variable onto the landgen mesh cells for one chunk,
    using uraster.  Inputs are already-written files (mesh GeoJSON + source
    raster GeoTIFF), so no temp-file management is needed here.

    Args:
        mesh_file_path (str|Path): Full path to the chunk mesh GeoJSON written
                               by write_mesh_to_geojson.
        var_file_path (dict):  One-element dict {'<varname>': Path} pointing to
                               the source raster GeoTIFF for this variable.
        cell_indices (tuple):  0-based indices into out_grid_data arrays for
                               the cells in this chunk.
        out_grid_data (GridData): Grid geometry object; its cell_id array is used
                               to map cellid values to output order when
                               out_type='data'.
        out_type (str):        'path' — return Path of the uraster output GeoJSON.
                               'data' — return 1D np.ndarray of mean values
                                        aligned to cell_indices order.
        remap_method (int):    uraster iFlag_remap_method
                               (1=nearest, 2=nearest, 3=weighted average).
                               Default 3 is correct for area-fraction data.

    Returns:
        Path       if out_type='path': Path to the uraster output GeoJSON.
        np.ndarray if out_type='data': 1D float64 array of length
                   len(cell_indices), regridded values in the same order
                   as cell_indices.
    """
    if out_type not in ('path', 'data'):
        raise ValueError(f"regrid_to_mesh: out_type must be 'path' or 'data', got '{out_type}'")

    mesh_path   = Path(mesh_file_path)
    varname     = next(iter(var_file_path.keys()))
    raster_path = Path(next(iter(var_file_path.values())))

    if not mesh_path.exists():
        raise FileNotFoundError(f"regrid_to_mesh: mesh file not found: {mesh_path}")
    if not raster_path.exists():
        raise FileNotFoundError(f"regrid_to_mesh: raster file not found: {raster_path}")

    # output file sits next to the mesh file
    out_path = mesh_path.parent / f"{varname}.geojson"

    config = {
        'sFilename_source_mesh':   str(mesh_path),
        'aFilename_source_raster': [str(raster_path)],
        'sFilename_target_mesh':   str(out_path),
        'iFlag_remap_method':      remap_method,
        'sField_unique_id':        'cellid',
        'iFlag_global':            0,
        'iFlag_polar':             0,
    }
    try:
        processor = URaster(config)
        processor.setup()
        processor.run_remap()
    except Exception as e:
        raise RuntimeError(
            f"regrid_to_mesh: uraster failed for '{varname}' "
            f"(mesh={mesh_path}, raster={raster_path}): {e}"
        ) from e

    if not out_path.exists():
        raise RuntimeError(
            f"regrid_to_mesh: uraster completed but output not found: {out_path}")

    if out_type == 'path':
        return out_path

    # out_type == 'data': read output GeoJSON and align values to cell_indices order
    with open(out_path, 'r') as f:
        geojson = json.load(f)

    result_map = {}
    for feature in geojson['features']:
        props = feature['properties']
        cid = int(props['cellid'])
        val = props.get('mean', None)
        result_map[cid] = float(val) if (val is not None and not np.isnan(val)) else 0.0

    # use out_grid_data.cell_id to map chunk positions -> cellid values -> result values
    chunk_cell_ids = out_grid_data.cell_id[np.asarray(cell_indices, dtype=np.intp)]
    out = np.array([result_map.get(int(cid), 0.0) for cid in chunk_cell_ids],
                   dtype=np.float64)

    logger.info(f"regrid_to_mesh: '{varname}' -> {len(out)} cells, "
                f"non-zero: {np.count_nonzero(out)}")
    return out


##### todo: need to set units an other attributres on the output variables here; currently just copying the raw values without metadata

#--------------------------------------------------------------------------
def write_module_netcdf(out_grid_data, module_data, out_path, file_name,
                        year=None, timevars=None, varnames=None, ll_limits=None):
    """
    Write a NetCDF file containing grid geometry from out_grid_data plus
    selected variables from one module data object (TopoData, LtData, etc.).

    Grid variables written (from out_grid_data):
        cell_id (coordinate), cell_area, landfrac, lon_xy, lat_xy,
        lon_vtx, lat_vtx

    Module variables written:
        all non-None array attributes of module_data, or only those listed in
        varnames if provided.  cell_idx is always skipped (internal bookkeeping).
        Variables in timevars get a leading unlimited 'time' dimension so that
        per-year files can be concatenated later with ncrcat or
        xarray.open_mfdataset(..., concat_dim='time').

    Spatial subsetting:
        If ll_limits=(min_lat, max_lat, min_lon, max_lon) is given, only the
        cells whose centre coordinates (out_grid_data.lat_xy / lon_xy) fall
        within the bounding box are written.  Otherwise all cells are written.

    Args:
        out_grid_data (GridData):   Populated grid geometry object.
        module_data (object):       Any per-cell data object (TopoData, LtData,
                                    …) — NOT a BaseManager-derived class.
        out_path (str|Path):        Directory in which to write the file.
        file_name (str):            NetCDF filename (including extension).
        year (int|None):            Calendar year for this record.  When given,
                                    a 'time' coordinate with value=year is added
                                    and all timevars receive a leading time dim.
        timevars (list[str]|None):  Variable names that receive a leading
                                    unlimited 'time' dimension.  Ignored when
                                    year=None.  None → no variables get a time dim.
        varnames (list[str]|None):  Variables to write from module_data.
                                    None → write all non-None array attributes.
        ll_limits (tuple|None):     (min_lat, max_lat, min_lon, max_lon) spatial
                                    subset.  None → write all cells.

    Returns:
        Path: Path to the written NetCDF file.
    """
    out_path = Path(out_path)
    out_path.mkdir(parents=True, exist_ok=True)
    nc_path = out_path / file_name

    n_cells  = out_grid_data.num_cells
    time_set = set(timevars) if (timevars and year is not None) else set()

    # --- determine which cells to write ---
    if ll_limits is not None:
        min_lat, max_lat, min_lon, max_lon = ll_limits
        mask = (
            (out_grid_data.lat_xy >= min_lat) & (out_grid_data.lat_xy <= max_lat) &
            (out_grid_data.lon_xy >= min_lon) & (out_grid_data.lon_xy <= max_lon)
        )
        idx = np.where(mask)[0]
    else:
        idx = np.arange(n_cells, dtype=np.intp)

    n_out = len(idx)
    if n_out == 0:
        raise ValueError(f"write_module_netcdf: no cells found within ll_limits {ll_limits}")

    # --- collect variables to write from module_data ---
    _skip = {'lock', 'cell_idx'}
    if varnames is None:
        varnames_to_write = [
            k for k, v in vars(module_data).items()
            if k not in _skip and isinstance(v, np.ndarray)
        ]
    else:
        varnames_to_write = list(varnames)

    # --- build xarray Dataset ---
    cell_dim = 'cell'
    vtx_dim  = 'vertex'
    time_dim = 'time'

    # time coordinate (size-1 so the unlimited dim exists for concatenation)
    coords = {
        'cell_id': xr.DataArray(out_grid_data.cell_id[idx], dims=[cell_dim],
                                attrs={'long_name': 'cell identifier'}),
    }
    if year is not None:
        coords['time'] = xr.DataArray(
            np.array([year], dtype=np.int32), dims=[time_dim],
            attrs={'long_name': 'year', 'units': 'calendar year'}
        )

    data_vars = {
        'cell_area': xr.DataArray(out_grid_data.cell_area[idx], dims=[cell_dim],
                                  attrs={'long_name': 'cell area'}),
        'landfrac':  xr.DataArray(out_grid_data.landfrac[idx],  dims=[cell_dim],
                                  attrs={'long_name': 'land fraction'}),
        'lon_xy':    xr.DataArray(out_grid_data.lon_xy[idx],    dims=[cell_dim],
                                  attrs={'long_name': 'cell-centre longitude', 'units': 'degrees_east'}),
        'lat_xy':    xr.DataArray(out_grid_data.lat_xy[idx],    dims=[cell_dim],
                                  attrs={'long_name': 'cell-centre latitude',  'units': 'degrees_north'}),
        'lon_vtx':   xr.DataArray(out_grid_data.lon_vtx[idx],  dims=[cell_dim, vtx_dim],
                                  attrs={'long_name': 'vertex longitudes', 'units': 'degrees_east'}),
        'lat_vtx':   xr.DataArray(out_grid_data.lat_vtx[idx],  dims=[cell_dim, vtx_dim],
                                  attrs={'long_name': 'vertex latitudes',  'units': 'degrees_north'}),
    }

    # add module variables
    for name in varnames_to_write:
        val = getattr(module_data, name, None)
        if val is None or not isinstance(val, np.ndarray):
            continue
        arr = val[idx] if val.shape[0] == n_cells else val

        # build spatial dimension names (no time prefix yet)
        if arr.ndim == 1:
            dims = [cell_dim]
        elif arr.ndim == 2:
            dims = [cell_dim, f'{name}_dim1']
        elif arr.ndim == 3:
            dims = [cell_dim, f'{name}_dim1', f'{name}_dim2']
        else:
            dims = [cell_dim] + [f'{name}_dim{i}' for i in range(1, arr.ndim)]

        if name in time_set:
            # prepend unlimited time dim with a size-1 axis
            arr  = arr[np.newaxis, ...]   # (1, cell, ...)
            dims = [time_dim] + dims
        data_vars[name] = xr.DataArray(arr, dims=dims)

    ds = xr.Dataset(data_vars=data_vars, coords=coords)
    # declare 'time' as the unlimited dimension so ncrcat / concat works
    encoding = {time_dim: {'unlimited': True}} if year is not None else {}
    ds.to_netcdf(nc_path, unlimited_dims=[time_dim] if year is not None else [])

    logger.info(f"write_module_netcdf: wrote {n_out} cells, "
                f"{len(varnames_to_write)} module vars to {nc_path}"
                + (f" (year {year}, {len(time_set)} timevars)" if year is not None else ""))
    return nc_path
