# land\_data_tools

This repository holds software tools that process a variety of source land data into self-consistent files for global modelling initial and boundary conditions. These tools have been developed as part of the Energy, Exascale, Earth System Model ([E3SM](https://e3sm.org)) project funded by the US Department of Energy. The two primary tools are:

## landgen

A python package that compiles and integrates a full suite of land data onto an unstructured grid. One aim of landgen is that it provides the maximum set of land variables possible based on the source data and the data integration processing. Retaining as much detail as possible maximizes the applicability of the resulting output data, such that others can use these output data by implementing a model-specific application to convert to the desired grid and variable schema (e.g., mksurfdata below), without having to reprocess all of the source data. The target landgen grid is a [HEALpix](https://healpix.sourceforge.io) equal area grid with each cell being 0.63 km^2 (~0.8km per side). We have been testing using a grid with ~10km per side, and are moving toward a grid with ~1.6km per side (2.53 km^2). These grids are designed such that the only land-relevant cells are stored in the grid file (ideally a scrip netcdf file) and in the resulting landgen output netcdf files. Land-relevant means all land cells, including antarctica and its ice shelves, plus a coastal buffer beyond these cells. The current buffer for the ~1.6km grid is 50km. See the README.md file in the landgen directory for more details.

## mksurfdata

TODO: A C++ program that converts the outputs from landgen into E3SM land model (ELM) land surface files. The two files are the static `surfdata` file that contains land surface characteristics and the land type distribution for a given year (usually the start year of a simulation), and a `landuse.timeseries` file that includes annual values of land type (e.g., vegetation and crop types) and land management (e.g., harvest, grazing). The annual values represent the land type state at the beginning of the first day of each labelled year and the annual volume or extent of land management during the labelled year. mksurfdata converts the landgen output onto the desired model grid and performs any necessary mapping of landgen variables to ELM variables. Another proposed feature of mksurfdata is that it will generate gross annual transitions in land type state upon spatail aggregation to a courser model grid than the landgen output grid. mksurfdata will be in its namesake directory at the same level as landgen.

## landgen-mksurfdata interface

The landgen-mksurfdata interface is defined by the file formats of the landgen output files, which are read by mksurfdata. landgen writes netcdf files for each year processed and for each of its processing modules (topography, land type, soil, human, atmosphere).

## Versioning

Official version releases will be tagged, with one version number representing both landgen and mksurfdata. This is to ensure that landgen and mksurfdata are always compatible with each other upon release. We plan to adopt a versioning scheme where the interface between landgen and mksurfdata is constant within an integer value, and a change in integer value indicates a change in this interface. For example, all 4.#.# versions of landgen and mksurfdata should be able to work together, and no v4.#.# of one code will work properly with v3.#.# of the other code. Sub-integer release versions could include updates of one or both codes, as long as the interface between the two does not change. The current development version is listed as v0.1.0.

## License

Need to figure this out and create a license file.

## Contributing

For now, contributions are restricted to land data development team members as part of the E3SM project.
