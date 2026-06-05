# Utility functions for landgen

import logging
import multiprocessing
import os
import sys
import threading
import time
import psutil
import numpy as np
import xarray as xr
from pathlib import Path
import matplotlib
matplotlib.use('Agg')   # non-interactive backend — safe for HPC/batch
import matplotlib.pyplot as plt
import matplotlib.cm as cm
from matplotlib.collections import PolyCollection

try:
    import cartopy.crs as ccrs
    import cartopy.feature as cfeature
    _HAS_CARTOPY = True
except ImportError:
    _HAS_CARTOPY = False


#------- shared logger setup ----------------------------------------------------
def setup_logger(name, log_path, level=logging.INFO):
    """
    Configure a named logger to write to log_path and to stdout.
    Call once (e.g. from main()) for each logger you need.  Any module can
    then obtain the same logger with:

        import logging
        logger = logging.getLogger('<name>')

    Each call is idempotent: if handlers are already attached the logger is
    returned unchanged, so calling setup_logger() twice is safe.

    Args:
        name (str):      Logger name, e.g. 'landgen' or 'ClusterMonitor'.
        log_path (Path): Full path to the log file (unique per run).
        level (int):     Logging level (default logging.INFO).

    Returns:
        logging.Logger: The configured logger.
    """
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger   # already configured

    logger.setLevel(level)
    logger.propagate = False   # don't double-log via the root logger

    fmt = logging.Formatter('%(asctime)s  %(levelname)-8s  %(message)s',
                            datefmt='%Y-%m-%d %H:%M:%S')

    # file handler — each run gets its own file (mode='w')
    fh = logging.FileHandler(log_path, mode='w')
    fh.setFormatter(fmt)
    logger.addHandler(fh)

    # console handler
    ch = logging.StreamHandler(sys.stdout)
    ch.setFormatter(fmt)
    logger.addHandler(ch)

    return logger

#------- monitoring computational resources for debugging and performance tuning ------
def monitor_cluster_resources(interval_sec=300.0, stop_event=None):
    """Periodically logs aggregated CPU and memory usage of the entire process tree."""
    parent_pid = os.getpid()
    
    try:
        parent_proc = psutil.Process(parent_pid)
    except psutil.NoSuchProcess:
        return

    # Cache psutil.Process objects to maintain persistent cpu_percent() tracking
    # This prevents the first-call 0.0% issue for long-running processes
    known_processes = {}

    _resource_logger = logging.getLogger('ClusterMonitor')
    _resource_logger.info(f"Starting resource monitor thread (Interval: {interval_sec}s)...")

    while not stop_event.is_set():
        # Wait for the specified interval first so cpu_percent has time to accumulate delta
        time.sleep(interval_sec)
        if stop_event.is_set():
            break

        try:
            # Gather current active process tree
            current_procs = [parent_proc] + parent_proc.children(recursive=True)
        except psutil.NoSuchProcess:
            continue

        total_mem_bytes = 0
        total_cpu_pct = 0.0
        active_count = 0
        new_process_cache = {}

        for proc in current_procs:
            pid = proc.pid
            try:
                if proc.is_running() and proc.status() != psutil.STATUS_ZOMBIE:
                    # Reuse existing process instance to ensure accurate CPU delta tracking
                    tracked_proc = known_processes.get(pid, proc)
                    new_process_cache[pid] = tracked_proc
                    
                    total_mem_bytes += tracked_proc.memory_info().rss
                    total_cpu_pct += tracked_proc.cpu_percent(interval=None)
                    active_count += 1
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue

        # Keep cache fresh (drops dead processes)
        known_processes = new_process_cache

        # Math calculations
        total_mem_gb = total_mem_bytes / (1024 ** 3)
        # Convert raw single-core percentage to an estimated number of fully utilized cores
        cores_utilized = total_cpu_pct / 100.0
        system_cores_available = psutil.cpu_count(logical=True) or 1

        _resource_logger.info(
            f"[SYSTEM MONITOR] Processes: {active_count} | "
            f"Aggregate core usage, as number of cores: {cores_utilized:.2f} | "
            f"Fraction of available cores active, based on aggregate core usage: {cores_utilized:.2f}/{system_cores_available} | "
            f"Memory In Use: {total_mem_gb:.2f} GB"
        )


#------- plotting ---------------------------------------------------------------

# Grid geometry fields written by write_module_netcdf() — excluded from auto-detect,
#    except for landfrac or when explicitly requested by varnames argument to plot_module_netcdf()
_GRID_FIELDS = frozenset({'lon_xy', 'lat_xy', 'lon_vtx', 'lat_vtx', 'cell_area'})


def _plot_one_var(ax, ds, varname, layer, year, mask, lon_xy, lat_xy,
                  plot_type, colormap, ll_limits, _log):
    """
    Draw a single variable onto an existing Axes object.  Returns the
    mappable (ScalarMappable) for colorbar attachment.
    Internal helper for plot_module_netcdf.
    """
    da   = ds[varname]
    dims = list(da.dims)

    # select time slice if present
    if 'time' in dims:
        time_vals = ds['time'].values
        t_idx = int(np.argmin(np.abs(time_vals - year)))
        da    = da.isel(time=t_idx)
        dims  = list(da.dims)

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

    # units label — not yet written to file; placeholder for future use
    # units = ds[varname].attrs.get('units', '')

    vmin, vmax = float(np.nanmin(values)), float(np.nanmax(values))
    norm = plt.Normalize(vmin=vmin, vmax=vmax)
    cmap = cm.get_cmap(colormap)

    # map extent
    if ll_limits is not None:
        min_lat, max_lat, min_lon, max_lon = ll_limits
    else:
        pad = 1.0
        min_lon = float(lon_xy.min()) - pad
        max_lon = float(lon_xy.max()) + pad
        min_lat = float(lat_xy.min()) - pad
        max_lat = float(lat_xy.max()) + pad

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
        mappable = ax.scatter(lon_xy, lat_xy, c=values, cmap=cmap, norm=norm,
                              s=1, linewidths=0, **geo_kw)
    else:  # 'rendered'
        lon_vtx = ds['lon_vtx'].values[mask]
        lat_vtx = ds['lat_vtx'].values[mask]
        verts   = np.stack([lon_vtx, lat_vtx], axis=-1)
        mappable = PolyCollection(verts, array=values, cmap=cmap, norm=norm,
                                  linewidths=0, **geo_kw)
        ax.add_collection(mappable)

    return mappable


def plot_module_netcdf(file_path, out_path, year, varnames=None, layers=None,
                       plot_type='scatter', file_type='png',
                       colormap='viridis', ll_limits=None):
    """
    Plot one or more variables from a NetCDF file written by write_module_netcdf().

    For file_type='pdf' all variable+layer combinations are written as separate
    pages in a single PDF file named '<file_stem>_<year>.pdf'.
    For file_type='png' each variable+layer combination is written to a separate
    file named '<file_stem>_<varname>_layer{N}_<year>.png' (or
    '<file_stem>_<varname>_<year>.png' for 1-D variables).

    Args:
        file_path (str|Path):   Full path to the NetCDF file.
        out_path (str|Path):    Directory in which to write output image(s).
        year (int):             Calendar year to plot.
        varnames (list[str]|None): Variables to plot.  None plots all data
                                fields that have a 'cell' dimension, excluding
                                grid geometry fields (lon_xy, lat_xy, etc.).
        layers (int|None):      Layer index (0-based) to plot for multi-layer
                                variables.  None (default) plots all layers.
                                Ignored for 1-D (cell-only) variables.
        plot_type (str):        'scatter' or 'rendered'.  Default 'scatter'.
        file_type (str):        'png' or 'pdf'.  Default 'png'.
        colormap (str):         Matplotlib colormap name.  Default 'viridis'.
        ll_limits (tuple|None): (min_lat, max_lat, min_lon, max_lon) extent
                                filter.  None uses the data extent.

    Returns:
        Path        if file_type='pdf': path to the single PDF file.
        list[Path]  if file_type='png': list of paths to the PNG files.
    """
   
    _log = logging.getLogger('landgen')

    if plot_type not in ('scatter', 'rendered'):
        _log.error(f"plot_module_netcdf: plot_type must be 'scatter' or 'rendered', got '{plot_type}'")
        raise ValueError(f"plot_module_netcdf: plot_type must be 'scatter' or 'rendered', got '{plot_type}'")
    if file_type not in ('png', 'pdf'):
        _log.error(f"plot_module_netcdf: file_type must be 'png' or 'pdf', got '{file_type}'")
        raise ValueError(f"plot_module_netcdf: file_type must be 'png' or 'pdf', got '{file_type}'")

    file_path = Path(file_path)
    if not file_path.exists():
        _log.error(f"plot_module_netcdf: file not found: {file_path}")
        raise FileNotFoundError(f"plot_module_netcdf: file not found: {file_path}")

    out_path = Path(out_path)
    out_path.mkdir(parents=True, exist_ok=True)

    ds = xr.open_dataset(file_path, decode_times=False)

    # --- validate / auto-detect varnames ---
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

    # --- validate year ---
    # check against the first variable that has a time dim, or skip if none do
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

    # --- spatial coordinates and mask (same for all variables) ---
    lon_xy = ds['lon_xy'].values
    lat_xy = ds['lat_xy'].values

    if ll_limits is not None:
        min_lat, max_lat, min_lon, max_lon = ll_limits
        mask = (
            (lat_xy >= min_lat) & (lat_xy <= max_lat) &
            (lon_xy >= min_lon) & (lon_xy <= max_lon)
        )
    else:
        mask = np.ones(len(lon_xy), dtype=bool)

    if not mask.any():
        ds.close()
        msg = f"plot_module_netcdf: no cells remain after applying ll_limits {ll_limits}"
        _log.error(msg)
        raise ValueError(msg)

    lon_xy = lon_xy[mask]
    lat_xy = lat_xy[mask]

    # --- render ---

    def _get_layer_indices(vname):
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

    if file_type == 'pdf':
        from matplotlib.backends.backend_pdf import PdfPages
        out_file = out_path / f"{file_path.stem}_{year}.pdf"
        page_count = 0
        with PdfPages(out_file) as pdf:
            for vname in varnames:
                layer_indices, is_multilayer = _get_layer_indices(vname)
                for layer_idx in layer_indices:
                    if _HAS_CARTOPY:
                        fig, ax = plt.subplots(figsize=(12, 6),
                                               subplot_kw={'projection': ccrs.PlateCarree()})
                    else:
                        fig, ax = plt.subplots(figsize=(12, 6))
                    try:
                        mappable = _plot_one_var(ax, ds, vname, layer_idx, year, mask,
                                                lon_xy, lat_xy, plot_type, colormap,
                                                ll_limits, _log)
                        fig.colorbar(mappable, ax=ax, label=vname)
                        pdf.savefig(fig, bbox_inches='tight', dpi=150)
                        page_count += 1
                    except Exception as e:
                        _log.warning(f"plot_module_netcdf: skipping '{vname}' layer {layer_idx}: {e}")
                    finally:
                        plt.close(fig)
        ds.close()
        _log.info(f"plot_module_netcdf: wrote {page_count}-page PDF to {out_file}")
        return out_file

    else:  # 'png'
        out_files = []
        for vname in varnames:
            layer_indices, is_multilayer = _get_layer_indices(vname)
            for layer_idx in layer_indices:
                if _HAS_CARTOPY:
                    fig, ax = plt.subplots(figsize=(12, 6),
                                           subplot_kw={'projection': ccrs.PlateCarree()})
                else:
                    fig, ax = plt.subplots(figsize=(12, 6))
                try:
                    mappable = _plot_one_var(ax, ds, vname, layer_idx, year, mask,
                                             lon_xy, lat_xy, plot_type, colormap,
                                             ll_limits, _log)
                    fig.colorbar(mappable, ax=ax, label=vname)
                    if is_multilayer:
                        out_file = out_path / f"{file_path.stem}_{vname}_layer{layer_idx}_{year}.png"
                    else:
                        out_file = out_path / f"{file_path.stem}_{vname}_{year}.png"
                    fig.savefig(out_file, dpi=150, bbox_inches='tight')
                    out_files.append(out_file)
                    _log.info(f"plot_module_netcdf: wrote {out_file}")
                except Exception as e:
                    _log.warning(f"plot_module_netcdf: skipping '{vname}' layer {layer_idx}: {e}")
                finally:
                    plt.close(fig)
        ds.close()
        return out_files
