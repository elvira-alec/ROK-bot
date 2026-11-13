"""Version numbers and the panel's update notice (the install itself is checked on
Windows by tests/updater_smoke.py)."""

import time

import yaml

from rokwatch import updater
from rokwatch.panel import Panel


def test_version_numbers():
    assert updater.parse("v1.10") == (1, 10) and updater.parse("1.2") == (1, 2)
    assert updater.newer("1.10", "1.9") and updater.newer("v2.0", "1.15")
    assert not updater.newer("1.2", "1.2") and not updater.newer("1.1", "1.2")
    assert updater.newer("1.0", "dev")                    # a source copy: anything numbered
    assert not updater.newer("latest", "1.0")


def make(tmp_path, monkeypatch, current="1.0", latest="1.1"):
    cfg = tmp_path / "config.yaml"
    cfg.write_text(yaml.safe_dump({"notify": {}}))
    monkeypatch.setattr(updater, "current_version", lambda: current)
    monkeypatch.setattr(updater, "latest_release", lambda timeout=10: {
        "version": latest, "url": "https://example.invalid/x.zip", "notes": "", "page": ""})
    return Panel(str(cfg))


def test_update_found_and_installs_by_itself_when_idle(tmp_path, monkeypatch):
    p = make(tmp_path, monkeypatch)
    assert p.check_for_update()["version"] == "1.1"
    st = p.state()
    assert st["version"] == "1.0" and st["update"]["version"] == "1.1"
    assert 50 <= st["update_in"] <= 60                      # a minute to say "not now"
    launched = []
    monkeypatch.setattr(updater, "can_self_update", lambda: True)
    monkeypatch.setattr(updater, "launch_updater", lambda info: launched.append(info["version"]))
    monkeypatch.setattr("rokwatch.panel.threading.Timer", lambda *a, **k: type("T", (), {"start": lambda s: None})())
    p.update_at = time.time() - 1
    p.update_tick()
    assert launched == ["1.1"] and p.state()["updating"] == "1.1"


def test_not_while_watching_and_not_now(tmp_path, monkeypatch):
    p = make(tmp_path, monkeypatch)
    p.worker.status.phase = "patrolling"
    p.check_for_update()
    assert p.update_at is None                              # waits for you to press Update
    p2 = make(tmp_path, monkeypatch)
    p2.check_for_update()
    p2.update_at = None                                     # "Not now"
    p2.update_tick()
    assert p2.updating is None


def test_no_update_when_up_to_date(tmp_path, monkeypatch):
    p = make(tmp_path, monkeypatch, current="1.3", latest="1.3")
    assert p.check_for_update() is None and p.state()["update"] is None


def test_source_copy_says_download(tmp_path, monkeypatch):
    p = make(tmp_path, monkeypatch)
    p.check_for_update()
    monkeypatch.setattr(updater, "can_self_update", lambda: False)
    assert "download it from GitHub" in p.install_update()
