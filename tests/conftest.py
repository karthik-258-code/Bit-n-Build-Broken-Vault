import pytest

from tests.helpers.vault import Vault


@pytest.fixture
def vault(tmp_path):
    """A running server with its own data directory and client state directory."""
    v = Vault(tmp_path / "env").start()
    yield v
    v.kill()


@pytest.fixture
def stopped_vault(tmp_path):
    """Same environment, server not started (tests start it with a fault mode)."""
    v = Vault(tmp_path / "env")
    yield v
    v.kill()
