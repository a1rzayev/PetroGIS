import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture(scope="session")
def sample_field(tmp_path_factory: pytest.TempPathFactory):
    """The synthetic demo field, generated once per test session."""
    from petrogis.sample_data import generate

    return generate(tmp_path_factory.mktemp("sample_data"), seed=42)


@pytest.fixture(scope="session")
def sample_dir(sample_field) -> Path:
    return sample_field.out_dir
