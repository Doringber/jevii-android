from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any

from .adb import AdbClient
from .models import Action, ActionType, AndroidState, TargetStrategy
from .perception import HierarchyParser


class AndroidDevice:
    """Android observer/executor: uiautomator2 first, ADB as a system fallback."""

    def __init__(self, serial: str | None = None, trace_dir: str = ".runs", stable_polls: int = 2):
        self.serial = serial or os.getenv("ANDROID_SERIAL")
        self.adb = AdbClient(self.serial)
        self.trace_dir = Path(trace_dir)
        self.trace_dir.mkdir(parents=True, exist_ok=True)
        self._u2: Any | None = None
        self.parser = HierarchyParser()
        self.stable_polls = stable_polls

    def connect(self) -> None:
        try:
            import uiautomator2 as u2
        except ImportError as e:
            raise RuntimeError("uiautomator2 is not installed; run pip install -r requirements.txt") from e
        self._u2 = u2.connect(self.serial) if self.serial else u2.connect()
        # Accessing device info checks the JSON-RPC connection and works with
        # current uiautomator2 releases, where Device.healthcheck() is absent.
        _ = self._u2.info

    @property
    def u2(self):
        if self._u2 is None:
            self.connect()
        return self._u2

    def _snapshot(self):
        package, activity = self.adb.current_app()
        info = self.u2.info or {}
        xml = self.u2.dump_hierarchy(compressed=False)
        elements, quality = self.parser.parse(xml)
        fp = self.parser.fingerprint(package, activity, elements)
        return package, activity, info, xml, elements, quality, fp

    def observe(self, step: int, recent_actions: list[dict]) -> AndroidState:
        first = self._snapshot()
        stable = True
        last = first
        for _ in range(max(0, self.stable_polls - 1)):
            time.sleep(.18)
            current = self._snapshot()
            stable = stable and current[-1] == last[-1]
            last = current
        package, activity, info, xml, elements, quality, fp = last
        shot = self.trace_dir / f"step-{step:03d}.png"
        self.u2.screenshot(str(shot))
        return AndroidState(
            package=package,
            activity=activity,
            hierarchy_xml=xml,
            screenshot_path=str(shot),
            width=info.get("displayWidth"),
            height=info.get("displayHeight"),
            step=step,
            recent_actions=recent_actions,
            elements=elements,
            screen_quality=quality,
            stable=stable,
            fingerprint=fp,
        )

    def _selector(self, action: Action):
        if not action.target:
            raise ValueError("action has no target")
        t = action.target
        if t.strategy == TargetStrategy.RESOURCE_ID:
            return self.u2(resourceId=t.value)
        if t.strategy == TargetStrategy.TEXT:
            return self.u2(text=t.value)
        if t.strategy == TargetStrategy.DESCRIPTION:
            return self.u2(description=t.value)
        if t.strategy == TargetStrategy.XPATH:
            return self.u2.xpath(t.value)
        raise ValueError(f"selector unsupported for {t.strategy}")

    def execute(self, action: Action) -> dict:
        started = time.time()
        result: dict[str, Any] = {"ok": True, "action": action.to_dict()}
        try:
            if action.action == ActionType.TAP:
                if action.target and action.target.strategy == TargetStrategy.COORDINATES:
                    assert action.target.x is not None and action.target.y is not None
                    try:
                        self.u2.click(action.target.x, action.target.y)
                    except Exception:
                        self.adb.tap(action.target.x, action.target.y)
                elif action.target and action.target.strategy == TargetStrategy.XPATH:
                    if not self.u2.xpath(action.target.value).click_exists(timeout=4):
                        raise RuntimeError("xpath target not found")
                else:
                    sel = self._selector(action)
                    if not sel.wait(timeout=4):
                        raise RuntimeError("target not found")
                    sel.click()
            elif action.action == ActionType.TYPE:
                sel = self._selector(action) if action.target else None
                if sel is not None:
                    if not sel.wait(timeout=4):
                        raise RuntimeError("text target not found")
                    sel.click(); sel.clear_text(); sel.set_text(action.text or "")
                else:
                    try: self.u2.send_keys(action.text or "", clear=False)
                    except Exception: self.adb.text(action.text or "")
            elif action.action == ActionType.SWIPE:
                direction = action.direction or "up"
                w, h = self.u2.window_size()
                points = {
                    "up": (w // 2, int(h * .75), w // 2, int(h * .25)),
                    "down": (w // 2, int(h * .25), w // 2, int(h * .75)),
                    "left": (int(w * .8), h // 2, int(w * .2), h // 2),
                    "right": (int(w * .2), h // 2, int(w * .8), h // 2),
                }
                self.u2.swipe(*points[direction], action.duration_ms / 1000)
            elif action.action == ActionType.BACK:
                try: self.u2.press("back")
                except Exception: self.adb.back()
            elif action.action == ActionType.HOME:
                try: self.u2.press("home")
                except Exception: self.adb.home()
            elif action.action == ActionType.OPEN_APP:
                if not action.package: raise ValueError("open_app requires package")
                try: self.u2.app_start(action.package, stop=False)
                except Exception: self.adb.open_app(action.package)
            elif action.action == ActionType.WAIT:
                time.sleep(max(0, action.duration_ms) / 1000)
            elif action.action in {ActionType.COMPLETE, ActionType.FAIL}:
                pass
            else:
                raise ValueError(f"unsupported action {action.action}")
        except Exception as e:
            result["ok"] = False
            result["error"] = f"{type(e).__name__}: {e}"
        result["duration_ms"] = round((time.time() - started) * 1000)
        return result
