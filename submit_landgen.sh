#!/bin/bash
#SBATCH --job-name=landgen
#SBATCH --nodes=1               # Ensure single node
#SBATCH --ntasks=1              # Run one task (master process)
#SBATCH --cpus-per-task=128
#SBATCH --exclusive             # Ensure exclusive access to the node (uses all cpus)
#SBATCH --mem=0                 # 0=Request all memory in this node (adjust as needed)
#SBATCH --time=01:00:00
#SBATCH --account e3sm
#SBATCH --qos=regular
#SBATCH --constraint=cpu

# with --exclusive and --nodes=1 and --ntasks=1, the job will have access to all cores on the node
# and SLURM_CPUS_PER_TASK will be set to the total number of cores on the node
# this allows multiprocessing to use all cores without oversubscribing,
#    when combined with OMP_NUM_THREADS=1 and MKL_NUM_THREADS=1 below

# but to automatically adjust cores based on the node, we can use srun instead, with SRUN_CPUS_PER_TASK
#    so launch with srun below and then the code looks for SRUN_CPUS_PER_TASK instead of SLURM_CPUS_PER_TASK
# to reduce the number of cores, set SRUN_CPUS_PER_TASK below to the desired number
#    and keep --exclusive to ensure the entire node is reserved for this job

# perlmutter has a regular and debug queue for cpus
# perlmutter has 128 cores and 512G per node

# OMP_NUM_THREADS controls the ProcessPoolExecutor worker count in harvest.py
export OMP_NUM_THREADS=${SLURM_CPUS_PER_TASK}
export MKL_NUM_THREADS=1

# Run the landgen package
# This command will run the __main__.py file inside the landgen package/directory
srun python -m landgen
