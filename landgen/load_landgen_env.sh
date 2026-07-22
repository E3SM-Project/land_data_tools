#!/bin/bash
# this script documents how to set up the landgen_env conda environment and install the landgen package in editable mode

# first load the conda module
module load conda

# create the landgen_env conda environment from the .yml file if it does not exist,
# otherwise update the existing environment
if conda env list | grep -q "^landgen_env "; then
    conda env update -n landgen_env -f landgen_env.yml
else
    conda env create -f landgen_env.yml
fi

# activate the landgen_env conda environment
conda activate landgen_env

# install the landgen package in editable mode (run from the landgen directory)
# this is separate from conda env update so that the env can be updated without pip trying to load landgen
pip install -e .

########## the following commands are to be run as needed, not in this script ##########

# install a new python package
#conda install <package_name>

# remove a python package
#conda remove <package_name>

# to update the .yml file with new packages added to the environment
# (landgen is removed automatically since it is not on PyPI and is installed locally via pip install -e .)
#conda env export -n landgen_env | grep -v "landgen==" > landgen_env.yml

# to update the enviroment with a new .yml file (e.g. after pulling changes from git)
#conda env update -n landgen_env -f landgen_env.yml