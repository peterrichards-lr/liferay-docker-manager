import os
import pytest


@pytest.fixture(autouse=True)
def suppress_browser():
    """Globally suppresses browser launching during tests."""
    os.environ["LDM_TEST_MODE"] = "true"
    yield
    # We don't necessarily need to unset it as it's just for the test process
