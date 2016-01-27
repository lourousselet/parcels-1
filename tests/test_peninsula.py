from parcels import Particle, ParticleSet, JITParticle
from parcels import NEMOGrid, ParticleFile, AdvectionRK4
from argparse import ArgumentParser
import numpy as np
import pytest


def peninsula_grid(xdim, ydim):
    """Construct a grid encapsulating the flow field around an
    idealised peninsula.

    :param xdim: Horizontal dimension of the generated grid
    :param xdim: Vertical dimension of the generated grid

    The original test description can be found in Fig. 2.2.3 in:
    North, E. W., Gallego, A., Petitgas, P. (Eds). 2009. Manual of
    recommended practices for modelling physical - biological
    interactions during fish early life.
    ICES Cooperative Research Report No. 295. 111 pp.
    http://archimer.ifremer.fr/doc/00157/26792/24888.pdf

    Note that the problem is defined on an A-grid while NEMO
    normally returns C-grids. However, to avoid accuracy
    problems with interpolation from A-grid to C-grid, we
    return NetCDF files that are on an A-grid.
    """
    # Set NEMO grid variables
    depth = np.zeros(1, dtype=np.float32)
    time = np.zeros(1, dtype=np.float64)

    # Generate the original test setup on A-grid in km
    dx = 100. / xdim / 2.
    dy = 50. / ydim / 2.
    La = np.linspace(dx, 100.-dx, xdim, dtype=np.float32)
    Wa = np.linspace(dy, 50.-dy, ydim, dtype=np.float32)

    # Define arrays U (zonal), V (meridional), W (vertical) and P (sea
    # surface height) all on A-grid
    U = np.zeros((xdim, ydim), dtype=np.float32)
    V = np.zeros((xdim, ydim), dtype=np.float32)
    W = np.zeros((xdim, ydim), dtype=np.float32)
    P = np.zeros((xdim, ydim), dtype=np.float32)

    u0 = 1
    x0 = 50.
    R = 0.32 * 50.

    # Create the fields
    x, y = np.meshgrid(La, Wa, sparse=True, indexing='ij')
    P = u0*R**2*y/((x-x0)**2+y**2)-u0*y
    U = u0-u0*R**2*((x-x0)**2-y**2)/(((x-x0)**2+y**2)**2)
    V = -2*u0*R**2*((x-x0)*y)/(((x-x0)**2+y**2)**2)

    # Set land points to NaN
    I = P >= 0.
    U[I] = np.nan
    V[I] = np.nan
    W[I] = np.nan

    # Convert from km to lat/lon
    lon = La / 1.852 / 60.
    lat = Wa / 1.852 / 60.

    return NEMOGrid.from_data(U, lon, lat, V, lon, lat,
                              depth, time, field_data={'P': P})


def pensinsula_example(grid, npart, mode='jit', degree=1,
                       verbose=False, output=False):
    """Example configuration of particle flow around an idealised Peninsula

    :arg filename: Basename of the input grid file set
    :arg npart: Number of particles to intialise"""

    # Determine particle class according to mode
    ParticleClass = JITParticle if mode == 'jit' else Particle

    # First, we define a custom Particle class to which we add a
    # custom variable, the initial stream function value p
    class MyParticle(ParticleClass):
        # JIT compilation requires a-priori knowledge of the particle
        # data structure, so we define additional variables here.
        user_vars = {'p': np.float32}

        def __init__(self, *args, **kwargs):
            """Custom initialisation function which calls the base
            initialisation and adds the instance variable p"""
            super(MyParticle, self).__init__(*args, **kwargs)
            self.p = None

        def __repr__(self):
            """Custom print function which overrides the built-in"""
            return "P(%.4f, %.4f)[p=%.5f]" % (self.lon, self.lat, self.p)

    # Initialise particles
    x = 3. * (1. / 1.852 / 60)  # 3 km offset from boundary
    y = (grid.U.lat[0] + x, grid.U.lat[-1] - x)  # latitude range, including offsets
    pset = ParticleSet(npart, grid, pclass=MyParticle, start=(x, y[0]), finish=(x, y[1]))
    for particle in pset:
        particle.p = grid.P[0., particle.lon, particle.lat]

    if verbose:
        print("Initial particle positions:")
        for p in pset:
            print(p)

    # Write initial output to file
    if output:
        out = ParticleFile(name="MyParticle", particleset=pset)
        out.write(pset, 0.)

    # Advect the particles for 24h
    time = 24 * 3600.
    dt = 36.
    print("Peninsula: Advecting %d particles for %d timesteps"
          % (npart, int(time / dt)))
    if output:
        # Use sub-timesteps when doing trajectory I/O
        substeps = 100
        timesteps = int(time / substeps / dt)
        current = 0.
        for _ in range(timesteps):
            pset.execute(AdvectionRK4, timesteps=substeps, dt=dt)
            current += substeps * dt
            out.write(pset, current)
    else:
        # Execution without I/O for performance benchmarks
        timesteps = int(time / dt)
        pset.execute(AdvectionRK4, timesteps=timesteps, dt=dt)

    if verbose:
        print("Final particle positions:")
        for p in pset:
            p_local = grid.P[0., p.lon, p.lat]
            print("%s\tP(final)%.5f \tdelta(P): %0.5g" % (str(p), p_local, p_local - p.p))

    return np.array([abs(p.p - grid.P[0., p.lon, p.lat]) for p in pset])


@pytest.mark.parametrize('mode', ['scipy', 'jit'])
def test_peninsula_grid(mode):
    """Execute peninsula test from grid generated in memory"""
    grid = peninsula_grid(100, 50)
    error = pensinsula_example(grid, 100, mode=mode, degree=1)
    assert(error <= 2.e-4).all()


@pytest.fixture(scope='module')
def gridfile():
    """Generate grid files for peninsula test"""
    filename = 'peninsula'
    grid = peninsula_grid(100, 50)
    grid.write(filename)
    return filename


@pytest.mark.parametrize('mode', ['scipy', 'jit'])
def test_peninsula_file(gridfile, mode):
    """Open grid files and execute"""
    grid = NEMOGrid.from_file(gridfile, extra_vars={'P': 'P'})
    error = pensinsula_example(grid, 100, mode=mode, degree=1)
    assert(error <= 2.e-4).all()


if __name__ == "__main__":
    p = ArgumentParser(description="""
Example of particle advection around an idealised peninsula""")
    p.add_argument('mode', choices=('scipy', 'jit'), nargs='?', default='jit',
                   help='Execution mode for performing RK4 computation')
    p.add_argument('-p', '--particles', type=int, default=20,
                   help='Number of particles to advect')
    p.add_argument('-d', '--degree', type=int, default=1,
                   help='Degree of spatial interpolation')
    p.add_argument('-v', '--verbose', action='store_true', default=False,
                   help='Print particle information before and after execution')
    p.add_argument('-o', '--output', action='store_true', default=False,
                   help='Output trajectory data to file')
    p.add_argument('--profiling', action='store_true', default=False,
                   help='Print profiling information after run')
    p.add_argument('-g', '--grid', type=int, nargs=2, default=None,
                   help='Generate grid file with given dimensions')
    args = p.parse_args()

    if args.grid is not None:
        filename = 'peninsula'
        grid = peninsula_grid(args.grid[0], args.grid[1])
        grid.write(filename)

    # Open grid file set
    grid = NEMOGrid.from_file('peninsula', extra_vars={'P': 'P'})

    if args.profiling:
        from cProfile import runctx
        from pstats import Stats
        runctx("pensinsula_example(grid, args.particles, mode=args.mode,\
                                   degree=args.degree, verbose=args.verbose,\
                                   output=args.output)",
               globals(), locals(), "Profile.prof")
        Stats("Profile.prof").strip_dirs().sort_stats("time").print_stats(10)
    else:
        pensinsula_example(grid, args.particles, mode=args.mode,
                           degree=args.degree, verbose=args.verbose,
                           output=args.output)
