from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass


class AdbError(RuntimeError):
    pass


@dataclass
class AdbClient:
    serial: str | None = None

    def _base(self) -> list[str]:
        cmd = ["adb"]
        serial = self.serial or os.getenv("ANDROID_SERIAL")
        if serial:
            cmd += ["-s", serial]
        return cmd

    def run(self, *args: str, timeout: int = 15, check: bool = True) -> str:
        try:
            p = subprocess.run(
                [*self._base(), *args],
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
        except FileNotFoundError as e:
            raise AdbError("adb was not found in PATH") from e
        if check and p.returncode != 0:
            raise AdbError(p.stderr.strip() or p.stdout.strip() or f"adb exit={p.returncode}")
        return p.stdout.strip()

    def devices(self) -> list[str]:
        out = self.run("devices")
        devices = []
        for line in out.splitlines()[1:]:
            if "\tdevice" in line:
                devices.append(line.split("\t", 1)[0])
        return devices

    def current_app(self) -> tuple[str | None, str | None]:
        out = self.run("shell", "dumpsys", "window", "windows", check=False)
        patterns = [
            r"mCurrentFocus=.*? ([\w.]+)/([\w.$]+)",
            r"mFocusedApp=.*? ([\w.]+)/([\w.$]+)",
        ]
        for pattern in patterns:
            m = re.search(pattern, out)
            if m:
                return m.group(1), m.group(2)
        return None, None

    def open_app(self, package: str) -> None:
        self.run("shell", "monkey", "-p", package, "-c", "android.intent.category.LAUNCHER", "1")

    def back(self) -> None:
        self.run("shell", "input", "keyevent", "4")

    def home(self) -> None:
        self.run("shell", "input", "keyevent", "3")

    def tap(self, x: int, y: int) -> None:
        self.run("shell", "input", "tap", str(x), str(y))

    def text(self, text: str) -> None:
        escaped = text.replace(" ", "%s")
        self.run("shell", "input", "text", escaped)
