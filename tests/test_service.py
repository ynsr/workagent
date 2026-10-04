"""Tests for `workagent service` (user systemd unit for `serve`)."""
from typer.testing import CliRunner

from workagent import cli, service as _svc
from workagent.webapp import BOOL_FLAGS, VAL_FLAGS

runner = CliRunner()


def test_render_unit_defaults():
    text = _svc.render_unit()
    assert "ExecStart=" in text and "serve --host 127.0.0.1 --port 3344" in text
    assert "Restart=on-failure" in text
    assert "RestartSec=5" in text
    assert "StartLimitBurst=3" in text
    assert "WantedBy=default.target" in text
    assert "After=network-online.target" in text
    assert "Environment=NO_COLOR=1" in text
    assert "Environment=PATH=" in text


def test_render_unit_embeds_install_time_path(monkeypatch):
    monkeypatch.setenv("PATH", "/home/bs/.bun/bin:/usr/bin:/bin")
    text = _svc.render_unit()
    assert "Environment=PATH=/home/bs/.bun/bin:/usr/bin:/bin" in text


def test_unit_path_home_scoped(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    assert _svc.unit_path() == tmp_path / ".config" / "systemd" / "user" / "workagent.service"


def test_install_dry_run_writes_nothing(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    out = _svc.install("127.0.0.1", 3344, yes=True, dry_run=True)
    assert out["dry_run"] is True
    assert not _svc.unit_path().exists()
    assert any("daemon-reload" in a for a in out["actions"])
    assert any("enable-linger" in a for a in out["actions"])


def test_uninstall_removes_unit(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    path = _svc.unit_path()
    path.parent.mkdir(parents=True)
    path.write_text("x")
    monkeypatch.setattr(_svc, "_run_systemctl",
                        lambda *a: type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})())
    out = _svc.uninstall()
    assert not path.exists()
    assert any("removed" in a for a in out["actions"])


def _fake_ok(*args):
    return type("R", (), {"returncode": 0, "stdout": "active", "stderr": ""})()


def test_cli_install_dry_run_json(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    r = runner.invoke(cli.app, ["service", "install", "--dry-run", "--json"])
    assert r.exit_code == 0, r.output
    assert '"dry-run": true' in r.output or '"dry_run": true' in r.output


def test_cli_install_non_interactive_requires_yes(tmp_path, monkeypatch):
    import io
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr("sys.stdin", io.StringIO(""))
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    r = runner.invoke(cli.app, ["service", "install", "--port", "3344"])
    assert r.exit_code == 2


def test_cli_start_stop_status_wrapped(monkeypatch):
    monkeypatch.setattr(_svc, "_run_systemctl", _fake_ok)
    for cmd in ("start", "stop", "restart"):
        r = runner.invoke(cli.app, ["service", cmd, "--json"])
        assert r.exit_code == 0, (cmd, r.output)
    r = runner.invoke(cli.app, ["service", "status", "--json"])
    assert r.exit_code == 0 and '"active": true' in r.output


def test_cli_logs_no_pager_off_tty(monkeypatch):
    got = {}

    def fake_journal(*args):
        got["args"] = args
        return 0

    monkeypatch.setattr(_svc, "_run_journal", fake_journal)
    r = runner.invoke(cli.app, ["service", "logs", "--lines", "5"])
    assert r.exit_code == 0, r.output
    assert "--no-pager" in got["args"]


def test_service_not_web_exposed():
    assert "service" not in BOOL_FLAGS and "service" not in VAL_FLAGS
