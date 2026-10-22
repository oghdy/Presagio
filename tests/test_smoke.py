"""Smoke tests: the package and its subpackages import on the pinned Python."""

import importlib
import sys

import pytest

import presagio

SUBPACKAGES = ["config", "db", "ingestion", "quality", "features", "models", "evaluation", "api"]


def test_python_is_312() -> None:
    """The test environment runs on Python 3.12."""
    assert sys.version_info[:2] == (3, 12)


def test_package_has_version() -> None:
    """The top-level package exposes a version string."""
    assert isinstance(presagio.__version__, str)


@pytest.mark.parametrize("name", SUBPACKAGES)
def test_subpackage_imports(name: str) -> None:
    """Every skeleton module imports without error."""
    importlib.import_module(f"presagio.{name}")
