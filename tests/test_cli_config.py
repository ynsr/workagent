import json
from tests.cli_helpers import _invoke


def test_config_show_and_set_default_harness(isolated_config):
    r1 = _invoke("config", "show", "--json")
    assert r1.exit_code == 0
    assert json.loads(r1.stdout)["default_harness"] == "agy"

    r2 = _invoke("config", "set", "default-harness", "omp", "--json")
    assert r2.exit_code == 0
    assert json.loads(r2.stdout)["default_harness"] == "omp"

    r3 = _invoke("config", "set", "default-harness", "claude")
    assert r3.exit_code == 2


def test_config_set_underscore_key(isolated_config):
    r = _invoke("config", "set", "default_harness", "omp", "--json")
    assert r.exit_code == 0
    assert json.loads(r.stdout)["default_harness"] == "omp"


def test_config_set_invalid_key(isolated_config):
    r = _invoke("config", "set", "invalid-key", "omp")
    assert r.exit_code == 2
    assert "unknown config key" in r.stderr


def test_config_show_human_readable(isolated_config):
    r = _invoke("config", "show")
    assert r.exit_code == 0
    assert "default_harness: agy" in r.stdout
    assert "supported_harnesses:" in r.stdout


def test_config_set_human_readable(isolated_config):
    r = _invoke("config", "set", "default-harness", "omp")
    assert r.exit_code == 0
    assert "default_harness: omp" in r.stdout
    assert "default_harness = omp" in r.stderr


def test_config_set_default_model_and_effort(isolated_config):
    r1 = _invoke("config", "set", "default-model", "gemini-pro", "--json")
    assert r1.exit_code == 0
    assert json.loads(r1.stdout)["default_model"] == "gemini-pro"

    r2 = _invoke("config", "set", "default-effort", "high", "--json")
    assert r2.exit_code == 0
    assert json.loads(r2.stdout)["default_effort"] == "high"

    r3 = _invoke("config", "show", "--json")
    assert r3.exit_code == 0
    data = json.loads(r3.stdout)
    assert data["default_model"] == "gemini-pro"
    assert data["default_effort"] == "high"

    # Clearing back to empty string works
    r4 = _invoke("config", "set", "default-model", "", "--json")
    assert r4.exit_code == 0
    assert json.loads(r4.stdout)["default_model"] == ""
