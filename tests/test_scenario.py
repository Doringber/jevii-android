import pytest

from jevii_android.adb import AdbClient
from jevii_android.scenario import load_case, run_case, validate_case


def test_load_case_compiles_nlp_story(tmp_path):
    path = tmp_path / "case.toml"
    path.write_text('''
name = "smoke"
story = "Open IMDb."
[apps]
IMDb = "com.imdb.mobile"
''')
    case = load_case(path)
    assert case["steps"] == [{"open_app": "com.imdb.mobile", "timeout_seconds": 4}]
    assert validate_case(path)["valid"] is True


def test_load_case_rejects_out_of_range_timeout(tmp_path):
    path = tmp_path / "bad.toml"
    path.write_text('''
name = "bad"
[[steps]]
assert_text = "Ready"
timeout_seconds = 61
''')
    with pytest.raises(ValueError, match="at most 60"):
        load_case(path)


class FakeAdb:
    def __init__(self):
        self.commands = []

    def run(self, *args, **kwargs):
        self.commands.append(args)
        if args[:4] == ("shell", "cmd", "package", "resolve-activity"):
            return "com.example/.MainActivity"
        return "started"

    def current_app(self):
        return "com.other", ".MainActivity"


class FakeDevice:
    def __init__(self):
        self.adb = FakeAdb()


def test_failed_app_launch_returns_actionable_issue():
    result = run_case({
        "name": "launch mismatch",
        "steps": [{"open_app": "com.example", "timeout_seconds": 0.01}],
    }, FakeDevice())
    assert result["ok"] is False
    assert result["issues"][0]["code"] == "APP_NOT_FOREGROUND"
    assert "Confirm the package name" in result["issues"][0]["next_check"]
    assert result["results"][0]["issue"] == result["issues"][0]


class OfflineAdb(FakeAdb):
    def run(self, *args, **kwargs):
        raise RuntimeError("adb: device offline")


def test_offline_emulator_is_reported_as_device_issue():
    device = FakeDevice()
    device.adb = OfflineAdb()
    result = run_case({"name": "offline", "steps": [{"open_app": "com.example", "timeout_seconds": 1}]}, device)
    assert result["issues"][0]["code"] == "DEVICE_NOT_READY"
    assert "doctor" in result["issues"][0]["next_check"]


def test_foreground_app_uses_fast_activity_dump(monkeypatch):
    client = AdbClient("emulator-5554")
    calls = []

    def fake_run(*args, **kwargs):
        calls.append(args)
        return "topResumedActivity=ActivityRecord{123 u0 com.example/.Home t1}"

    monkeypatch.setattr(client, "run", fake_run)
    assert client.current_app() == ("com.example", ".Home")
    assert calls == [("shell", "dumpsys", "activity", "activities")]
