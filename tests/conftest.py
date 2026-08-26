import io
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "game290.json")


@pytest.fixture(scope="session")
def game290():
    with io.open(FIXTURE, encoding="utf-8") as fh:
        return json.load(fh)
