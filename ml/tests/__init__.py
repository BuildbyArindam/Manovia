"""Tests package marker.

Present so `from tests.conftest import config_dict` works the same way under
pytest, mypy and a plain `python -c` import — the tests are part of the package's
public surface for the purposes of linting.
"""
