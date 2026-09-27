from __future__ import annotations

import json
import time
import tomllib
from pathlib import Path
from typing import Any

from .agent import AndroidAgent
from .device import AndroidDevice
from .jev import JevDecisionClient
from .nlp import compile_story


STEP_OPERATIONS = {
    "goal", "open_app", "open_url", "wait_ms", "home", "back",
    "relaunch_app", "assert_app", "assert_text", "type_text", "enter", "tap_text",
}


def load_case(path: str | Path) -> dict[str, Any]:
    case_path = Path(path)
    with case_path.open("rb") as f:
        case = tomllib.load(f)
    if "story" in case:
        if "steps" in case:
            raise ValueError("Use either a natural-language story or [[steps]]; do not mix both forms")
        case = compile_story(case)
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
        elif operation in {"home", "back", "enter"}:
            if value is not True:
                raise ValueError(f"step {index}: {operation} must be true")
        elif not isinstance(value, str) or not value.strip():
            raise ValueError(f"step {index}: {operation} must be a non-empty string")
        if operation == "open_url" and step.get("package") is not None and not isinstance(step["package"], str):
            raise ValueError(f"step {index}: package must be a string")
        if operation in {"open_app", "relaunch_app"} and step.get("activity") is not None and not isinstance(step["activity"], str):
            raise ValueError(f"step {index}: activity must be a component string")
        if operation == "tap_text" and "optional" in step and not isinstance(step["optional"], bool):
            raise ValueError(f"step {index}: optional must be a boolean")
        for timeout_name in ("timeout_seconds",):
            timeout = step.get(timeout_name)
            if timeout is not None and (isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not 0 < timeout <= 60):
                raise ValueError(f"step {index}: {timeout_name} must be greater than 0 and at most 60")
        if operation == "goal" and "max_steps" in step and (not isinstance(step["max_steps"], int) or not 1 <= step["max_steps"] <= 100):
            raise ValueError(f"step {index}: max_steps must be between 1 and 100")
    return case


def _launch_app(device: AndroidDevice, package: str, activity: str | None, deadline: float) -> None:
    if activity:
        component = activity if "/" in activity else f"{package}/{activity}"
    else:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError(f"Timed out resolving the launcher activity for {package}")
        resolved = device.adb.run("shell", "cmd", "package", "resolve-activity", "--brief", package, timeout=remaining)
        component = next((line.strip() for line in reversed(resolved.splitlines()) if "/" in line), None)
        if not component:
            raise RuntimeError(f"Could not resolve launcher activity for {package}; set activity in the app mapping")
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError(f"Timed out resolving the launcher activity for {package}")
    device.adb.run("shell", "am", "start", "-n", component, timeout=remaining)


def _wait_for_app(device: AndroidDevice, package: str, timeout_seconds: float) -> tuple[bool, str | None, str | None]:
    deadline = time.monotonic() + timeout_seconds
    actual: str | None = None
    activity: str | None = None
    while True:
        remaining = max(0.1, deadline - time.monotonic())
        actual, activity = device.adb.current_app(timeout=remaining)
        if actual == package:
            return True, actual, activity
        if time.monotonic() >= deadline:
            return False, actual, activity
        time.sleep(min(0.12, max(0, deadline - time.monotonic())))


ISSUE_HINTS = {
    "app": "Confirm the package name, emulator state, and app startup screen.",
    "text": "Check the displayed text, app language, and whether the previous step opened the expected screen.",
    "goal": "Open the saved .runs trace and screenshot; use an explicit tap_text/assert_text or revise the Jev goal.",
    "automation": "Check the uiautomator2 connection and device accessibility service, then rerun the saved case.",
    "device": "Run `jevii-android doctor --serial <serial>` and resolve ADB/emulator readiness before retrying.",
}


def run_case(case: dict[str, Any], device: AndroidDevice, max_steps: int = 30) -> dict[str, Any]:
    results: list[dict[str, Any]] = []
    issues: list[dict[str, Any]] = []
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
                launch_timeout = float(step.get("timeout_seconds", 4))
                deadline = time.monotonic() + launch_timeout
                _launch_app(device, value, step.get("activity"), deadline)
                ok, actual, activity = _wait_for_app(device, value, max(0, deadline - time.monotonic()))
                result.update({"package": value, "actual_package": actual, "activity": activity, "ok": ok})
                if not ok:
                    result["error"] = f"App did not reach foreground within {step.get('timeout_seconds', 4)} seconds"
            elif operation == "open_url":
                args = ["shell", "am", "start", "-a", "android.intent.action.VIEW", "-d", value]
                if step.get("package"):
                    args.extend(["-p", step["package"]])
                url_timeout = float(step.get("timeout_seconds", 4))
                deadline = time.monotonic() + url_timeout
                result["adb_output"] = device.adb.run(*args, timeout=url_timeout)
                if step.get("package"):
                    ok, actual, activity = _wait_for_app(device, step["package"], max(0, deadline - time.monotonic()))
                    result.update({"expected_package": step["package"], "actual_package": actual, "activity": activity, "ok": ok})
                    if not ok:
                        result["error"] = f"URL did not foreground {step['package']} (actual: {actual})"
            elif operation == "wait_ms":
                time.sleep(value / 1000)
            elif operation == "home":
                device.adb.home()
            elif operation == "back":
                device.adb.back()
            elif operation == "type_text":
                device.u2.send_keys(value, clear=True)
            elif operation == "tap_text":
                target = device.u2(text=value)
                timeout = float(step.get("timeout_seconds", 0.3 if step.get("optional", False) else 1.5))
                try:
                    target.click(timeout=timeout)
                except Exception as e:
                    if not step.get("optional", False) or type(e).__name__ != "UiObjectNotFoundError":
                        raise
            elif operation == "enter":
                device.u2.press("enter")
            elif operation == "relaunch_app":
                launch_timeout = float(step.get("timeout_seconds", 4))
                deadline = time.monotonic() + launch_timeout
                device.adb.run("shell", "am", "force-stop", value, timeout=deadline - time.monotonic())
                _launch_app(device, value, step.get("activity"), deadline)
                ok, actual, activity = _wait_for_app(device, value, max(0, deadline - time.monotonic()))
                result.update({"package": value, "actual_package": actual, "activity": activity, "ok": ok})
                if not ok:
                    result["error"] = f"App did not reach foreground within {step.get('timeout_seconds', 4)} seconds after restart"
            elif operation == "assert_app":
                _, actual, activity = _wait_for_app(device, value, float(step.get("timeout_seconds", 1.5)))
                result.update({"expected_package": value, "actual_package": actual, "activity": activity})
                result["ok"] = actual == value
                if not result["ok"]:
                    result["error"] = f"Expected {value} in foreground; found {actual}"
            elif operation == "assert_text":
                exists = device.u2(text=value).exists(timeout=float(step.get("timeout_seconds", 1.5)))
                result.update({"expected_text": value, "ok": bool(exists)})
                if not exists:
                    result["error"] = f"Visible text not found: {value}"
        except Exception as e:
            result.update({"ok": False, "error": f"{type(e).__name__}: {e}"})
        result["duration_ms"] = round((time.monotonic() - step_started) * 1000)
        results.append(result)
        if not result["ok"]:
            error_text = str(result.get("error", ""))
            if operation == "goal":
                issue_kind = "goal"
            elif operation in {"open_app", "relaunch_app", "assert_app", "open_url"}:
                issue_kind = "app"
            elif operation in {"assert_text", "tap_text"} and ("Text not found" in error_text or "UiObjectNotFound" in error_text):
                issue_kind = "text"
            elif operation in {"assert_text", "tap_text"} and error_text:
                issue_kind = "automation"
            elif operation in {"assert_text", "tap_text"}:
                issue_kind = "text"
            else:
                issue_kind = "step"
            if "offline" in error_text.lower() or "no devices/emulators" in error_text.lower() or "timeoutexpired" in error_text.lower():
                issue_kind = "device"
            issue_code = {
                "goal": "JEV_GOAL_FAILED",
                "app": "APP_NOT_FOREGROUND",
                "text": "UI_TEXT_NOT_FOUND",
                "automation": "UI_AUTOMATION_ERROR",
                "device": "DEVICE_NOT_READY",
                "step": "STEP_EXECUTION_FAILED",
            }[issue_kind]
            issue = {
                "code": issue_code,
                "step": index,
                "operation": operation,
                "instruction": value,
                "message": result.get("error") or result.get("reason") or (
                    f"Jev goal ended with status '{result.get('status')}'"
                    if operation == "goal" else f"Step {index} did not pass"
                ),
                "next_check": ISSUE_HINTS.get(issue_kind, "Inspect the step result and saved run trace."),
            }
            issues.append(issue)
            result["issue"] = issue
            break
    return {
        "name": case["name"],
        "ok": len(results) == len(case["steps"]) and all(item["ok"] for item in results),
        "steps_completed": len(results),
        "duration_ms": round((time.monotonic() - started) * 1000),
        "slowest_steps": [
            {"step": item["step"], "operation": item["operation"], "duration_ms": item["duration_ms"]}
            for item in sorted(results, key=lambda item: item["duration_ms"], reverse=True)[:3]
        ],
        "results": results,
        "issues": issues,
    }


def validate_case(path: str | Path) -> dict[str, Any]:
    case = load_case(path)
    return {"valid": True, "name": case["name"], "steps": len(case["steps"]), "plan": case["steps"]}


def write_result(result: dict[str, Any]) -> None:
    print(json.dumps(result, indent=2, ensure_ascii=False))
