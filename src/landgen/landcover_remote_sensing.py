# landcover_remote_sensing.py
# this module defines the landcover source data
#   including schema mapping files

# the goal is to be able to add different source data definitions here
#    and use a generic api to landcover.py




# todo: use this code for temp files:
   # Determine a scratch base directory for worker-local temporary files.
    # Priority: $SCRATCH (NERSC/HPC) -> $TMPDIR -> system default (usually /tmp).
    # Each worker gets its own subdirectory to avoid cross-worker file collisions.
    scratch_base = os.environ.get('SCRATCH') or os.environ.get('TMPDIR') or tempfile.gettempdir()
    tmp_dir = Path(tempfile.mkdtemp(dir=scratch_base, prefix=f'landcover_{year}_'))
    print(f"  landcover_process: worker temp dir: {tmp_dir}")

    try:



finally:
        # clean up worker temp dir regardless of success or failure
        shutil.rmtree(tmp_dir, ignore_errors=True)