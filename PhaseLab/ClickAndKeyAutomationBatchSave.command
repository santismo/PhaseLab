#!/bin/zsh
set -euo pipefail

# Click and Key Automation Recorder batch save workflow
#
# This script automates:
# 1) Select source folder (once)
# 2) Select first preset (once)
# 3) Click Save
# 4) Press Return to confirm save
# 5) Move to next preset (Down Arrow)
#
# Requirements:
# - macOS Accessibility permission for Terminal
# - cliclick installed: brew install cliclick

if ! command -v cliclick >/dev/null 2>&1; then
  echo "Missing dependency: cliclick"
  echo "Install with: brew install cliclick"
  exit 1
fi

capture_point() {
  local label="$1"
  read -r "?Move mouse to ${label}, then press Enter... "
  local raw x y
  raw="$(cliclick p)"
  x="$(echo "$raw" | grep -oE '[0-9]+' | sed -n '1p')"
  y="$(echo "$raw" | grep -oE '[0-9]+' | sed -n '2p')"
  if [[ -z "${x:-}" || -z "${y:-}" ]]; then
    echo "Could not read mouse coordinates from: $raw"
    exit 1
  fi
  echo "${x},${y}"
}

key_code() {
  local code="$1"
  osascript >/dev/null <<OSA
 tell application "System Events"
   key code ${code}
 end tell
OSA
}

echo "Click and Key Automation Recorder - Batch Save"
echo "Press Ctrl+C at any time to stop."

read -r "?How many presets to process? " TOTAL
if [[ ! "$TOTAL" =~ ^[0-9]+$ ]] || (( TOTAL < 1 )); then
  echo "Enter a positive whole number."
  exit 1
fi

read -r "?Delay after Save click before Return (seconds, default 0.25): " SAVE_DELAY
SAVE_DELAY=${SAVE_DELAY:-0.25}
read -r "?Delay after Return before next preset (seconds, default 0.20): " NEXT_DELAY
NEXT_DELAY=${NEXT_DELAY:-0.20}

if [[ ! "$SAVE_DELAY" =~ ^[0-9]+([.][0-9]+)?$ ]]; then
  echo "Invalid save delay"
  exit 1
fi
if [[ ! "$NEXT_DELAY" =~ ^[0-9]+([.][0-9]+)?$ ]]; then
  echo "Invalid next delay"
  exit 1
fi

echo
echo "Coordinate capture"
FOLDER_POINT="$(capture_point "Source folder in browser")"
FIRST_PRESET_POINT="$(capture_point "First preset in list")"
SAVE_BUTTON_POINT="$(capture_point "Save button")"
LIST_FOCUS_POINT="$(capture_point "Preset list area (for focus before Down Arrow)")"

echo
echo "Captured:"
echo "Source folder:    ${FOLDER_POINT}"
echo "First preset:     ${FIRST_PRESET_POINT}"
echo "Save button:      ${SAVE_BUTTON_POINT}"
echo "List focus point: ${LIST_FOCUS_POINT}"

echo
echo "Open your target app and make sure the preset window is visible."
echo "Starting in 5 seconds..."
sleep 5

# Initial navigation to first preset
cliclick c:"$FOLDER_POINT"
sleep 0.15
cliclick c:"$FIRST_PRESET_POINT"
sleep 0.15

for ((i=1; i<=TOTAL; i++)); do
  echo "[$i/$TOTAL] Saving preset..."
  cliclick c:"$SAVE_BUTTON_POINT"
  sleep "$SAVE_DELAY"

  # Confirm save dialog/name
  key_code 36   # Return
  sleep "$NEXT_DELAY"

  # Move to next preset except on last loop
  if (( i < TOTAL )); then
    cliclick c:"$LIST_FOCUS_POINT"
    sleep 0.05
    key_code 125  # Down arrow
    sleep 0.12
  fi
done

echo "Done. Processed ${TOTAL} preset(s)."
