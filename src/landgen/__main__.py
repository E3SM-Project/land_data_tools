# this allows landgen to be run as a standalone script, e.g. `python -m landgen <path/config.json>`

from landgen import landgen
import sys
import multiprocessing as mp

HELP = """\
Usage: python -m landgen <path/config_name.json>

Preprocess source land data to a target grid for use by mksurfdata.

Arguments:
  path/config_name.json  Path to and name of the JSON configuration file.
  Do not need to specify the full path if the config file is in the current working directory.

Options:
  -h, --help        Show this message and exit.

Base example call:
  python -m landgen config.json

This package is designed to be run on a cluster with SLURM, using a single node and multiple cores.
It can also be run on a local machine with multiple cores, but the user must adapt the load and submit scripts accordingly.
It is not designed for distributed execution across multiple nodes.

To execute this package using SLURM:
  source load_landgen_env.sh  # create/update the landgen environment and install the current landgen package in editable mode
  sbatch submit_landgen.sh # submit the SLURM job with the appropriate configuration file specified in submit_landgen.sh

"""

if __name__ == '__main__':
    # use spawn start method for multiprocessing to avoid issues with fork on some platforms
    mp.set_start_method('spawn')
    if len(sys.argv) == 2 and sys.argv[1] in ('-h', '--help'):
        print(HELP)
        sys.exit(0)
    if len(sys.argv) != 2:
        print(f"Usage: python -m landgen <path/config_name.json>  (use --help for more info)")
        sys.exit(1)
    print('Executing as standalone script')
    landgen.main(sys.argv[1])