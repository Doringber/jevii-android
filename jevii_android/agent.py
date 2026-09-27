from __future__ import annotations

import json
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Protocol

from .device import AndroidDevice
from .models import Action, ActionType


class DecisionEngine(Protocol):
    def decide(self, goal: str, state): ...


class AndroidAgent:
    def __init__(self, device: AndroidDevice, decision_engine: DecisionEngine, max_steps: int = 30, max_same_screen: int = 4):
        self.device = device
        self.decision_engine = decision_engine
        self.max_steps = max_steps
        self.max_same_screen = max_same_screen
        self.history: list[dict] = []

    def _write_trace(self, path: Path) -> None:
        path.write_text(json.dumps(self.history, ensure_ascii=False, indent=2, default=str), encoding="utf-8")

    def run(self, goal: str) -> dict:
        run_id = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        run_dir = Path(os.getenv("JEVII_ANDROID_TRACE_DIR", ".runs")) / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        self.device.trace_dir = run_dir
        last_fingerprint = None
        same_screen_count = 0

        for step in range(1, self.max_steps + 1):
            before = self.device.observe(step=step, recent_actions=self.history)
            if before.fingerprint and before.fingerprint == last_fingerprint:
                same_screen_count += 1
            else:
                same_screen_count = 0
            last_fingerprint = before.fingerprint

            if same_screen_count >= self.max_same_screen:
                return {"ok": False, "status": "stuck", "steps": step, "run_dir": str(run_dir), "reason": "screen did not change across repeated decisions"}

            action: Action = self.decision_engine.decide(goal, before)
            result = self.device.execute(action)
            changed = None

            if action.action not in {ActionType.WAIT, ActionType.COMPLETE, ActionType.FAIL} and result.get("ok"):
                # Verification is semantic: a successful click is not enough; the observed UI should change.
                after = self.device.observe(step=step, recent_actions=self.history)
                changed = bool(before.fingerprint and after.fingerprint and before.fingerprint != after.fingerprint)
                result["screen_changed"] = changed
                result["after_fingerprint"] = after.fingerprint

            record = {
                "step": step,
                "state": {
                    "package": before.package,
                    "activity": before.activity,
                    "screenshot": before.screenshot_path,
                    "stable": before.stable,
                    "fingerprint": before.fingerprint,
                    "screen_quality": before.screen_quality.score if before.screen_quality else None,
                    "candidate_count": len(before.elements),
                },
                "action": action.to_dict(),
                "result": result,
            }
            self.history.append(record)
            self._write_trace(run_dir / "trace.json")

            if action.action == ActionType.COMPLETE:
                return {"ok": True, "status": "complete", "steps": step, "run_dir": str(run_dir)}
            if action.action == ActionType.FAIL:
                return {"ok": False, "status": "failed", "steps": step, "run_dir": str(run_dir), "reason": action.reason}

        return {"ok": False, "status": "max_steps", "steps": self.max_steps, "run_dir": str(run_dir)}
