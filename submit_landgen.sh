#!/bin/bash
#SBATCH --job-name=landgen
#SBATCH --nodes=1               # Ensure single node
#SBATCH --ntasks=1              # Run one task (master process)
#SBATCH --cpus-per-task=128
#SBATCH --exclusive             # Ensure exclusive access to the node (uses all cpus)
#SBATCH --mem=0                 # 0=Request all memory in this node (adjust as needed)
#SBATCH --time=02:00:00
#SBATCH --account e3sm
#SBATCH --qos=regular
#SBATCH --constraint=cpu

# type: sbatch submit_landgen.sh to submit the job to SLURM (in the directory where this script is located)

# srun belpow will run the __main__.py file inside the landgen package/directory
#    with config.json as the default input configuration file
# The configuration file should be located in the same directory as this script
# Update the configuration file path if it's located elsewhere or has a different name
# Note that config_template.json is a template and should be copied to config.json, or another name,
#    and edited with the desired settings before running this script

# with --exclusive and --nodes=1 and --ntasks=1, the job will have access to all cores on the node
# and SLURM_CPUS_PER_TASK will be set to the total number of cores on the node
# this allows multiprocessing to use all cores without oversubscribing,
#    when combined with OMP_NUM_THREADS=1 and MKL_NUM_THREADS=1 below

# but to automatically adjust cores based on the node, we can use srun instead, with SRUN_CPUS_PER_TASK
#    so launch with srun below and then the code looks for SRUN_CPUS_PER_TASK instead of SLURM_CPUS_PER_TASK
# to reduce the number of cores, set SRUN_CPUS_PER_TASK below to the desired number
#    and keep --exclusive to ensure the entire node is reserved for this job

# perlmutter has a regular and debug queue for cpus
# perlmutter has 128 physical cores, with 256 logical (2 hyperthreads per core), and 512G per node

# Calculate the number of workers to use:
# SLURM_CPUS_ON_NODE counts logical CPUs (hyperthreaded), so on Perlmutter:
#   SLURM_CPUS_ON_NODE = 256 logical = 128 physical x 2 hyperthreads
#   physical = 256 / 2 = 128
PHYS_CPUS=$(( SLURM_CPUS_ON_NODE / 2 ))
HALF_CPUS=$(( PHYS_CPUS / 2 ))
P90_CPUS=$(( (PHYS_CPUS * 90 + 99) / 100 ))  # 90% of physical cores, rounded up
HALF_LOG_CPUS=$(( (SLURM_CPUS_ON_NODE * 50 + 99) / 100 ))  # 50% of logical cores, rounded up
P90_LOG_CPUS=$(( (SLURM_CPUS_ON_NODE * 90 + 99) / 100 ))  # 90% of logical cores, rounded up

# set the srun cpus to use per task
export SRUN_CPUS_PER_TASK=$P90_LOG_CPUS

# Tell Python math and OpenMP to use the requested number of threads
# set these to 1 so that each process uses one thread on each core
# If these are >1 they allow multiple threads per process, which asks for #process * #thread cores
export OMP_NUM_THREADS=1
export MKL_NUM_THREADS=1

#### Activate the landgen conda environment

module load conda
conda activate landgen_env

#### Run the landgen package

# Use SLURM_SUBMIT_DIR (directory where sbatch was invoked),
# which resolves to the SLURM spool directory at runtime.

SCRIPT_DIR="${SLURM_SUBMIT_DIR}"

# Redirect stdout and stderr to the run output directory.
# #SBATCH --output cannot reference shell variables, so we use exec to replace
# this script's file descriptors after reading out_path from config.json.
# The default slurm-JOBID.out in the submit dir will be created but left empty.
OUT_PATH=$(python -c "import json,sys; print(json.load(open('${SCRIPT_DIR}/config.json')).get('out_path','.'))")
mkdir -p "${OUT_PATH}"
exec > "${OUT_PATH}/slurm-${SLURM_JOB_ID}.out" 2>&1
# Remove the now-empty default SLURM output file from the submit directory
rm -f "${SLURM_SUBMIT_DIR}/slurm-${SLURM_JOB_ID}.out"

srun python -m landgen "${SCRIPT_DIR}/config.json"
