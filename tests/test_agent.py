from pathlib import Path

from jevii_android.agent import AndroidAgent
from jevii_android.models import Action, ActionType, AndroidState


class FakeDevice:
    def __init__(self, tmp_path: Path):
        self.trace_dir = tmp_path
        self.n = 0

    def observe(self, step, recent_actions):
        return AndroidState("pkg", "Activity", "<hierarchy/>", None, step=step, recent_actions=recent_actions)

    def execute(self, action):
        self.n += 1
        return {"ok": True}


class FakeEngine:
    def __init__(self):
        self.n = 0

    def decide(self, goal, state):
        self.n += 1
        if self.n == 1:
            return Action(ActionType.WAIT, duration_ms=0)
        return Action(ActionType.COMPLETE)


def test_agent_stops_on_complete(tmp_path, monkeypatch):
    monkeypatch.setenv("JEVII_ANDROID_TRACE_DIR", str(tmp_path))
    out = AndroidAgent(FakeDevice(tmp_path), FakeEngine(), max_steps=5).run("goal")
    assert out["ok"] is True
    assert out["steps"] == 2
