#!/usr/bin/env bash
set -euo pipefail

AVD_NAME="${1:-}"

find_cmd() {
  local name="$1"
  if command -v "$name" >/dev/null 2>&1; then
    command -v "$name"
    return 0
  fi
  if [[ -n "${ANDROID_HOME:-}" && -x "${ANDROID_HOME}/platform-tools/${name}" ]]; then
    printf '%s\n' "${ANDROID_HOME}/platform-tools/${name}"
    return 0
  fi
  if [[ -n "${ANDROID_SDK_ROOT:-}" && -x "${ANDROID_SDK_ROOT}/platform-tools/${name}" ]]; then
    printf '%s\n' "${ANDROID_SDK_ROOT}/platform-tools/${name}"
    return 0
  fi
  return 1
}

find_emulator() {
  if command -v emulator >/dev/null 2>&1; then
    command -v emulator
    return 0
  fi
  if [[ -n "${ANDROID_HOME:-}" && -x "${ANDROID_HOME}/emulator/emulator" ]]; then
    printf '%s\n' "${ANDROID_HOME}/emulator/emulator"
    return 0
  fi
  if [[ -n "${ANDROID_SDK_ROOT:-}" && -x "${ANDROID_SDK_ROOT}/emulator/emulator" ]]; then
    printf '%s\n' "${ANDROID_SDK_ROOT}/emulator/emulator"
    return 0
  fi
  return 1
}

ADB="$(find_cmd adb || true)"
EMU="$(find_emulator || true)"

if [[ -z "$ADB" ]]; then
  echo "adb not found. Install Android Platform Tools or set ANDROID_HOME/ANDROID_SDK_ROOT." >&2
  exit 1
fi

existing="$($ADB devices | awk '$1 ~ /^emulator-/ && $2 == "device" {print $1; exit}')"

if [[ -z "$existing" ]]; then
  if [[ -z "$EMU" ]]; then
    echo "Android emulator binary not found. Start an AVD from Android Studio Device Manager." >&2
    exit 1
  fi
  if [[ -z "$AVD_NAME" ]]; then
    echo "Usage: $0 <AVD_NAME>" >&2
    echo "Available AVDs:" >&2
    "$EMU" -list-avds >&2 || true
    exit 2
  fi
  echo "Starting AVD: $AVD_NAME"
  nohup "$EMU" -avd "$AVD_NAME" -no-snapshot-save >/tmp/jevii-android-emulator.log 2>&1 &
fi

"$ADB" wait-for-device

serial=""
for _ in $(seq 1 120); do
  serial="$($ADB devices | awk '$1 ~ /^emulator-/ && $2 == "device" {print $1; exit}')"
  if [[ -n "$serial" ]]; then
    booted="$($ADB -s "$serial" shell getprop sys.boot_completed 2>/dev/null | tr -d '\r')"
    if [[ "$booted" == "1" ]]; then
      break
    fi
  fi
  sleep 1
done

if [[ -z "$serial" ]]; then
  echo "Emulator did not become ready." >&2
  exit 1
fi

export ANDROID_SERIAL="$serial"

echo "Emulator ready: $ANDROID_SERIAL"
"$ADB" -s "$ANDROID_SERIAL" shell am start -a android.settings.SETTINGS >/dev/null
sleep 1

if command -v jevii-android >/dev/null 2>&1; then
  jevii-android doctor --serial "$ANDROID_SERIAL"
else
  echo "jevii-android CLI is not installed yet. Run: pip install -e ."
fi

echo
echo "Demo target opened: Android Settings"
echo "Run:"
echo "  jevii-android run --serial $ANDROID_SERIAL \"Open Network & internet settings\""
