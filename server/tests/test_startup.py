"""Startup problems should name the fix, not just raise.

A wrong data folder used to surface as a bare PermissionError under uvicorn,
which tells the operator nothing.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import Settings  # noqa: E402
from app.main import create_app  # noqa: E402


def test_a_data_folder_that_cannot_be_created_explains_itself(tmp_path):
    # A file where a directory should be: mkdir fails on every platform.
    blocker = tmp_path / "blocker"
    blocker.write_text("not a folder")

    settings = Settings(data_dir=blocker / "data")
    with pytest.raises(RuntimeError) as failure:
        create_app(settings)

    message = str(failure.value)
    assert "IDROID_DATA_DIR" in message
    assert str(blocker / "data") in message
