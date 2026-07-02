"""Isolation des tests : chaque test travaille dans un tmp_path (output + data)."""

import pytest

from usine.config import settings


@pytest.fixture(autouse=True)
def isolated_dirs(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "output_dir", str(tmp_path / "output"))
    monkeypatch.setattr(settings, "data_dir", str(tmp_path / "data"))
    yield tmp_path
