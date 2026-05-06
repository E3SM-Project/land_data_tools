# this allows landgen to be run as a standalone script, e.g. `python -m landgen <path/config.json>`

from landgen import landgen
import sys
from pathlib import Path
import multiprocessing as mp

if __name__ == '__main__':
    # use spawn start method for multiprocessing to avoid issues with fork on some platforms
    mp.set_start_method('spawn')
    if len(sys.argv) != 2:
        print(f"Usage: python {Path(__file__).name} <path/config.json>")
        sys.exit(1)
    print('Executing as standalone script')
    landgen.main(sys.argv[1])