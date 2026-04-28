#!/bin/bash
#SBATCH --job-name=landgen
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=128
#SBATCH --exclusive
##SBATCH --mem=16G              # Request memory (adjust as needed)
#SBATCH --time=01:00:00
#SBATCH --account e3sm
#SBATCH --qos=regular
#SBATCH --constraint=cpu

# OMP_NUM_THREADS controls the ProcessPoolExecutor worker count in harvest.py
export OMP_NUM_THREADS=${SLURM_CPUS_PER_TASK}
export MKL_NUM_THREADS=1

# Activate your conda environment
conda activate landgen_env

# Run the landgen package
# This command will run the __main__.py file inside the landgen package/directory
python -m landgen  config_harvest_test.json
