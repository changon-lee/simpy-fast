# simpy-fast

An **unofficial, modified build of [SimPy](https://gitlab.com/team-simpy/simpy) 4.0.2**
with a Cython-compiled core and resources. The import name is still `simpy`.

This is not maintained by the SimPy authors. Please do not send bug reports
about this build to them.

## Install (Windows x64, Python 3.14)

```
pip install "https://github.com/changon-lee/simpy-fast/raw/main/wheels/simpy-4.0.2%2Bfast3-cp314-cp314-win_amd64.whl"
```

## Build

```
SIMPY_PGO=1 pip wheel . --no-build-isolation
```

## License

SimPy is Copyright (c) 2013 Ontje Lünsdorf and Stefan Scherfke (see
[AUTHORS.rst](AUTHORS.rst)) and is distributed under the MIT License.
The original license text is kept in [LICENSE](LICENSE), and the
modifications in this repository are under the same license.
