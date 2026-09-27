from __future__ import annotations

import argparse
import json
import os
import sys

from .agent import AndroidAgent
from .device import AndroidDevice
from .jev import JevDecisionClient
from .scenario import load_case, run_case, validate_case, write_result


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="jevii-android", description="Jev-driven Android agent using uiautomator2 + ADB")
    sub = p.add_subparsers(dest="cmd", required=True)

    doctor = sub.add_parser("doctor")
    doctor.add_argument("--serial")

    run = sub.add_parser("run")
    run.add_argument("goal")
    run.add_argument("--serial")
    run.add_argument("--max-steps", type=int, default=30)

    case = sub.add_parser("run-case", help="run a reusable TOML QA/product use case")
    case.add_argument("file")
    case.add_argument("--serial")
    case.add_argument("--max-steps", type=int, default=30, help="maximum Jev decisions per goal step")
    case.add_argument("--check", action="store_true", help="validate the case without connecting to Android or Jev")
    return p


def doctor(serial: str | None) -> int:
    d = AndroidDevice(serial)
    checks = {}
    try:
        checks["adb_devices"] = d.adb.devices()
    except Exception as e:
        checks["adb_error"] = str(e)
    try:
        d.connect()
        checks["uiautomator2"] = True
        checks["device_info"] = d.u2.info
    except Exception as e:
        checks["uiautomator2"] = False
        checks["uiautomator2_error"] = str(e)
    checks["typesafe_key_present"] = bool(os.getenv("TYPESAFE_API_KEY"))
    print(json.dumps(checks, indent=2, ensure_ascii=False))
    return 0 if checks.get("uiautomator2") and checks.get("adb_devices") else 1


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.cmd == "doctor":
        return doctor(args.serial)
    if args.cmd == "run-case":
        try:
            if args.check:
                write_result(validate_case(args.file))
                return 0
            case = load_case(args.file)
            result = run_case(case, AndroidDevice(args.serial), max_steps=args.max_steps)
            write_result(result)
            return 0 if result["ok"] else 2
        except (OSError, ValueError) as e:
            print(json.dumps({"ok": False, "error": str(e)}, indent=2, ensure_ascii=False), file=sys.stderr)
            return 2
    device = AndroidDevice(args.serial)
    engine = JevDecisionClient()
    result = AndroidAgent(device, engine, max_steps=args.max_steps).run(args.goal)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0 if result.get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(main())
