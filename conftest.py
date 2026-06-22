"""Pytest hooks: keep collection stable when invoked from subdirectories."""

from __future__ import annotations

from pathlib import Path


def pytest_configure(config) -> None:
    # Pytest 9 only applies testpaths when invocation_dir == rootpath.
    # If you run `pytest` from docker/, it would otherwise scan docker/test-data/.
    invocation_dir = config.invocation_params.dir
    rootpath = config.rootpath
    if invocation_dir != rootpath:
        tests_dir = Path(rootpath) / "tests"
        if tests_dir.is_dir():
            config.args = [str(tests_dir)]


def pytest_ignore_collect(collection_path, config):  # noqa: ARG001
    if collection_path.name == "test-data":
        return True
    return None
