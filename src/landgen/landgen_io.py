# landgen_io.py
# Utility functions for reading harvest data from LUH2 and grazing data from HYDE3.5

import xarray as xr
import numpy as np
from pathlib import Path
import pandas as pd
import rasterio
from rasterio.transform import from_bounds
import json
import shapely.wkb
from uraster.classes.uraster import uraster as URaster
import glob
import os
import math
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
    """
    nc_path = Path(nc_path_name)
    if not nc_path.exists():
        raise FileNotFoundError(f"Mesh NetCDF file not found: {nc_path}")

    # read the full file once — this is a one-time setup call
    # note that lat and lon are approximate cell centers
    ds      = xr.open_dataset(nc_path, decode_times=False)
    lat     = ds['lat'].values.astype(np.float64)    # (n_cells,)
    lon     = ds['lon'].values.astype(np.float64)    # (n_cells,)
    xv      = ds['xv'].values.astype(np.float64)     # (n_cells, n_vertices)
    yv      = ds['yv'].values.astype(np.float64)     # (n_cells, n_vertices)
    cellids = ds['cellid'].values.astype(np.int64)   # (n_cells,) — HEALPix cell IDs
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

        # Store HEALPix cellid values (not NC row indices) so callers can use them
        # directly as cell identifiers.  Row indices are only needed here to index
        # into xv/yv; they are kept in the companion .npz for load_mesh_nc().
        decomp_indices.append(tuple(cellids[indices].tolist()))
        decomp_ll_limits.append((
            float(yv_chunk.min()), float(yv_chunk.max()),
            float(xv_chunk.min()), float(xv_chunk.max()),
        ))

        # companion file stores NC row indices (used by load_mesh_nc via ds.isel())
        key = f"{min_lat:.0f}_{max_lat:.0f}_{min_lon:.0f}_{max_lon:.0f}"
        index[key] = indices

    # write companion file to out_dir (if given) or alongside the mesh NC
    npz_name = Path(nc_path).stem + '.spatial_index.npz'
    npz_dir = Path(out_dir) if out_dir is not None else Path(nc_path).parent
    out_path = npz_dir / npz_name
    try:
        np.savez_compressed(out_path, **index)
        print(f"  set_decomp_cell_idx_ll_limits: wrote {len(index)} chunks to {out_path}")
    except PermissionError:
        print(f"  set_decomp_cell_idx_ll_limits: WARNING — could not write companion file to {out_path} (permission denied, skipping)")
        out_path = None

    print(f"  set_decomp_cell_idx_ll_limits: built {len(decomp_indices)} chunks from {nc_path}")
    print(f"  set_decomp_cell_idx_ll_limits: chunks include {sum(len(t) for t in decomp_indices)} cells")

    return out_path

#--------------------------------------------------------------------------
def load_mesh_nc(nc_path_name, indices=None, ll_limits=None):
    """
    Load HEALPix mesh data from a NetCDF domain file.

    Args:
        nc_path_name (str|Path):      Path (and name) to the domain NetCDF file.
        indices (tuple|None): 1D tuple of NC indices to load,
                                   as returned in chunk_indices by
                                   set_chunk_cell_idx_ll_limits().  If None,
                                   check for ll_limits.
        ll_limits (tuple|None): (min_lat, max_lat, min_lon, max_lon) tuple
                                to select cells by mapping file.  If also None,
                                no lat/lon filtering is applied. This option
                                uses the companion .npz spatial mapping file
                                created by set_chunk_cell_idx_ll_limits().

    Returns:
        dict with keys 'cellid', 'xv', 'yv', 'lon', 'lat' (all np.ndarray).
    """
    nc_path = Path(nc_path_name)
    if not nc_path.exists():
        raise FileNotFoundError(f"Mesh NetCDF file not found: {nc_path}")

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
                f"Spatial index not found: {idx_path}. "
                f"Run set_chunk_cell_idx_ll_limits('{nc_path}', chunk_indices, chunk_ll_limits) first."
            )
        indices_ll  = np.load(idx_path)[key]
        if indices_ll.size == 0:
            ds.close()
            raise ValueError(f"No mesh cells found within ll_limits {ll_limits}.")
        cell_dim = ds['lat'].dims[0]
        subset   = ds.isel({cell_dim: indices_ll})
    else:
        subset = ds

    cellid = subset['cellid'].values.astype(np.int64)
    xv     = subset['xv'].values.astype(np.float64)
    yv     = subset['yv'].values.astype(np.float64)
    lon    = subset['lon'].values.astype(np.float64)
    lat    = subset['lat'].values.astype(np.float64)
    ds.close()

    print(f"  load_mesh_nc: loaded {len(cellid)} cells from {nc_path}")
    return {'cellid': cellid, 'xv': xv, 'yv': yv, 'lon': lon, 'lat': lat}



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
            f"size_degrees ({size_degrees}) must evenly divide "
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
                f"Requested year {year} not found in {ncfile} "
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
            f"Requested year {year} not found in {ncfile} "
            f"(closest available: {actual_year:.0f})"
        )
    return year_idx




###todo: write general hdf read function:

#--------------------------------------------------------------------------
def get_modis_tile_idx(lon, lat):
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
    min_lat, max_lat, min_lon, max_lon = ll_limits
    corners = [(min_lon, min_lat), (min_lon, max_lat), (max_lon, min_lat), (max_lon, max_lat)]
    tile_idxs = [get_modis_tile_idx(lon, lat) for lon, lat in corners]
    min_h = min(h for h, v in tile_idxs)
    max_h = max(h for h, v in tile_idxs)
    min_v = min(v for h, v in tile_idxs)
    max_v = max(v for h, v in tile_idxs)
    tile_idxs = [(h, v) for h in range(min_h, max_h + 1) for v in range(min_v, max_v + 1)]
    tile_strs = [f"h{h:02d}v{v:02d}" for h, v in set(tile_idxs)]
    return ", ".join(tile_strs)

#--------------------------------------------------------------------------
def get_sds_string(hdf_path_name, data_name):
    """
    Scans HDF subdatasets and returns a pyModis SDS string.
    Example: '( 0 1 0 0 )' if the 2nd subdataset matches.
    """
    # Open the main HDF file
    ds = gdal.Open(hdf_path_name)
    # GetSubDatasets returns a list of (name, description) tuples
    subdatasets = ds.GetSubDatasets()
    
    sds_bits = []
    for sds_name, description in subdatasets:
        # Check if the target name (e.g., 'LC_Type1') is in the description
        if data_name in description:
            sds_bits.append("1")
        else:
            sds_bits.append("0")
            
    # Format as '( 0 1 0 ... )' as required by modis_convert
    return f"( {' '.join(sds_bits)} )"

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

    # bbox for modis_convert spatial subsetting: (min_lon, max_lon, min_lat, max_lat)
    # None means no spatial subsetting (full tile extent)
    if ll_limits is not None:
        min_lat, max_lat, min_lon, max_lon = ll_limits
        bbox = (min_lon, max_lon, min_lat, max_lat)
    else:
        bbox = None

    # latest_date and earliest_date are in 'YYYY-MM-DD' format
    latest_date = f"{year}-12-31"
    earliest_date = f"{year}-01-01"

    # identify the tile files based on ll_limits and create a string of modis indices for downmodis()
    if ll_limits is not None:
        tiles_str = get_modis_tile_idxs_ll(ll_limits)

        # download a subset of the tiles with downmodis()
        downloader = downmodis.downmodis(
            destinationFolder=dir_path,
            tiles=tiles_str,
            product=product,
            today=latest_date,
            enddate=earliest_date
        )
    else:
        # download all tiles for the year with downmodis()
        downloader = downmodis.downmodis(
            destinationFolder=dir_path,
            product=product,
            today=latest_date,
            enddate=earliest_date
        )

    # Run download
    try:
        downloader.downloads()
    except Exception as e:
        raise RuntimeError(
            f"read_modis_ll_to_geotiff: MODIS download failed for product '{product}', year {year}: {e}"
        ) from e

    # use modis_mosaic() to mosaic the tiles together if more than one file is needed to cover the ll_limits
    os.makedirs(dir_path, exist_ok=True)
    # list of strings of all HDF files downloaded
    hdf_files = [str(f) for f in dir_path.glob('*.[hH][dD][fF]')]
    # Mosaic the tiles (combines tiles for each date)
    mosaic_output = str(dir_path / f'mosaic_{year}.hdf')
    m = modis_mosaic.modis_mosaic(hdf_files, mosaic_output)
    try:
        m.run()
    except Exception as e:
        raise RuntimeError(
            f"read_modis_ll_to_geotiff: MODIS mosaic failed for product '{product}', year {year}: {e}"
        ) from e

    # generate the sds input string for modis_convert() based on the variable_names list

    # use modis_convert() to convert each desired variable to GeoTIFF with ll_limits as the bounding box
    # Convert mosaicked HDF to GeoTIFF and select specific subdataset

    out = {}
    for var in variable_names:
        # get the sds string for this variable
        # Example: '( 0 1 0 0 )' to select the 2nd subdataset matching var
        sds_str = get_sds_string(hdf_files[0], var)
        output_tif = output_dir / f"{var}_{year}.tif"
        converter = modis_convert.modis_convert(
            mosaic_output,
            str(output_dir),
            output_filename=str(output_tif),
            sds=sds_str,           # Example: '( 0 1 0 0 )' to select the 2nd subdataset matching var
            subset=bbox,           # Applies spatial subsetting
            epsg=4326              # Re-projects to WGS84
        )
        try:
            converter.run()
        except Exception as e:
            raise RuntimeError(
                f"read_modis_ll_to_geotiff: MODIS convert failed for variable '{var}', product '{product}', year {year}: {e}"
            ) from e
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
        raise KeyError(f"Variable names must be provided in the json input file for {ncfile}. "
                       f"Available variables: {list(ds.data_vars)}")

    time_units = ds['time'].attrs.get('units', None)
    # _get_year_idx() handles both 'years since' and 'days since' patterns,
    #    as well as the case of no time units (assumed calendar years)
    # add cases to _get_year_idx as needed if other time unit patterns are encountered in source data
    year_idx = _get_year_idx(ds['time'].values, year, ncfile, time_units=time_units)
    print(f"  read_netcdf: reading year {year} (time index {year_idx}) from {ncfile}")

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
                f"No grid cells found within ll_limits {ll_limits} in {ncfile}. "
                f"lat range: [{lat_vals.min():.2f}, {lat_vals.max():.2f}], "
                f"lon range: [{lon_vals.min():.2f}, {lon_vals.max():.2f}]"
            )

        lat_dim = ds['lat'].dims[0]
        lon_dim = ds['lon'].dims[0]
        ds = ds.isel({lat_dim: lat_idx, lon_dim: lon_idx})

    out = {'lat': ds['lat'].values, 'lon': ds['lon'].values}
    for v in variable_names:
        if v not in ds:
            raise KeyError(f"Variable '{v}' not found in {ncfile}. "
                           f"Available variables: {list(ds.data_vars)}")
        out[v] = ds[v].isel(time=year_idx).values  # shape: (lat, lon)

    ds.close()
    return out


#--------------------------------------------------------------------------
def read_luh2_harvest(year, harvest_path, harvest_name, variable_names=None):
    """
    Read LUH2 harvest variables for a given year.

    Args:
        year (int): Year to extract. LUH2 covers 850-2015.
        harvest_path (str or Path): Directory containing the LUH2 NetCDF file
        harvest_name (str): Filename of the LUH2 NetCDF file
        variable_names (list or None): Variables to extract. Defaults to all 5 LUH2_HARVEST_VARS if None.

    Returns:
        dict: {varname: 2D np.ndarray shape (lat, lon)} for the requested year.
              lat/lon coordinate arrays are included as 'lat' and 'lon' keys.
    """
    if variable_names is None:
        from .harvest import LUH2_HARVEST_VARS  # noqa: PLC0415
        variable_names = LUH2_HARVEST_VARS

    ncfile = Path(harvest_path) / harvest_name
    if not ncfile.exists():
        raise FileNotFoundError(f"LUH2 harvest file not found: {ncfile}")

    ds = xr.open_dataset(ncfile, decode_times=False)

    # LUH2 time axis is 'years since 850-01-01'; values are offsets from 850, not calendar years
    time_units = ds['time'].attrs.get('units', None)
    year_idx = _get_year_idx(ds['time'].values, year, ncfile, time_units=time_units)
    print(f"  read_luh2_harvest: reading year {year} (time index {year_idx}) from {ncfile}")

    out = {'lat': ds['lat'].values, 'lon': ds['lon'].values}
    for v in variable_names:
        if v not in ds:
            raise KeyError(f"Variable '{v}' not found in {ncfile}. "
                           f"Available variables: {list(ds.data_vars)}")
        out[v] = ds[v].isel(time=year_idx).values  # shape: (lat, lon)

    ds.close()
    return out

#--------------------------------------------------------------------------
def read_hyde_grazing(year, grazing_path, grazing_names):
    """
    Read HYDE3.5 grazing data for a given year.

    grazing_names is a dict mapping a grazing category label to a NetCDF filename,
    as specified in config.json.  Each file is expected to contain a single variable
    whose name matches the file stem (e.g. 'pasture.nc' -> variable 'pasture').

    Args:
        year (int): Year to extract. HYDE3.5 baseline covers 10000 BCE - 2023 CE.
        grazing_path (str or Path): Directory containing the HYDE3.5 NetCDF files
        grazing_names (dict): Mapping of {category_label: filename}

    Returns:
        dict: {category_label: 2D np.ndarray shape (lat, lon)} for the requested year,
              plus 'lat' and 'lon' coordinate arrays (taken from the first file read).
    """
    if not isinstance(grazing_names, dict):
        raise TypeError(
            f"grazing_names must be a dict (e.g. {{'pasture': 'pasture.nc', "
            f"'rangeland': 'rangeland.nc'}}), got {type(grazing_names).__name__}. "
            f"Please update config.json accordingly."
        )

    grazing_path = Path(grazing_path)
    out = {}

    first = True
    for category, filename in grazing_names.items():
        ncfile = grazing_path / filename
        if not ncfile.exists():
            raise FileNotFoundError(f"HYDE3.5 grazing file not found: {ncfile}")
        # variable name is the file stem, e.g. 'pasture.nc' -> 'pasture'
        varname = Path(filename).stem
        ds = xr.open_dataset(ncfile, decode_times=False)
        time_units = ds['time'].attrs.get('units', None)
        year_idx = _get_year_idx(ds['time'].values, year, ncfile, time_units=time_units)
        print(f"  read_hyde_grazing: reading '{varname}' year {year} "
              f"(time index {year_idx}) from {ncfile}")
        if first:
            out['lat'] = ds['lat'].values
            out['lon'] = ds['lon'].values
            first = False
        out[category] = ds[varname].isel(time=year_idx).values  # shape: (lat, lon)
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
            f"No source grid cells found within ll_limits {ll_limits}. "
            f"lat range: [{lat.min():.2f}, {lat.max():.2f}], "
            f"lon range: [{lon.min():.2f}, {lon.max():.2f}]"
        )

    # slice the data
    chunk_lat = lat[lat_idx]
    chunk_lon = lon[lon_idx]
    chunk_data = data_2d[np.ix_(lat_idx, lon_idx)].astype(np.float32)

    # rasterio uses (west, south, east, north) bounds
    west  = float(chunk_lon[0])  - abs(lon_step) / 2.0
    east  = float(chunk_lon[-1]) + abs(lon_step) / 2.0
    south = float(chunk_lat[0])  - abs(lat_step) / 2.0
    north = float(chunk_lat[-1]) + abs(lat_step) / 2.0

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

    print(f"  write_latlon_to_geotiff: wrote {n_rows}x{n_cols} chunk to {tmp_path}")
    return tmp_path


#--------------------------------------------------------------------------
def write_chunk_mesh_to_geojson(global_mesh_df, cell_ids, tmp_path):
    """
    Filter the global HEALPix mesh DataFrame to only the cells in cell_ids
    and write a chunk-sized GeoJSON file for use as uraster source mesh.

    GeoJSON is used (rather than Parquet) because it is always supported by
    GDAL/OGR without additional plugins (unlike the Parquet/DuckDB driver).

    The caller should load the global parquet once (e.g. in run()) and pass
    the resulting DataFrame here to avoid re-reading the 37 MB file for every
    variable and chunk.

    Args:
        global_mesh_df (pd.DataFrame): Full merged_land_cells DataFrame, already
                                       loaded. Must have 'cellid' (int) and
                                       'geometry' (WKB bytes) columns.
        cell_ids (array-like):         1D array of integer cellid values for chunk.
        tmp_path (str|Path):           Full path of the output GeoJSON file to write.

    Returns:
        Path: Path to the written GeoJSON.
    """
    chunk_df = global_mesh_df[global_mesh_df['cellid'].isin(cell_ids)]

    if chunk_df.empty:
        raise ValueError(
            f"No mesh cells found for the provided cell_ids. "
            f"First few cell_ids: {list(cell_ids[:5])}"
        )

    # build GeoJSON manually from WKB geometry column
    features = []
    for _, row in chunk_df.iterrows():
        geom = shapely.wkb.loads(row['geometry'])
        features.append({
            'type': 'Feature',
            'geometry': geom.__geo_interface__,
            'properties': {'cellid': int(row['cellid'])},
        })

    geojson = {'type': 'FeatureCollection', 'features': features}
    tmp_path = Path(tmp_path)
    tmp_path.parent.mkdir(parents=True, exist_ok=True)
    with open(tmp_path, 'w') as f:
        json.dump(geojson, f)

    print(f"  write_chunk_mesh_to_geojson: wrote {len(features)} cells to {tmp_path}")
    return tmp_path


#--------------------------------------------------------------------------
def regrid_to_landgen_grid(data_2d, src_lat, src_lon, cell_ids, ll_limits,
                            global_mesh_df, tmp_dir, varname,
                            remap_method=3):
    """
    Regrid a single 2D source variable onto the landgen HEALPix grid cells
    for one spatial chunk, using uraster.

    Workflow:
        1. Slice source data to ll_limits and write to a temp GeoTIFF.
        2. Filter global_mesh_df to cell_ids and write a chunk parquet.
        3. Run uraster with iFlag_remap_method=3 (weighted average).
        4. Read the uraster output GeoJSON, extract 'mean' per cellid.
        5. Return a 1D array aligned to cell_ids order; cells with no overlap get 0.
        6. Clean up all temp files.

    Args:
        data_2d (np.ndarray):        2D source array shape (n_lat, n_lon).
        src_lat (np.ndarray):        1D source latitude array.
        src_lon (np.ndarray):        1D source longitude array.
        cell_ids (array-like):       1D array of integer cellid values for this chunk.
        ll_limits (tuple):           (min_lat, max_lat, min_lon, max_lon).
        global_mesh_df (pd.DataFrame): Pre-loaded merged_land_cells DataFrame.
                                     Load once in run() and pass here to avoid
                                     re-reading the 37 MB parquet for every variable.
        tmp_dir (str|Path):          Directory for temporary files (unique per worker).
        varname (str):               Variable name, used for temp file naming only.
        remap_method (int):          uraster iFlag_remap_method
                                     (1=nearest, 2=nearest, 3=weighted average).
                                     Default 3 is correct for area fraction data.

    Returns:
        np.ndarray: 1D float64 array of length len(cell_ids), regridded values
                    in the same order as cell_ids.
    """
    tmp_dir = Path(tmp_dir)
    tmp_dir.mkdir(parents=True, exist_ok=True)

    # unique suffix to avoid collisions between parallel workers
    suffix = f"{varname}_{ll_limits[0]:.0f}_{ll_limits[2]:.0f}"
    tmp_raster  = tmp_dir / f"src_{suffix}.tif"
    tmp_mesh_in = tmp_dir / f"mesh_in_{suffix}.geojson"   # GeoJSON: universal GDAL support
    tmp_mesh_out= tmp_dir / f"mesh_out_{suffix}.geojson"

    try:
        # 1. write source chunk as GeoTIFF
        write_latlon_to_geotiff(data_2d, src_lat, src_lon, ll_limits, tmp_raster)

        # 2. write filtered chunk mesh as GeoJSON (parquet requires libgdal-arrow-parquet)
        write_chunk_mesh_to_geojson(global_mesh_df, cell_ids, tmp_mesh_in)

        # 3. run uraster
        config = {
            'sFilename_source_mesh':    str(tmp_mesh_in),
            'aFilename_source_raster':  [str(tmp_raster)],
            'sFilename_target_mesh':    str(tmp_mesh_out),
            'iFlag_remap_method':       remap_method,
            'sField_unique_id':         'cellid',
            'iFlag_global':             0,   # chunk is regional, not global
            'iFlag_polar':              0,
        }
        processor = URaster(config)
        processor.setup()
        processor.run_remap()

        # 4. read output GeoJSON and extract 'mean' per cellid
        with open(tmp_mesh_out, 'r') as f:
            geojson = json.load(f)

        # build a cellid -> mean value lookup from the uraster output
        # cells with no raster overlap are missing the 'mean' key entirely;
        # default those to 0.0
        result_map = {}
        for feature in geojson['features']:
            props = feature['properties']
            cid = int(props['cellid'])
            val = props.get('mean', None)
            result_map[cid] = float(val) if (val is not None and not np.isnan(val)) else 0.0

        # 5. align to cell_ids order; missing cells default to 0
        out = np.array([result_map.get(int(cid), 0.0) for cid in cell_ids],
                       dtype=np.float64)

        print(f"  regrid_to_landgen_grid: '{varname}' -> {len(out)} cells, "
              f"non-zero: {np.count_nonzero(out)}")
        return out

    except ValueError as e:
        # raised by write_latlon_to_geotiff (no source cells in bounds) or
        # write_chunk_mesh_to_geojson (no mesh cells for cell_ids) — treat as
        # an empty chunk and return zeros rather than aborting the whole run
        print(f"  regrid_to_landgen_grid: WARNING skipping empty '{varname}' chunk "
              f"ll_limits={ll_limits}: {e}")
        return np.zeros(len(cell_ids), dtype=np.float64)

    except FileNotFoundError as e:
        # raised by write_latlon_to_geotiff or load_mesh_nc — a missing input
        # file is a configuration error; re-raise so the run fails clearly
        raise RuntimeError(
            f"regrid_to_landgen_grid: missing file for '{varname}' "
            f"ll_limits={ll_limits}: {e}"
        ) from e

    except KeyError as e:
        # raised by _get_year_idx / read_luh2_harvest — variable not in file
        raise RuntimeError(
            f"regrid_to_landgen_grid: variable lookup failed for '{varname}': {e}"
        ) from e

    finally:
        # 6. clean up temp files regardless of success or failure
        for f in [tmp_raster, tmp_mesh_in, tmp_mesh_out]:
            try:
                Path(f).unlink(missing_ok=True)
            except Exception:
                pass
        # uraster also creates a '_fixed' copy of the input mesh; clean that up too
        try:
            Path(str(tmp_mesh_in).replace('.geojson', '_fixed.geojson')).unlink(missing_ok=True)
        except Exception:
            pass
        # remove the tmp_dir if it is now empty
        try:
            tmp_dir.rmdir()
        except Exception:
            pass  # not empty or already gone - that's fine

#--------------------------------------------------------------------------


#--------------------------------------------------------------------------

def write_lt_year_data_to_netcdf(lt_year_data, cell_ids, year, out_path, out_fname,
                                  harvest_var_names, grazing_category_names):
    """Write one year of LtData harvest/grazing arrays to a NetCDF file.

    The output file is named  <out_path>/<stem>_<year>.<ext>
    e.g. landgen_land_type.nc -> landgen_land_type_2015.nc

    If the file already exists (from a previous run) it is overwritten.

    Parameters
    ----------
    lt_year_data    : LtData proxy  – source of harvest_frac / grazing_frac
    cell_ids        : 1-D array of HEALPix cell IDs (length n_cells)
    year            : int
    out_path        : str or Path – base output directory
    out_fname       : str – template filename, e.g. 'landgen_land_type.nc'
    harvest_var_names    : list of str – e.g. LUH2_HARVEST_VARS (length n_harvest)
    grazing_category_names : list of str – e.g. ['pasture','rangeland'] (length n_grazing)
    """
    stem, suffix = out_fname.rsplit('.', 1)
    year_fname = f"{stem}_{year}.{suffix}"
    out_file = Path(out_path) / year_fname
    out_file.parent.mkdir(parents=True, exist_ok=True)

    # retrieve arrays from the manager proxy
    harvest_frac = lt_year_data.get_harvest_frac()   # shape (n_cells, n_harvest)
    grazing_frac = lt_year_data.get_grazing_frac()   # shape (n_cells, n_grazing)

    n_cells = len(cell_ids)

    ds = xr.Dataset(
        coords={
            'cell':    ('cell',    np.arange(n_cells)),
            'harvest': ('harvest', harvest_var_names),
            'grazing': ('grazing', grazing_category_names),
        }
    )
    # cell_id is a coordinate variable on the cell dimension, not a dimension itself
    ds.coords['cell_id'] = xr.DataArray(cell_ids, dims='cell',
                                         attrs={'long_name': 'HEALPix cell ID'})
    ds.coords['cell'].attrs['long_name']    = 'HEALPix cell index'
    ds.coords['harvest'].attrs['long_name'] = 'LUH2 harvest variable'
    ds.coords['grazing'].attrs['long_name'] = 'HYDE grazing category'

    ds['harvest_frac'] = xr.DataArray(
        harvest_frac,
        dims=('cell', 'harvest'),
        attrs={
            'long_name': 'fractional area harvested',
            'units':     'fraction',
        }
    )

    ds['grazing_frac'] = xr.DataArray(
        grazing_frac,
        dims=('cell', 'grazing'),
        attrs={
            'long_name': 'fractional area grazed',
            'units':     'fraction',
        }
    )

    ds.attrs['year']        = year
    ds.attrs['description'] = 'landgen land type output — harvest and grazing fractions'

    # use zlib compression to keep file sizes manageable
    encoding = {
        'harvest_frac': {'zlib': True, 'complevel': 4, 'dtype': 'float32'},
        'grazing_frac': {'zlib': True, 'complevel': 4, 'dtype': 'float32'},
    }

    ds.to_netcdf(out_file, encoding=encoding)
    print(f"  Written: {out_file}")
    return out_file

#--------------------------------------------------------------------------

#--------------------------------------------------------------------------
def get_chunk_cell_ids(size_degrees):
    """Determine the landgen grid cell ids that intersect each lat-lon chunk.
    There will be duplicate cell ids across chunks; filter these later      

    Args:
        size_degrees (float): Size of the lat-lon chunks in degrees (e.g. 10 for 10x10 degree chunks).

    Returns:
        list of arrays: A list where each element is a 1D array of cell ids for the corresponding chunk.
    """
    # This function would read the global HEALPix mesh and determine which cells fall into each lat-lon chunk.
    # The implementation would depend on how the global mesh is structured and stored.
    # For example, if the global mesh has 'cellid', 'lat', and 'lon' columns, we could do something like:

    global_mesh_df = pd.read_parquet('path_to_global_mesh.parquet')  # adjust path as needed
    ll_limits = calc_ll_limits(size_degrees)
    chunk_cell_ids = []
    for min_lat, max_lat, min_lon, max_lon in ll_limits:
        mask = (
            (global_mesh_df['lat'] >= min_lat) & (global_mesh_df['lat'] < max_lat) &
            (global_mesh_df['lon'] >= min_lon) & (global_mesh_df['lon'] < max_lon)
        )
        cell_ids = global_mesh_df.loc[mask, 'cellid'].values
        chunk_cell_ids.append(cell_ids)
    return chunk_cell_ids
