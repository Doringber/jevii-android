from __future__ import annotations

import json
import time
import tomllib
from pathlib import Path
from typing import Any

from .agent import AndroidAgent
from .device import AndroidDevice
from .jev import JevDecisionClient


STEP_OPERATIONS = {
    "goal", "open_app", "open_url", "wait_ms", "home", "back",
    "relaunch_app", "assert_app", "assert_text",
}


def load_case(path: str | Path) -> dict[str, Any]:
    case_path = Path(path)
    with case_path.open("rb") as f:
        case = tomllib.load(f)
    if not isinstance(case.get("name"), str) or not case["name"].strip():
        raise ValueError("case must define a non-empty name")
    steps = case.get("steps")
    if not isinstance(steps, list) or not steps:
        raise ValueError("case must define at least one [[steps]] entry")
    for index, step in enumerate(steps, 1):
        if not isinstance(step, dict):
            raise ValueError(f"step {index} must be a TOML table")
        operations = STEP_OPERATIONS.intersection(step)
        if len(operations) != 1:
            raise ValueError(f"step {index} must contain exactly one operation from: {', '.join(sorted(STEP_OPERATIONS))}")
        operation = next(iter(operations))
        value = step[operation]
        if operation == "wait_ms":
            if not isinstance(value, int) or value < 0:
                raise ValueError(f"step {index}: wait_ms must be a non-negative integer")
        elif operation in {"home", "back"}:
            if value is not True:
                raise ValueError(f"step {index}: {operation} must be true")
        elif not isinstance(value, str) or not value.strip():
            raise ValueError(f"step {index}: {operation} must be a non-empty string")
        if operation == "open_url" and step.get("package") is not None and not isinstance(step["package"], str):
            raise ValueError(f"step {index}: package must be a string")
    return case


def run_case(case: dict[str, Any], device: AndroidDevice, max_steps: int = 30) -> dict[str, Any]:
    results: list[dict[str, Any]] = []
    started = time.monotonic()
    for index, step in enumerate(case["steps"], 1):
        operation = next(iter(STEP_OPERATIONS.intersection(step)))
        value = step[operation]
        step_started = time.monotonic()
        result: dict[str, Any] = {"step": index, "operation": operation, "ok": True}
        try:
            if operation == "goal":
                goal = AndroidAgent(device, JevDecisionClient(), max_steps=int(step.get("max_steps", max_steps))).run(value)
                result.update(goal)
                result["ok"] = bool(goal.get("ok"))
            elif operation == "open_app":
                device.u2.app_start(value, stop=False)
                result["package"] = value
            elif operation == "open_url":
                args = ["shell", "am", "start", "-a", "android.intent.action.VIEW", "-d", value]
                if step.get("package"):
                    args.extend(["-p", step["package"]])
                result["adb_output"] = device.adb.run(*args)
            elif operation == "wait_ms":
                time.sleep(value / 1000)
            elif operation == "home":
                device.adb.home()
            elif operation == "back":
                device.adb.back()
            elif operation == "relaunch_app":
                device.adb.run("shell", "am", "force-stop", value)
                device.u2.app_start(value, stop=False)
                result["package"] = value
            elif operation == "assert_app":
                actual, activity = device.adb.current_app()
                if actual is None:
                    foreground = device.u2.app_current()
                    actual = foreground.get("package")
                    activity = foreground.get("activity")
                result.update({"expected_package": value, "actual_package": actual, "activity": activity})
                result["ok"] = actual == value
            elif operation == "assert_text":
                exists = device.u2(text=value).exists(timeout=float(step.get("timeout_seconds", 5)))
                result.update({"expected_text": value, "ok": bool(exists)})
        except Exception as e:
            result.update({"ok": False, "error": f"{type(e).__name__}: {e}"})
        result["duration_ms"] = round((time.monotonic() - step_started) * 1000)
        results.append(result)
        if not result["ok"]:
            break
    return {
        "name": case["name"],
        "ok": len(results) == len(case["steps"]) and all(item["ok"] for item in results),
        "steps_completed": len(results),
        "duration_ms": round((time.monotonic() - started) * 1000),
        "results": results,
    }


def validate_case(path: str | Path) -> dict[str, Any]:
    case = load_case(path)
    return {"valid": True, "name": case["name"], "steps": len(case["steps"])}


def write_result(result: dict[str, Any]) -> None:
    print(json.dumps(result, indent=2, ensure_ascii=False))
