# Utility functions for landgen

import logging
import multiprocessing
import os
import sys
import threading
import time
import psutil
import numpy as np
from pathlib import Path

from .plot_landgen import plot_module_netcdf


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


def init_worker_logging(log_path, logger_name='landgen'):
    """
    Attach a FileHandler (append mode) to the named logger inside a worker
    process.  Must be called at the start of any function that runs inside a
    multiprocessing Pool, because forkserver/spawn workers start with a clean
    logging state — the parent's handlers are not inherited.

    Idempotent: does nothing if the logger already has handlers.

    Args:
        log_path (str | Path): Path to the shared log file.
        logger_name (str):     Logger name (default 'landgen').
    """
    worker_logger = logging.getLogger(logger_name)
    if worker_logger.handlers:
        return  # already configured in this process
    worker_logger.setLevel(logging.INFO)
    worker_logger.propagate = False
    fmt = logging.Formatter('%(asctime)s  %(levelname)-8s  %(message)s',
                            datefmt='%Y-%m-%d %H:%M:%S')
    fh = logging.FileHandler(log_path, mode='a')
    fh.setFormatter(fmt)
    worker_logger.addHandler(fh)


def redirect_uraster_logs(out_path):
    """
    Redirect uraster's auto-created log files from the process CWD to out_path.

    uraster calls logging.FileHandler(f"{module_name}.log") at module-level
    import time (no directory prefix), so the files land in whatever the CWD is
    when the worker process first imports uraster.  This function walks all
    active loggers, finds FileHandlers whose log file is not already inside
    out_path, and replaces them with append-mode handlers in out_path.

    Call this in each worker process after uraster has been imported.

    Args:
        out_path (str | Path): Directory to write uraster log files into.
    """
    out_path = Path(out_path)
    out_path.mkdir(parents=True, exist_ok=True)
    out_str = str(out_path.resolve())

    for obj in logging.Logger.manager.loggerDict.values():
        if not isinstance(obj, logging.Logger):
            continue  # skip PlaceHolder entries
        for handler in list(obj.handlers):
            if not isinstance(handler, logging.FileHandler):
                continue
            current = Path(handler.baseFilename).resolve()
            if str(current).startswith(out_str):
                continue  # already in the right place
            new_path = out_path / current.name
            new_handler = logging.FileHandler(new_path, mode='a')
            if handler.formatter:
                new_handler.setFormatter(handler.formatter)
            new_handler.setLevel(handler.level)
            obj.removeHandler(handler)
            handler.close()
            obj.addHandler(new_handler)


#------- HPC / multiprocessing helpers -----------------------------------------
def parse_cpu_env(varname):
    """
    Return the integer value of an environment variable, or None if unset or
    not a valid integer.  Logs a warning via the 'landgen' logger on bad values.

    Args:
        varname (str): Environment variable name to read.

    Returns:
        int | None
    """
    _log = logging.getLogger('landgen')
    val = os.environ.get(varname)
    if val is None:
        return None
    try:
        return int(val)
    except ValueError:
        _log.warning(f"{varname} has invalid integer value '{val}'; ignoring.")
        return None


#------- monitoring computational resources for debugging and performance tuning ------
def monitor_cluster_resources(interval_sec=60.0, stop_event=None):
    """Periodically logs aggregated CPU and memory usage of the entire process tree."""
    parent_pid = os.getpid()

    try:
        parent_proc = psutil.Process(parent_pid)
    except psutil.NoSuchProcess:
        return

    _resource_logger = logging.getLogger('ClusterMonitor')
    _resource_logger.info(f"Starting resource monitor thread (Interval: {interval_sec}s)...")

    # Use logical CPUs so the reported capacity matches psutil cpu_percent(),
    # which is measured in units of one logical CPU.
    # On Perlmutter: 128 physical cores, 256 logical (hyperthreaded).
    node_cores = psutil.cpu_count(logical=True) or psutil.cpu_count(logical=False) or 1

    # Prime cpu_percent() for all current processes. psutil.Process.cpu_percent()
    # always returns 0.0 on the first call — it only establishes the baseline
    # timestamp. Without priming here, every process would show 0% on the first
    # monitoring interval.
    known_processes = {}
    try:
        for proc in [parent_proc] + parent_proc.children(recursive=True):
            try:
                proc.cpu_percent(interval=None)  # first call; always returns 0 — primes the counter
                known_processes[proc.pid] = proc
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
    except psutil.NoSuchProcess:
        pass

    while not stop_event.is_set():
        # Wait for the interval so cpu_percent() has a meaningful delta to measure
        stop_event.wait(interval_sec)
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
                    # Reuse cached psutil.Process object so cpu_percent() delta
                    # is measured from the previous interval (not from now).
                    # New processes (not yet primed) will return 0 this interval
                    # but will report accurately on the next one.
                    tracked_proc = known_processes.get(pid, proc)
                    new_process_cache[pid] = tracked_proc

                    total_mem_bytes += tracked_proc.memory_info().rss
                    total_cpu_pct += tracked_proc.cpu_percent(interval=None)
                    active_count += 1
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue

        # Drop dead processes from cache
        known_processes = new_process_cache

        total_mem_gb = total_mem_bytes / (1024 ** 3)
        # cpu_percent() returns percent of one core, so divide by 100 to get core-equivalents
        cores_utilized = total_cpu_pct / 100.0

        _resource_logger.info(
            f"[SYSTEM MONITOR] Processes: {active_count} | "
            f"Equivalent logical cores utilized: {cores_utilized:.1f}/{node_cores} | "
            f"Memory in use: {total_mem_gb:.2f} GB"
        )

