"""Plot utilities for landgen NetCDF outputs.

This module can be imported from other landgen code and can also be run as a
standalone CLI module via:

    python -m landgen.plot_landgen <file_path> <out_path> <year> [options]
"""

import argparse
import json
import logging
from pathlib import Path

import numpy as np
import xarray as xr
import matplotlib

matplotlib.use('Agg')   # non-interactive backend — safe for HPC/batch
import matplotlib.pyplot as plt
from matplotlib.collections import PolyCollection

try:
    import cartopy.crs as ccrs
    import cartopy.feature as cfeature
    _HAS_CARTOPY = True
except ImportError:
    _HAS_CARTOPY = False


# Grid geometry fields written by write_module_netcdf() — excluded from
# auto-detect, except for landfrac or when explicitly requested by varnames.
_GRID_FIELDS = frozenset({'lon_cen', 'lat_cen', 'lon_vtx', 'lat_vtx', 'cell_area'})


def _plot_one_var(ax, ds, varname, layer, year, mask, lon_cen, lat_cen,
                  plot_type, colormap, ll_limits, _log, scale_limits=None):
    """Draw a single variable onto an existing Axes object."""
    da = ds[varname]
    dims = list(da.dims)

    # select time slice if present
    if 'time' in dims:
        time_vals = ds['time'].values
        t_idx = int(np.argmin(np.abs(time_vals - year)))
        da = da.isel(time=t_idx)
        dims = list(da.dims)

    # select layer / collapse extra dims
    layer_applied = da.ndim >= 2
    if da.ndim >= 2:
        if da.ndim > 2:
            extra_dims = dims[2:]
            _log.warning(
                f"plot_module_netcdf: '{varname}' has extra dimensions {extra_dims} "
                f"beyond (cell, {dims[1]}); collapsing each to index 0."
            )
        da = da.isel({dims[1]: layer})
        while da.ndim > 1:
            da = da.isel({list(da.dims)[1]: 0})

    values = da.values.astype(np.float64)[mask]

    if scale_limits is not None:
        vmin, vmax = float(scale_limits[0]), float(scale_limits[1])
    else:
        vmin, vmax = float(np.nanmin(values)), float(np.nanmax(values))
    norm = plt.Normalize(vmin=vmin, vmax=vmax)
    cmap = plt.colormaps[colormap]

    # map extent
    if ll_limits is not None:
        min_lat, max_lat, min_lon, max_lon = ll_limits
    else:
        pad = 1.0
        min_lon = float(lon_cen.min()) - pad
        max_lon = float(lon_cen.max()) + pad
        min_lat = float(lat_cen.min()) - pad
        max_lat = float(lat_cen.max()) + pad

    if _HAS_CARTOPY:
        ax.set_extent([min_lon, max_lon, min_lat, max_lat], crs=ccrs.PlateCarree())
        ax.add_feature(cfeature.COASTLINE, linewidth=0.5, zorder=5)
    else:
        ax.set_xlim(min_lon, max_lon)
        ax.set_ylim(min_lat, max_lat)
        ax.set_aspect('equal')
    ax.set_xlabel('Longitude')
    ax.set_ylabel('Latitude')
    layer_suffix = f", layer {layer}" if layer_applied else ""
    ax.set_title(f"{varname}  (year {year}{layer_suffix})")

    geo_kw = {'transform': ccrs.PlateCarree()} if _HAS_CARTOPY else {}

    if plot_type == 'scatter':
        mappable = ax.scatter(lon_cen, lat_cen, c=values, cmap=cmap, norm=norm,
                              s=1, linewidths=0, **geo_kw)
    else:  # 'rendered'
        lon_vtx = ds['lon_vtx'].values[mask]
        lat_vtx = ds['lat_vtx'].values[mask]
        verts = np.stack([lon_vtx, lat_vtx], axis=-1)
        mappable = PolyCollection(verts, array=values, cmap=cmap, norm=norm,
                                  linewidths=0, **geo_kw)
        ax.add_collection(mappable)

    return mappable


def _get_layer_indices(ds, vname, layers):
    """Return (layer_indices, is_multilayer) for a variable in ds."""
    da_tmp = ds[vname]
    dims_tmp = list(da_tmp.dims)
    if 'time' in dims_tmp:
        da_tmp = da_tmp.isel(time=0)
        dims_tmp = list(da_tmp.dims)
    if da_tmp.ndim >= 2:
        n_layers = da_tmp.sizes[dims_tmp[1]]
        if layers is None:
            idxs = list(range(n_layers))
        elif isinstance(layers, int):
            idxs = [layers]
        else:
            idxs = list(layers)
        return idxs, True
    return [0], False


def _layers_for_var(vname, layers):
    """Resolve per-variable layer selection.

    layers can be:
      - None
      - int
      - iterable of ints
      - dict mapping varname -> (None | int | iterable of ints)
    """
    if isinstance(layers, dict):
        return layers.get(vname, None)
    return layers


def plot_module_netcdf(file_path, out_path, year, varnames=None, layers=None,
                       plot_type='scatter', file_type='png',
                       colormap='viridis', ll_limits=None, scale_limits=None):
    """Plot one or more variables from a NetCDF file written by landgen."""
    _log = logging.getLogger('landgen')

    if plot_type not in ('scatter', 'rendered'):
        _log.error(f"plot_module_netcdf: plot_type must be 'scatter' or 'rendered', got '{plot_type}'")
        raise ValueError(f"plot_module_netcdf: plot_type must be 'scatter' or 'rendered', got '{plot_type}'")
    if file_type != 'png':
        _log.error(f"plot_module_netcdf: file_type must be 'png', got '{file_type}'")
        raise ValueError(f"plot_module_netcdf: file_type must be 'png', got '{file_type}'")

    file_path = Path(file_path)
    if not file_path.exists():
        _log.error(f"plot_module_netcdf: file not found: {file_path}")
        raise FileNotFoundError(f"plot_module_netcdf: file not found: {file_path}")

    out_path = Path(out_path)
    out_path.mkdir(parents=True, exist_ok=True)

    ds = xr.open_dataset(file_path, decode_times=False)

    cell_dim = 'cell'
    if varnames is None:
        varnames = [
            v for v in ds.data_vars
            if v not in _GRID_FIELDS and cell_dim in ds[v].dims
        ]
        if not varnames:
            ds.close()
            msg = f"plot_module_netcdf: no plottable data variables found in {file_path}"
            _log.error(msg)
            raise ValueError(msg)
        _log.info(f"plot_module_netcdf: auto-detected variables: {varnames}")
    else:
        missing = [v for v in varnames if v not in ds]
        if missing:
            ds.close()
            msg = (f"plot_module_netcdf: variables {missing} not found in {file_path}. "
                   f"Available: {list(ds.data_vars)}")
            _log.error(msg)
            raise KeyError(msg)

    for v in varnames:
        if 'time' in ds[v].dims:
            time_vals = ds['time'].values
            t_idx = int(np.argmin(np.abs(time_vals - year)))
            if int(time_vals[t_idx]) != year:
                ds.close()
                msg = (f"plot_module_netcdf: year {year} not found in {file_path} "
                       f"(closest: {int(time_vals[t_idx])})")
                _log.error(msg)
                raise ValueError(msg)
            break

    lon_cen = ds['lon_cen'].values
    lat_cen = ds['lat_cen'].values

    if ll_limits is not None:
        min_lat, max_lat, min_lon, max_lon = ll_limits
        mask = (
            (lat_cen >= min_lat) & (lat_cen <= max_lat) &
            (lon_cen >= min_lon) & (lon_cen <= max_lon)
        )
    else:
        mask = np.ones(len(lon_cen), dtype=bool)

    if not mask.any():
        ds.close()
        msg = f"plot_module_netcdf: no cells remain after applying ll_limits {ll_limits}"
        _log.error(msg)
        raise ValueError(msg)

    lon_cen = lon_cen[mask]
    lat_cen = lat_cen[mask]

    out_files = []
    for vname in varnames:
        var_layers = _layers_for_var(vname, layers)
        layer_indices, is_multilayer = _get_layer_indices(ds, vname, var_layers)
        for layer_idx in layer_indices:
            if _HAS_CARTOPY:
                fig, ax = plt.subplots(figsize=(12, 6),
                                       subplot_kw={'projection': ccrs.PlateCarree()})
            else:
                fig, ax = plt.subplots(figsize=(12, 6))
            try:
                mappable = _plot_one_var(ax, ds, vname, layer_idx, year, mask,
                                         lon_cen, lat_cen, plot_type, colormap,
                                         ll_limits, _log, scale_limits=scale_limits)
                fig.colorbar(mappable, ax=ax, label=vname)
                if is_multilayer:
                    out_file = out_path / f"{file_path.stem}_{vname}_layer{layer_idx}_{year}.png"
                else:
                    out_file = out_path / f"{file_path.stem}_{vname}_{year}.png"
                fig.savefig(out_file, dpi=300, bbox_inches='tight')
                out_files.append(out_file)
                _log.info(f"plot_module_netcdf: wrote {out_file}")
            except Exception as e:
                _log.warning(f"plot_module_netcdf: skipping '{vname}' layer {layer_idx}: {e}")
            finally:
                plt.close(fig)
    ds.close()
    return out_files


def _parse_layers_arg(layers_raw):
    """Parse CLI layers argument.

    Accepted examples:
      "0"
      "[0,1]"
      '{"pct_pft": [0,1], "pct_ocean": null}'
    """
    if layers_raw is None:
        return None
    txt = layers_raw.strip()
    if txt == '':
        return None
    try:
        parsed = json.loads(txt)
    except json.JSONDecodeError:
        if txt.lstrip('-').isdigit():
            return int(txt)
        raise ValueError(
            "Invalid --layers value. Use JSON (e.g. '[0,1]' or '{\"pct_pft\":[0,1]}') "
            "or a single integer like '0'."
        )
    return parsed


def main(argv=None):
    """CLI entry point for standalone plotting."""
    parser = argparse.ArgumentParser(
        description="Plot variables from a landgen NetCDF file."
    )
    parser.add_argument('file_path', help='Input NetCDF file path')
    parser.add_argument('out_path', help='Output directory for plots')
    parser.add_argument('year', type=int, help='Calendar year to plot')
    parser.add_argument('--varnames', nargs='*', default=None,
                        help='Optional variable names to plot; default=None prints all variables except grid geometry fields')
    parser.add_argument('--layers', default=None,
                        help="Layer selector: int, JSON list, or JSON dict by variable; default=None prints all layers")
    parser.add_argument('--plot-type', choices=['scatter', 'rendered'], default='scatter',
                        help="Plot type: 'scatter' for colored points, 'rendered' for cell polygons; default='scatter'")
    parser.add_argument('--file-type', choices=['png'], default='png', help="Output file type; only 'png' is supported")
    parser.add_argument('--colormap', default='viridis', help="Colormap for plots; default='viridis'")
    parser.add_argument('--ll-limits', nargs=4, type=float, metavar=('MIN_LAT', 'MAX_LAT', 'MIN_LON', 'MAX_LON'),
                        default=None, help="Latitude and longitude limits for plots; default=None uses full extent")
    parser.add_argument('--scale-limits', nargs=2, type=float, metavar=('VMIN', 'VMAX'),
                        default=None, help="Colorscale min and max; default=None uses data min/max")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s  %(levelname)-8s  %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )

    layers = _parse_layers_arg(args.layers)

    result = plot_module_netcdf(
        file_path=args.file_path,
        out_path=args.out_path,
        year=args.year,
        varnames=args.varnames,
        layers=layers,
        plot_type=args.plot_type,
        file_type=args.file_type,
        colormap=args.colormap,
        ll_limits=tuple(args.ll_limits) if args.ll_limits is not None else None,
        scale_limits=tuple(args.scale_limits) if args.scale_limits is not None else None,
    )

    print(result)


if __name__ == '__main__':
    main()
