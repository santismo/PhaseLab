#!/usr/bin/env python3
"""Record and replay click + key automations."""

from __future__ import annotations

import json
import subprocess
import threading
import time
import tkinter as tk
from typing import Any
from tkinter import filedialog, messagebox, ttk


def _which_ok(cmd: str) -> bool:
    try:
        subprocess.run(["/usr/bin/which", cmd], check=True, capture_output=True, text=True)
        return True
    except subprocess.CalledProcessError:
        return False


def _click_point(x: int, y: int) -> None:
    subprocess.run(["cliclick", f"c:{x},{y}"], check=True, capture_output=True, text=True)


def _modifiers_clause(mods: list[str]) -> str:
    if not mods:
        return ""
    tokens = ", ".join(f"{m} down" for m in mods)
    return f" using {{{tokens}}}"


def _osascript(script: str) -> None:
    subprocess.run(["osascript", "-e", script], check=True, capture_output=True, text=True)


def _send_key_char(char: str, mods: list[str]) -> None:
    escaped = char.replace("\\", "\\\\").replace('"', '\\"')
    clause = _modifiers_clause(mods)
    script = f'tell application "System Events" to keystroke "{escaped}"{clause}'
    _osascript(script)


def _send_key_code(code: int, mods: list[str]) -> None:
    clause = _modifiers_clause(mods)
    script = f'tell application "System Events" to key code {code}{clause}'
    _osascript(script)


def _paste_text(text: str) -> None:
    subprocess.run(["pbcopy"], input=text, text=True, check=True)
    _send_key_code(9, ["command"])  # Cmd+V


def _frontmost_app_name() -> str:
    script = 'tell application "System Events" to get name of first application process whose frontmost is true'
    proc = subprocess.run(["osascript", "-e", script], capture_output=True, text=True)
    return (proc.stdout or "").strip()


INSERTABLE_KEYCODES: dict[str, int] = {
    "Down": 125,
    "Up": 126,
    "Left": 123,
    "Right": 124,
    "Enter": 36,
    "Tab": 48,
    "Space": 49,
    "Esc": 53,
    "Backspace": 51,
    "Delete": 117,
}


class ClickKeyAutomationRecorderGUI(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("Click and Key Automation Recorder")
        self.geometry("860x700")
        self.minsize(780, 620)

        self.actions: list[dict[str, Any]] = []
        self.action_lock = threading.Lock()

        self.repeat_var = tk.StringVar(value="100")
        self.startup_delay_var = tk.StringVar(value="3.0")
        self.record_arm_delay_var = tk.StringVar(value="0.35")
        self.speed_var = tk.DoubleVar(value=1.0)
        self.speed_text = tk.StringVar(value="1.00x")
        self.record_count_text = tk.StringVar(value="Recorded actions: 0")
        self.recorder_status_text = tk.StringVar(value="Checking recorder dependency...")
        self.selected_step_text = tk.StringVar(value="Selected step: none")
        self.insert_key_var = tk.StringVar(value="Down")
        self.progressive_var = tk.BooleanVar(value=False)
        self.global_space_hotkey_var = tk.BooleanVar(value=True)
        self.click_step_var = tk.StringVar(value="0")
        self.step_delay_var = tk.StringVar(value="0.000")
        self.paste_list_var = tk.StringVar(value="")
        self.click_retarget_pending_index: int | None = None
        self.last_space_hotkey_at = 0.0

        self.recording = False
        self.playing = False
        self.record_start_requested_at = 0.0
        self.last_event_time = 0.0

        self.play_stop_event = threading.Event()
        self.play_thread: threading.Thread | None = None

        self.frontmost_cache_name = ""
        self.frontmost_cache_time = 0.0

        self.quartz = None
        self.event_tap = None
        self.run_loop_source = None
        self.tap_run_loop = None
        self.tap_thread: threading.Thread | None = None

        self._build_ui()
        self._sync_speed_label()
        self._refresh_actions_view()
        if self._refresh_recorder_state():
            self._ensure_event_tap_running()
        self.protocol("WM_DELETE_WINDOW", self._on_close)

        if not _which_ok("cliclick"):
            self._log("cliclick not found. Install with: brew install cliclick")

        self._log("Ready. Use Start Recording, perform actions in your target app, then Stop Recording.")
        self._log("Space toggles Play/Stop globally (works even when this window is not focused).")

    def _build_ui(self) -> None:
        root = ttk.Frame(self, padding=12)
        root.pack(fill="both", expand=True)

        ttk.Label(root, text="Click and Key Automation Recorder", font=("TkDefaultFont", 13, "bold")).grid(
            row=0, column=0, columnspan=4, sticky="w"
        )
        ttk.Label(
            root,
            text=(
                "Workflow:\n"
                "1) Click Start Recording\n"
                "2) Switch to your target app and do the workflow manually\n"
                "3) Stop recording with Ctrl+Esc or Stop Recording button\n"
                "4) Play the recorded actions with repeat count + speed"
            ),
            justify="left",
        ).grid(row=1, column=0, columnspan=4, sticky="w", pady=(4, 12))

        dep_row = ttk.Frame(root)
        dep_row.grid(row=2, column=0, columnspan=4, sticky="ew")
        dep_row.columnconfigure(2, weight=1)
        ttk.Label(dep_row, text="Recorder dependency:").grid(row=0, column=0, sticky="w")
        ttk.Label(dep_row, textvariable=self.recorder_status_text).grid(row=0, column=1, sticky="w", padx=(8, 8))
        ttk.Label(dep_row, text="(enable Accessibility for keyboard capture in macOS)").grid(row=0, column=2, sticky="w")
        ttk.Checkbutton(
            dep_row,
            text="Global Space hotkey (Play/Stop)",
            variable=self.global_space_hotkey_var,
            command=self._toggle_global_hotkey,
        ).grid(row=1, column=0, columnspan=3, sticky="w", pady=(6, 0))

        ttk.Separator(root, orient="horizontal").grid(row=3, column=0, columnspan=4, sticky="ew", pady=10)

        ttk.Label(root, textvariable=self.record_count_text, font=("TkDefaultFont", 10, "bold")).grid(
            row=4, column=0, columnspan=4, sticky="w"
        )

        button_row = ttk.Frame(root)
        button_row.grid(row=5, column=0, columnspan=4, sticky="w", pady=(8, 8))
        ttk.Button(button_row, text="Start Recording", command=self._start_recording).grid(row=0, column=0, padx=(0, 8))
        ttk.Button(button_row, text="Stop Recording", command=self._stop_recording).grid(row=0, column=1, padx=(0, 8))
        ttk.Button(button_row, text="Clear Recording", command=self._clear_recording).grid(row=0, column=2, padx=(0, 8))
        ttk.Button(button_row, text="Save Recording", command=self._save_recording).grid(row=0, column=3, padx=(0, 8))
        ttk.Button(button_row, text="Load Recording", command=self._load_recording).grid(row=0, column=4, padx=(0, 8))
        ttk.Button(button_row, text="Play Recording", command=self._start_playback).grid(row=0, column=5, padx=(0, 8))
        ttk.Button(button_row, text="Stop Playback", command=self._stop_playback).grid(row=0, column=6)

        ttk.Separator(root, orient="horizontal").grid(row=6, column=0, columnspan=4, sticky="ew", pady=10)

        ttk.Label(root, text="Repeat count").grid(row=7, column=0, sticky="w")
        ttk.Entry(root, textvariable=self.repeat_var, width=12).grid(row=7, column=1, sticky="w")

        ttk.Label(root, text="Playback startup delay (sec)").grid(row=8, column=0, sticky="w")
        ttk.Entry(root, textvariable=self.startup_delay_var, width=12).grid(row=8, column=1, sticky="w")

        ttk.Label(root, text="Record arm delay (sec)").grid(row=9, column=0, sticky="w")
        ttk.Entry(root, textvariable=self.record_arm_delay_var, width=12).grid(row=9, column=1, sticky="w")

        ttk.Label(root, text="Playback speed").grid(row=10, column=0, sticky="w", pady=(8, 0))
        speed_frame = ttk.Frame(root)
        speed_frame.grid(row=10, column=1, columnspan=3, sticky="ew", pady=(8, 0))
        speed_frame.columnconfigure(0, weight=1)
        ttk.Scale(
            speed_frame,
            from_=0.25,
            to=6.0,
            variable=self.speed_var,
            orient="horizontal",
            command=lambda _v: self._sync_speed_label(),
        ).grid(row=0, column=0, sticky="ew")
        ttk.Label(speed_frame, textvariable=self.speed_text, width=8).grid(row=0, column=1, padx=(8, 0))

        ttk.Separator(root, orient="horizontal").grid(row=11, column=0, columnspan=4, sticky="ew", pady=10)

        ttk.Label(root, text="Recorded Steps").grid(row=12, column=0, sticky="w")
        steps_frame = ttk.Frame(root)
        steps_frame.grid(row=13, column=0, columnspan=4, sticky="nsew")
        steps_frame.columnconfigure(0, weight=1)
        steps_frame.rowconfigure(1, weight=1)

        ttk.Label(steps_frame, textvariable=self.selected_step_text).grid(row=0, column=0, sticky="w", pady=(0, 6))

        self.actions_tree = ttk.Treeview(
            steps_frame,
            columns=("idx", "kind", "detail", "dt", "loop"),
            show="headings",
            height=10,
            selectmode="browse",
        )
        self.actions_tree.heading("idx", text="#")
        self.actions_tree.heading("kind", text="Type")
        self.actions_tree.heading("detail", text="Detail")
        self.actions_tree.heading("dt", text="Delay")
        self.actions_tree.heading("loop", text="Loop")
        self.actions_tree.column("idx", width=48, anchor="center", stretch=False)
        self.actions_tree.column("kind", width=100, anchor="center", stretch=False)
        self.actions_tree.column("detail", width=320, anchor="w", stretch=True)
        self.actions_tree.column("dt", width=80, anchor="center", stretch=False)
        self.actions_tree.column("loop", width=220, anchor="w", stretch=True)
        self.actions_tree.grid(row=1, column=0, sticky="nsew")
        self.actions_tree.bind("<<TreeviewSelect>>", self._on_action_select)

        controls = ttk.Frame(steps_frame)
        controls.grid(row=2, column=0, sticky="ew", pady=(8, 0))
        controls.columnconfigure(8, weight=1)

        ttk.Label(controls, text="Insert key after selected:").grid(row=0, column=0, sticky="w")
        ttk.Combobox(
            controls,
            textvariable=self.insert_key_var,
            values=list(INSERTABLE_KEYCODES.keys()),
            state="readonly",
            width=12,
        ).grid(row=0, column=1, padx=(6, 8), sticky="w")
        ttk.Button(controls, text="Insert Key", command=self._insert_key_after_selected).grid(row=0, column=2, padx=(0, 8))
        ttk.Button(controls, text="Remove Step", command=self._remove_selected_action).grid(row=0, column=3, padx=(0, 14))
        ttk.Button(controls, text="Retarget Click", command=self._retarget_selected_click).grid(row=0, column=4, padx=(0, 14))

        ttk.Checkbutton(
            controls,
            text="Loop multiplier (cycle # times)",
            variable=self.progressive_var,
        ).grid(row=0, column=5, padx=(0, 10), sticky="w")
        ttk.Label(controls, text="Click Y step/exec:").grid(row=0, column=6, sticky="w")
        ttk.Entry(controls, textvariable=self.click_step_var, width=6).grid(row=0, column=7, padx=(6, 8), sticky="w")
        ttk.Label(controls, text="Delay sec:").grid(row=0, column=8, sticky="w")
        ttk.Entry(controls, textvariable=self.step_delay_var, width=7).grid(row=0, column=9, padx=(6, 8), sticky="w")
        ttk.Button(controls, text="Apply To Selected", command=self._apply_action_tweaks).grid(row=0, column=10, padx=(0, 8))

        ttk.Label(controls, text="Paste list (comma-separated):").grid(row=1, column=0, sticky="w", pady=(8, 0))
        ttk.Entry(controls, textvariable=self.paste_list_var, width=72).grid(
            row=1, column=1, columnspan=7, sticky="ew", padx=(6, 8), pady=(8, 0)
        )
        ttk.Button(controls, text="Insert Paste List", command=self._insert_paste_list_after_selected).grid(
            row=1, column=8, columnspan=3, sticky="w", pady=(8, 0)
        )

        ttk.Label(
            steps_frame,
            text="Tip: use Retarget Click to remap a click step. Paste List pastes one name per loop cycle. Loop multiplier gives 1x/2x/3x... per cycle. Click Y step moves repeated clicks downward. Ctrl+Esc interrupts record/play.",
        ).grid(row=3, column=0, sticky="w", pady=(6, 0))

        ttk.Separator(root, orient="horizontal").grid(row=14, column=0, columnspan=4, sticky="ew", pady=10)

        ttk.Label(root, text="Log").grid(row=15, column=0, sticky="w")
        self.log_text = tk.Text(root, height=12, wrap="word")
        self.log_text.grid(row=16, column=0, columnspan=4, sticky="nsew")

        root.rowconfigure(13, weight=1)
        root.rowconfigure(16, weight=1)
        root.columnconfigure(3, weight=1)

    def _sync_speed_label(self) -> None:
        self.speed_text.set(f"{self.speed_var.get():.2f}x")

    def _log(self, msg: str) -> None:
        ts = time.strftime("%H:%M:%S")
        self.log_text.insert("end", f"[{ts}] {msg}\n")
        self.log_text.see("end")

    def _set_record_count(self, n: int | None = None) -> None:
        if n is None:
            with self.action_lock:
                n = len(self.actions)
        self.record_count_text.set(f"Recorded actions: {n}")

    def _key_label_from_code(self, code: int) -> str:
        for label, mapped in INSERTABLE_KEYCODES.items():
            if mapped == code:
                return label
        return f"code {code}"

    def _action_summary(self, idx: int, action: dict[str, Any]) -> tuple[str, str, str, str, str]:
        kind = str(action.get("kind", ""))
        detail = ""
        if kind == "click":
            detail = f"x={int(action.get('x', 0))}, y={int(action.get('y', 0))}"
        elif kind == "key_char":
            char = str(action.get("char", ""))
            mods = ",".join(action.get("mods", []))
            detail = f"char='{char}'" if not mods else f"char='{char}' mods={mods}"
        elif kind == "key_code":
            code = int(action.get("code", 0))
            mods = ",".join(action.get("mods", []))
            label = self._key_label_from_code(code)
            detail = label if not mods else f"{label} mods={mods}"
        elif kind == "paste_list":
            values = [str(v) for v in action.get("values", []) if str(v).strip()]
            preview = " | ".join(values[:3])
            if len(values) > 3:
                preview += " | ..."
            detail = f"{len(values)} values: {preview}"
        else:
            detail = str(action)

        dt = float(action.get("dt", 0.0))
        loop_tokens: list[str] = []
        if bool(action.get("progressive_repeat", False)):
            loop_tokens.append("repeat x cycle")
        y_step = int(action.get("loop_y_step", 0))
        if y_step != 0:
            loop_tokens.append(f"y+={y_step}/exec")
        loop_text = ", ".join(loop_tokens) if loop_tokens else "-"
        return (str(idx), kind, detail, f"{dt:.3f}", loop_text)

    def _refresh_actions_view(self) -> None:
        if not hasattr(self, "actions_tree"):
            return
        selected_idx = self._selected_action_index()
        for item in self.actions_tree.get_children():
            self.actions_tree.delete(item)
        with self.action_lock:
            actions_copy = list(self.actions)
        for idx, action in enumerate(actions_copy):
            self.actions_tree.insert("", "end", iid=str(idx), values=self._action_summary(idx, action))

        if selected_idx is not None and selected_idx < len(actions_copy):
            sel = str(selected_idx)
            self.actions_tree.selection_set(sel)
            self.actions_tree.focus(sel)
        else:
            self.selected_step_text.set("Selected step: none")
            self.progressive_var.set(False)
            self.click_step_var.set("0")
            self.step_delay_var.set("0.000")
            self.paste_list_var.set("")

    def _selected_action_index(self) -> int | None:
        if not hasattr(self, "actions_tree"):
            return None
        selected = self.actions_tree.selection()
        if not selected:
            return None
        try:
            return int(selected[0])
        except Exception:  # noqa: BLE001
            return None

    def _on_action_select(self, _event: Any = None) -> None:
        idx = self._selected_action_index()
        if idx is None:
            self.selected_step_text.set("Selected step: none")
            self.progressive_var.set(False)
            self.click_step_var.set("0")
            self.step_delay_var.set("0.000")
            self.paste_list_var.set("")
            return
        with self.action_lock:
            if idx < 0 or idx >= len(self.actions):
                return
            action = dict(self.actions[idx])
        kind = str(action.get("kind", "unknown"))
        self.selected_step_text.set(f"Selected step: #{idx} ({kind})")
        self.progressive_var.set(bool(action.get("progressive_repeat", False)))
        self.click_step_var.set(str(int(action.get("loop_y_step", 0))))
        self.step_delay_var.set(f"{float(action.get('dt', 0.0)):.3f}")
        if action.get("kind") == "paste_list":
            values = [str(v).strip() for v in action.get("values", []) if str(v).strip()]
            self.paste_list_var.set(", ".join(values))
        else:
            self.paste_list_var.set("")

    def _insert_key_after_selected(self) -> None:
        key_name = self.insert_key_var.get().strip()
        key_code = INSERTABLE_KEYCODES.get(key_name)
        if key_code is None:
            messagebox.showerror("Invalid key", "Select a valid key to insert.")
            return

        idx = self._selected_action_index()
        with self.action_lock:
            if idx is None:
                idx = len(self.actions) - 1
            insert_at = max(0, idx + 1)
            self.actions.insert(
                insert_at,
                {"kind": "key_code", "code": int(key_code), "mods": [], "dt": 0.05},
            )
            count = len(self.actions)

        self._set_record_count(count)
        self._refresh_actions_view()
        if count > 0:
            select_id = str(min(insert_at, count - 1))
            self.actions_tree.selection_set(select_id)
            self.actions_tree.focus(select_id)
            self._on_action_select()
        self._log(f"Inserted {key_name} key at step {insert_at}.")

    def _insert_paste_list_after_selected(self) -> None:
        raw = self.paste_list_var.get().strip()
        values = [v.strip() for v in raw.split(",") if v.strip()]
        if not values:
            messagebox.showerror("Invalid list", "Enter at least one comma-separated value.")
            return

        idx = self._selected_action_index()
        with self.action_lock:
            if idx is None:
                idx = len(self.actions) - 1
            insert_at = max(0, idx + 1)
            self.actions.insert(
                insert_at,
                {"kind": "paste_list", "values": values, "dt": 0.05, "progressive_repeat": False, "loop_y_step": 0},
            )
            count = len(self.actions)

        self._set_record_count(count)
        self._refresh_actions_view()
        if count > 0:
            select_id = str(min(insert_at, count - 1))
            self.actions_tree.selection_set(select_id)
            self.actions_tree.focus(select_id)
            self._on_action_select()
        self._log(f"Inserted paste-list step at {insert_at} ({len(values)} values).")

    def _remove_selected_action(self) -> None:
        idx = self._selected_action_index()
        if idx is None:
            messagebox.showinfo("No step selected", "Select a step to remove.")
            return
        with self.action_lock:
            if idx < 0 or idx >= len(self.actions):
                return
            self.actions.pop(idx)
            count = len(self.actions)
        self._set_record_count(count)
        self._refresh_actions_view()
        self._log(f"Removed step {idx}.")

    def _retarget_selected_click(self) -> None:
        if self.recording:
            messagebox.showwarning("Recording active", "Stop recording before retargeting.")
            return
        if self.playing:
            messagebox.showwarning("Playback active", "Stop playback before retargeting.")
            return
        idx = self._selected_action_index()
        if idx is None:
            messagebox.showinfo("No step selected", "Select a click step first.")
            return
        with self.action_lock:
            if idx < 0 or idx >= len(self.actions):
                return
            if self.actions[idx].get("kind") != "click":
                messagebox.showinfo("Not a click step", "Select a click step to retarget.")
                return
        self.click_retarget_pending_index = idx
        self._ensure_event_tap_running()
        self._log(
            "Retarget armed. Click a new location in your target app to update this click step. "
            "Press Ctrl+Esc to cancel."
        )

    def _apply_action_tweaks(self) -> None:
        idx = self._selected_action_index()
        if idx is None:
            messagebox.showinfo("No step selected", "Select a step to edit.")
            return
        try:
            y_step = int(self.click_step_var.get().strip() or "0")
        except ValueError:
            messagebox.showerror("Invalid value", "Click Y step must be a whole number.")
            return
        try:
            delay = float(self.step_delay_var.get().strip() or "0")
        except ValueError:
            messagebox.showerror("Invalid value", "Delay sec must be a number.")
            return
        if delay < 0:
            messagebox.showerror("Invalid value", "Delay sec must be >= 0.")
            return
        paste_values = [v.strip() for v in self.paste_list_var.get().split(",") if v.strip()]
        needs_paste_values = False

        with self.action_lock:
            if idx < 0 or idx >= len(self.actions):
                return
            action = self.actions[idx]
            needs_paste_values = action.get("kind") == "paste_list"
        if needs_paste_values and not paste_values:
            messagebox.showerror("Invalid list", "Paste-list step requires one or more values.")
            return

        with self.action_lock:
            if idx < 0 or idx >= len(self.actions):
                return
            action = self.actions[idx]
            action["dt"] = delay
            action["progressive_repeat"] = bool(self.progressive_var.get())
            if action.get("kind") == "click":
                action["loop_y_step"] = y_step
            else:
                action["loop_y_step"] = 0
            if action.get("kind") == "paste_list":
                action["values"] = paste_values

        self._refresh_actions_view()
        self._log(f"Updated step {idx} loop settings.")

    def _refresh_recorder_state(self) -> bool:
        try:
            import Quartz  # type: ignore
        except Exception as exc:  # noqa: BLE001
            self.quartz = None
            self.recorder_status_text.set(f"Missing Quartz ({exc})")
            return False
        self.quartz = Quartz
        self.recorder_status_text.set("Quartz ready")
        return True

    def _toggle_global_hotkey(self) -> None:
        enabled = bool(self.global_space_hotkey_var.get())
        if enabled:
            if not self._refresh_recorder_state():
                self.global_space_hotkey_var.set(False)
                messagebox.showerror("Recorder unavailable", "Quartz backend unavailable for global hotkeys.")
                return
            self._ensure_event_tap_running()
            self._log("Global Space hotkey enabled.")
            return

        if not self.recording and not self.playing and self.click_retarget_pending_index is None:
            self._stop_event_tap()
        self._log("Global Space hotkey disabled.")

    def _mods_from_flags(self, flags: int) -> list[str]:
        q = self.quartz
        if q is None:
            return []
        mods: list[str] = []
        if flags & int(q.kCGEventFlagMaskControl):
            mods.append("control")
        if flags & int(q.kCGEventFlagMaskCommand):
            mods.append("command")
        if flags & int(q.kCGEventFlagMaskAlternate):
            mods.append("option")
        if flags & int(q.kCGEventFlagMaskShift):
            mods.append("shift")
        return mods

    def _event_tap_callback(self, _proxy: Any, event_type: int, event: Any, _refcon: Any) -> Any:
        q = self.quartz
        if q is None:
            return event

        if event_type == q.kCGEventTapDisabledByTimeout:
            if self.event_tap is not None:
                q.CGEventTapEnable(self.event_tap, True)
            return event
        if event_type == q.kCGEventTapDisabledByUserInput:
            return event

        if event_type == q.kCGEventKeyDown:
            key_code = int(q.CGEventGetIntegerValueField(event, q.kCGKeyboardEventKeycode))
            mods = self._mods_from_flags(int(q.CGEventGetFlags(event)))
            is_repeat = int(q.CGEventGetIntegerValueField(event, q.kCGKeyboardEventAutorepeat))
            if key_code == 53 and "control" in mods:
                if self.recording:
                    self.after(0, self._stop_recording)
                if self.playing:
                    self.after(0, self._stop_playback)
                if self.click_retarget_pending_index is not None:
                    self.click_retarget_pending_index = None
                    self.after(0, lambda: self._log("Retarget cancelled."))
                    if (
                        not self.recording
                        and not self.playing
                        and not self.global_space_hotkey_var.get()
                    ):
                        self.after(0, self._stop_event_tap)
                return event

            if (
                key_code == 49
                and is_repeat == 0
                and not mods
                and self.global_space_hotkey_var.get()
                and not self.recording
            ):
                now = time.monotonic()
                if now - self.last_space_hotkey_at >= 0.25:
                    self.last_space_hotkey_at = now
                    if self.playing:
                        self.after(0, self._stop_playback)
                    else:
                        self.after(0, self._start_playback)
                return event

        if self.click_retarget_pending_index is not None and event_type == q.kCGEventLeftMouseDown:
            if self._is_foreground_blocked():
                return event
            point = q.CGEventGetLocation(event)
            idx = self.click_retarget_pending_index
            self.click_retarget_pending_index = None
            if idx is not None:
                with self.action_lock:
                    if 0 <= idx < len(self.actions) and self.actions[idx].get("kind") == "click":
                        self.actions[idx]["x"] = int(point.x)
                        self.actions[idx]["y"] = int(point.y)
                self.after(0, self._refresh_actions_view)
                self.after(0, lambda i=idx, x=int(point.x), y=int(point.y): self._log(f"Retargeted click step {i} to x={x}, y={y}."))
            if (
                not self.recording
                and not self.playing
                and not self.global_space_hotkey_var.get()
            ):
                self.after(0, self._stop_event_tap)
            return event

        if not self.recording:
            return event

        if not self._should_record_input():
            return event

        if event_type == q.kCGEventLeftMouseDown:
            point = q.CGEventGetLocation(event)
            self._record_action({"kind": "click", "x": int(point.x), "y": int(point.y)})
        elif event_type == q.kCGEventKeyDown:
            key_code = int(q.CGEventGetIntegerValueField(event, q.kCGKeyboardEventKeycode))
            mods = self._mods_from_flags(int(q.CGEventGetFlags(event)))
            self._record_action({"kind": "key_code", "code": key_code, "mods": mods})
        return event

    def _event_tap_worker(self) -> None:
        q = self.quartz
        if q is None:
            self.after(0, lambda: self._log("Recorder backend unavailable."))
            self.after(0, self._stop_recording)
            return

        event_mask = (1 << q.kCGEventLeftMouseDown) | (1 << q.kCGEventKeyDown)
        tap = q.CGEventTapCreate(
            q.kCGSessionEventTap,
            q.kCGHeadInsertEventTap,
            q.kCGEventTapOptionListenOnly,
            event_mask,
            self._event_tap_callback,
            None,
        )
        if tap is None:
            self.after(0, lambda: messagebox.showerror("Recorder error", "Could not create event tap. Grant Accessibility access."))
            self.after(0, lambda: self._log("Failed to create event tap (check Accessibility permissions)."))
            self.after(0, self._stop_recording)
            return

        source = q.CFMachPortCreateRunLoopSource(None, tap, 0)
        loop = q.CFRunLoopGetCurrent()
        self.event_tap = tap
        self.run_loop_source = source
        self.tap_run_loop = loop

        q.CFRunLoopAddSource(loop, source, q.kCFRunLoopCommonModes)
        q.CGEventTapEnable(tap, True)
        if self.recording:
            self.after(0, lambda: self._log("Recording started. Perform actions in your target app. Ctrl+Esc or Stop Recording to end."))
        q.CFRunLoopRun()

        try:
            q.CGEventTapEnable(tap, False)
        except Exception:  # noqa: BLE001
            pass
        try:
            q.CFRunLoopRemoveSource(loop, source, q.kCFRunLoopCommonModes)
        except Exception:  # noqa: BLE001
            pass

    def _stop_event_tap(self) -> None:
        q = self.quartz
        loop = self.tap_run_loop
        if q is not None and loop is not None:
            try:
                q.CFRunLoopStop(loop)
                q.CFRunLoopWakeUp(loop)
            except Exception:  # noqa: BLE001
                pass
        if self.tap_thread and self.tap_thread.is_alive() and threading.current_thread() is not self.tap_thread:
            self.tap_thread.join(timeout=1.0)
        self.tap_thread = None
        self.tap_run_loop = None
        self.run_loop_source = None
        self.event_tap = None

    def _ensure_event_tap_running(self) -> None:
        if self.tap_thread and self.tap_thread.is_alive():
            return
        self.tap_thread = threading.Thread(target=self._event_tap_worker, daemon=True)
        self.tap_thread.start()

    def _parse_float(self, value: str, name: str, min_value: float = 0.0) -> float:
        try:
            parsed = float(value)
        except ValueError as exc:
            raise ValueError(f"{name} must be a number.") from exc
        if parsed < min_value:
            raise ValueError(f"{name} must be >= {min_value}.")
        return parsed

    def _parse_int(self, value: str, name: str, min_value: int = 1) -> int:
        try:
            parsed = int(value)
        except ValueError as exc:
            raise ValueError(f"{name} must be a whole number.") from exc
        if parsed < min_value:
            raise ValueError(f"{name} must be >= {min_value}.")
        return parsed

    def _clear_recording(self) -> None:
        if self.recording:
            self._stop_recording()
        self.click_retarget_pending_index = None
        with self.action_lock:
            self.actions = []
        self._set_record_count(0)
        self._refresh_actions_view()
        self._log("Cleared recorded actions.")

    def _validate_loaded_actions(self, raw_actions: Any) -> list[dict[str, Any]]:
        if not isinstance(raw_actions, list):
            raise ValueError("Invalid file: actions must be a list.")
        validated: list[dict[str, Any]] = []
        for idx, raw in enumerate(raw_actions, start=1):
            if not isinstance(raw, dict):
                raise ValueError(f"Invalid action #{idx}: must be an object.")

            kind = raw.get("kind")
            dt = float(raw.get("dt", 0.0))
            if dt < 0:
                raise ValueError(f"Invalid action #{idx}: dt must be >= 0.")
            progressive_repeat = bool(raw.get("progressive_repeat", False))
            loop_y_step = int(raw.get("loop_y_step", 0))

            if kind == "click":
                x = int(raw["x"])
                y = int(raw["y"])
                validated.append(
                    {
                        "kind": "click",
                        "x": x,
                        "y": y,
                        "dt": dt,
                        "progressive_repeat": progressive_repeat,
                        "loop_y_step": loop_y_step,
                    }
                )
                continue

            if kind == "paste_list":
                raw_values = raw.get("values", [])
                if not isinstance(raw_values, list):
                    raise ValueError(f"Invalid action #{idx}: paste_list values must be a list.")
                values = [str(v).strip() for v in raw_values if str(v).strip()]
                if not values:
                    raise ValueError(f"Invalid action #{idx}: paste_list values cannot be empty.")
                validated.append(
                    {
                        "kind": "paste_list",
                        "values": values,
                        "dt": dt,
                        "progressive_repeat": progressive_repeat,
                        "loop_y_step": 0,
                    }
                )
                continue

            mods_raw = raw.get("mods", [])
            if not isinstance(mods_raw, list):
                raise ValueError(f"Invalid action #{idx}: mods must be a list.")
            mods = [str(m) for m in mods_raw]

            if kind == "key_char":
                char = str(raw.get("char", ""))
                if not char:
                    raise ValueError(f"Invalid action #{idx}: key_char is missing char.")
                validated.append(
                    {
                        "kind": "key_char",
                        "char": char,
                        "mods": mods,
                        "dt": dt,
                        "progressive_repeat": progressive_repeat,
                        "loop_y_step": 0,
                    }
                )
                continue

            if kind == "key_code":
                code = int(raw["code"])
                validated.append(
                    {
                        "kind": "key_code",
                        "code": code,
                        "mods": mods,
                        "dt": dt,
                        "progressive_repeat": progressive_repeat,
                        "loop_y_step": 0,
                    }
                )
                continue

            raise ValueError(f"Invalid action #{idx}: unknown kind '{kind}'.")
        return validated

    def _save_recording(self) -> None:
        if self.recording:
            self._stop_recording()
        with self.action_lock:
            actions = list(self.actions)
        if not actions:
            messagebox.showwarning("No recording", "Record actions first.")
            return

        path = filedialog.asksaveasfilename(
            title="Save Recording",
            defaultextension=".json",
            filetypes=[("JSON files", "*.json"), ("All files", "*.*")],
            initialfile="click_key_recording.json",
        )
        if not path:
            return

        payload = {
            "format": "click-key-automation-v1",
            "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "actions": actions,
        }
        try:
            with open(path, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, indent=2)
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("Save failed", str(exc))
            return

        self._log(f"Saved recording to {path}")

    def _load_recording(self) -> None:
        if self.recording:
            self._stop_recording()
        if self.playing:
            self._stop_playback()
        self.click_retarget_pending_index = None

        path = filedialog.askopenfilename(
            title="Load Recording",
            filetypes=[("JSON files", "*.json"), ("All files", "*.*")],
        )
        if not path:
            return

        try:
            with open(path, "r", encoding="utf-8") as handle:
                payload = json.load(handle)
            if isinstance(payload, list):
                actions = self._validate_loaded_actions(payload)
            else:
                actions = self._validate_loaded_actions(payload.get("actions"))
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("Load failed", str(exc))
            return

        with self.action_lock:
            self.actions = actions
            count = len(self.actions)
        self._set_record_count(count)
        self._refresh_actions_view()
        self._log(f"Loaded recording from {path} ({count} actions)")

    def _is_foreground_blocked(self) -> bool:
        now = time.monotonic()
        if now - self.frontmost_cache_time > 0.08:
            self.frontmost_cache_name = _frontmost_app_name()
            self.frontmost_cache_time = now
        app = self.frontmost_cache_name.lower()
        blocked = ("python", "terminal", "iterm", "phaselab")
        return any(token in app for token in blocked)

    def _should_record_input(self) -> bool:
        if not self.recording:
            return False
        return not self._is_foreground_blocked()

    def _record_action(self, payload: dict[str, Any]) -> None:
        now = time.monotonic()
        dt = max(0.0, now - self.last_event_time)
        self.last_event_time = now
        payload["dt"] = dt
        payload.setdefault("progressive_repeat", False)
        payload.setdefault("loop_y_step", 0)
        with self.action_lock:
            self.actions.append(payload)
            count = len(self.actions)
        self.after(0, lambda c=count: self._set_record_count(c))
        self.after(0, self._refresh_actions_view)

    def _start_recording(self) -> None:
        if self.recording:
            messagebox.showinfo("Already recording", "Recorder is already running.")
            return
        if not self._refresh_recorder_state():
            messagebox.showerror("Missing dependency", "Quartz backend unavailable. Install pyobjc-framework-Quartz.")
            return
        if self.playing:
            messagebox.showwarning("Playback active", "Stop playback before recording.")
            return

        try:
            arm_delay = self._parse_float(self.record_arm_delay_var.get(), "Record arm delay", 0.0)
        except ValueError as exc:
            messagebox.showerror("Invalid settings", str(exc))
            return

        with self.action_lock:
            self.actions = []
        self._set_record_count(0)
        self._refresh_actions_view()
        self.recording = True
        self.record_start_requested_at = time.monotonic()
        self._log(f"Recording requested. Arming in {arm_delay:.2f}s...")

        def arm_worker() -> None:
            time.sleep(arm_delay)
            if not self.recording:
                return
            self.last_event_time = time.monotonic()
            self._ensure_event_tap_running()

        threading.Thread(target=arm_worker, daemon=True).start()

    def _stop_recording(self) -> None:
        if not self.recording:
            return
        self.recording = False
        if not self.global_space_hotkey_var.get() and not self.playing and self.click_retarget_pending_index is None:
            self._stop_event_tap()
        with self.action_lock:
            count = len(self.actions)
        self._set_record_count(count)
        self._log(f"Recording stopped. Captured {count} actions.")

    def _sleep_or_stop(self, seconds: float) -> bool:
        end = time.monotonic() + max(0.0, seconds)
        while time.monotonic() < end:
            if self.play_stop_event.is_set():
                return False
            time.sleep(0.005)
        return True

    def _start_playback(self) -> None:
        if self.recording:
            messagebox.showwarning("Recording active", "Stop recording before playback.")
            return
        if self.playing:
            messagebox.showinfo("Already playing", "Playback is already running.")
            return

        try:
            repeat = self._parse_int(self.repeat_var.get(), "Repeat count", 1)
            startup_delay = self._parse_float(self.startup_delay_var.get(), "Playback startup delay", 0.0)
            speed = max(0.25, float(self.speed_var.get()))
        except ValueError as exc:
            messagebox.showerror("Invalid settings", str(exc))
            return

        with self.action_lock:
            actions = list(self.actions)
        if not actions:
            messagebox.showwarning("No recording", "Record actions first.")
            return

        has_clicks = any(a.get("kind") == "click" for a in actions)
        if has_clicks and not _which_ok("cliclick"):
            messagebox.showerror("Missing cliclick", "Install cliclick first: brew install cliclick")
            return

        self._ensure_event_tap_running()
        self.play_stop_event.clear()
        self.playing = True
        self._log(f"Playback starting in {startup_delay:.2f}s. Repeats={repeat}, speed={speed:.2f}x")

        def worker() -> None:
            try:
                if not self._sleep_or_stop(startup_delay):
                    self.after(0, lambda: self._log("Playback cancelled before start."))
                    return
                action_exec_counts = [0 for _ in actions]
                for cycle in range(1, repeat + 1):
                    if self.play_stop_event.is_set():
                        break
                    self.after(0, lambda c=cycle, r=repeat: self._log(f"Playback cycle {c}/{r}"))
                    for idx, action in enumerate(actions):
                        base_delay = float(action.get("dt", 0.0)) / speed
                        repeats_this_cycle = cycle if bool(action.get("progressive_repeat", False)) else 1
                        for rep in range(repeats_this_cycle):
                            delay = base_delay if rep == 0 else min(0.05, base_delay)
                            if not self._sleep_or_stop(delay):
                                break
                            if self.play_stop_event.is_set():
                                break
                            kind = action.get("kind")
                            if kind == "click":
                                x = int(action["x"])
                                y = int(action["y"])
                                y_step = int(action.get("loop_y_step", 0))
                                y += y_step * action_exec_counts[idx]
                                _click_point(x, y)
                                action_exec_counts[idx] += 1
                            elif kind == "key_char":
                                _send_key_char(str(action["char"]), list(action.get("mods", [])))
                            elif kind == "key_code":
                                _send_key_code(int(action["code"]), list(action.get("mods", [])))
                            elif kind == "paste_list":
                                values = [str(v).strip() for v in action.get("values", []) if str(v).strip()]
                                if values:
                                    value = values[(cycle - 1) % len(values)]
                                    _paste_text(value)
                        if self.play_stop_event.is_set():
                            break
                    if self.play_stop_event.is_set():
                        break
                self.after(0, lambda: self._log("Playback complete." if not self.play_stop_event.is_set() else "Playback stopped."))
            except Exception as exc:  # noqa: BLE001
                self.after(0, lambda: messagebox.showerror("Playback error", str(exc)))
                self.after(0, lambda: self._log(f"Playback error: {exc}"))
            finally:
                self.playing = False
                self.play_stop_event.clear()
                if (
                    not self.recording
                    and not self.global_space_hotkey_var.get()
                    and self.click_retarget_pending_index is None
                ):
                    self._stop_event_tap()

        self.play_thread = threading.Thread(target=worker, daemon=True)
        self.play_thread.start()

    def _stop_playback(self) -> None:
        if not self.playing:
            self._log("Playback is not running.")
            return
        self.play_stop_event.set()
        self._log("Stop requested for playback.")

    def _on_close(self) -> None:
        if self.recording:
            self._stop_recording()
        if self.playing:
            self._stop_playback()
        self.destroy()


def main() -> None:
    app = ClickKeyAutomationRecorderGUI()
    app.mainloop()


if __name__ == "__main__":
    main()
