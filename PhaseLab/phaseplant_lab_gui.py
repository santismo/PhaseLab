#!/usr/bin/env python3
"""Clickable GUI for phaseplant_lab operations."""

from __future__ import annotations

import copy
import random
import re
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, ttk

from phaseplant_lab import (
    build_enum_pool,
    build_reference_model,
    catalyze_expand_structure,
    count_changed_leaf_values,
    capture_master_pitch_modulation_signatures,
    capture_generator_ingredient_guard,
    capture_protected_switches,
    combine_binary_preset_blobs,
    crossover_states,
    diffuse_lane_fx_and_lfo,
    enforce_generator_ingredient_guard,
    find_unresolved_snapin_targets,
    load_binary_preset_blob,
    load_preset,
    merge_assets,
    mutate_binary_preset_blob,
    mutate_active_generator_parameters,
    mutate_generator_types,
    mutate_global_modulation_amounts,
    mutate_state,
    apply_random_audibility_safety,
    apply_output_audibility_safety,
    remove_new_master_pitch_modulations,
    restore_protected_switches,
    sanitize_state_for_output,
    stamp_binary_metadata_blob,
    stamp_state_metadata,
    synthesize_random_state,
    write_binary_preset_blob,
    write_preset,
)

APP_DIR = Path(__file__).resolve().parent


class PhasePlantLabGUI(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("Phase Plant Lab")
        self.geometry("1120x700")
        self.minsize(980, 620)
        self.configure(bg="#0d1117")

        self.mutate_amount = tk.DoubleVar(value=0.20)
        self.mutate_seed = tk.StringVar(value="")
        self.mutate_count = tk.StringVar(value="1")
        self.mutate_use_full_folder = tk.BooleanVar(value=False)

        self.combine_mix_rate = tk.DoubleVar(value=0.35)
        self.combine_mutate = tk.DoubleVar(value=0.10)
        self.combine_seed = tk.StringVar(value="")
        self.combine_count = tk.StringVar(value="1")
        self.combine_use_full_folder = tk.BooleanVar(value=False)

        self.random_complexity = tk.DoubleVar(value=0.5)
        self.random_seed = tk.StringVar(value="")
        self.random_count = tk.StringVar(value="1")
        self.random_use_guidance = tk.BooleanVar(value=True)

        self.anomalize_count = tk.StringVar(value="1")

        default_preset_dir = APP_DIR.parent
        self.preset_dir_var = tk.StringVar(value=str(default_preset_dir))
        self.output_dir_var = tk.StringVar(value=str(default_preset_dir))

        self.status_var = tk.StringVar(value="Ready.")
        self.output_log_var = tk.StringVar(value="No presets generated yet.")
        self.preset_paths: list[Path] = []
        self._logo_image: tk.PhotoImage | None = None
        self._mode_description_override: str | None = None

        self._apply_dark_theme()
        self._build_ui()
        self._update_folder_indicators()
        self.refresh_preset_list()

    def _apply_dark_theme(self) -> None:
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass

        bg = "#0d1117"
        panel = "#121a25"
        field = "#1a2430"
        fg = "#dbe8ff"
        muted = "#8aa0bf"
        accent = "#6ddf5b"

        style.configure(".", background=bg, foreground=fg)
        style.configure("TFrame", background=bg)
        style.configure("TLabel", background=bg, foreground=fg)
        style.configure("Muted.TLabel", background=bg, foreground=muted)
        style.configure("Header.TLabel", background=bg, foreground=accent, font=("Helvetica Neue", 18, "bold"))
        style.configure("SubHeader.TLabel", background=bg, foreground=muted)
        style.configure("TEntry", fieldbackground=field, foreground=fg, insertcolor=fg)
        style.configure("TNotebook", background=bg, borderwidth=0)
        style.configure("TNotebook.Tab", background=panel, foreground=fg, padding=(10, 6))
        style.map("TNotebook.Tab", background=[("selected", "#1f2d3d")], foreground=[("selected", accent)])
        style.configure("TButton", background=panel, foreground=fg, borderwidth=0, focusthickness=0, padding=(10, 6))
        style.map("TButton", background=[("active", "#243549"), ("pressed", "#1b2735")], foreground=[("active", "#ecf4ff")])
        style.configure("Horizontal.TScale", background=bg, troughcolor="#263446")

    def _create_brand_logo(self, parent: ttk.Frame) -> None:
        logo_path = APP_DIR / "assets" / "phaselab-icon-1024.png"
        if logo_path.is_file():
            try:
                raw_image = tk.PhotoImage(file=str(logo_path))
                factor = max(1, round(max(raw_image.width(), raw_image.height()) / 52))
                self._logo_image = raw_image.subsample(factor, factor)
                tk.Label(parent, image=self._logo_image, bg="#0d1117", bd=0, highlightthickness=0).pack(side="left")
                return
            except tk.TclError:
                self._logo_image = None

        logo = tk.Canvas(parent, width=52, height=52, bg="#0d1117", highlightthickness=0)
        logo.pack(side="left")
        logo.create_oval(4, 4, 48, 48, outline="#58de6a", width=2)
        logo.create_text(26, 26, text="PL", fill="#d8ffe0", font=("Helvetica Neue", 16, "bold"))

    def _build_ui(self) -> None:
        root = ttk.Frame(self, padding=12)
        root.pack(fill="both", expand=True)

        brand = ttk.Frame(root)
        brand.pack(fill="x", pady=(0, 10))
        self._create_brand_logo(brand)
        text_wrap = ttk.Frame(brand)
        text_wrap.pack(side="left", padx=(10, 0))
        ttk.Label(text_wrap, text="PhaseLab", style="Header.TLabel").pack(anchor="w")
        ttk.Label(text_wrap, text="Preset Diffusion + Fusion + Catalyze + Anomalize Toolkit", style="SubHeader.TLabel").pack(anchor="w")

        header = ttk.Frame(root)
        header.pack(fill="x")
        header.columnconfigure(4, weight=1)
        ttk.Button(header, text="Select Preset Folder", command=self.choose_preset_dir).grid(
            row=0, column=0, sticky="w", pady=(0, 6)
        )
        self.preset_folder_indicator = tk.Label(
            header,
            text="?",
            fg="#e5b84b",
            bg="#0d1117",
            font=("Helvetica Neue", 13, "bold"),
        )
        self.preset_folder_indicator.grid(row=0, column=1, sticky="w", padx=(8, 16), pady=(0, 6))

        ttk.Button(header, text="Select Output Folder", command=self.choose_output_dir).grid(
            row=0, column=2, sticky="w", pady=(0, 6)
        )
        self.output_folder_indicator = tk.Label(
            header,
            text="?",
            fg="#e5b84b",
            bg="#0d1117",
            font=("Helvetica Neue", 13, "bold"),
        )
        self.output_folder_indicator.grid(row=0, column=3, sticky="w", padx=(8, 16), pady=(0, 6))

        ttk.Button(header, text="Refresh", command=self.refresh_preset_list).grid(row=0, column=4, sticky="e", pady=(0, 6))

        body = ttk.Panedwindow(root, orient="horizontal")
        body.pack(fill="both", expand=True, pady=(10, 10))

        left = ttk.Frame(body, padding=8)
        body.add(left, weight=1)
        ttk.Label(left, text="Presets In Selected Folder", font=("TkDefaultFont", 10, "bold")).pack(anchor="w")

        list_frame = ttk.Frame(left)
        list_frame.pack(fill="both", expand=True, pady=(8, 0))

        self.preset_list = tk.Listbox(
            list_frame,
            selectmode="multiple",
            exportselection=False,
            bg="#141d29",
            fg="#dbe8ff",
            selectbackground="#2f8f43",
            selectforeground="#ecfff0",
            highlightthickness=1,
            highlightbackground="#263446",
            relief="flat",
        )
        scroll = ttk.Scrollbar(list_frame, orient="vertical", command=self.preset_list.yview)
        self.preset_list.configure(yscrollcommand=scroll.set)
        self.preset_list.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        self.preset_list.bind("<<ListboxSelect>>", self._on_listbox_select)

        ttk.Label(
            left,
            text=(
                "Click to toggle select/unselect presets (no Cmd/Ctrl needed). "
                "Diffusion: 1 preset. Fusion: 2+ presets or full folder. "
                "Catalyze: smart generation (optional guidance from existing presets). "
                "Anomalize: surprise mode (mixes all modes)."
            ),
            foreground="#555",
            wraplength=300,
        ).pack(anchor="w", pady=(8, 0))

        right = ttk.Frame(body, padding=8)
        body.add(right, weight=2)

        notebook = ttk.Notebook(right)
        notebook.pack(fill="both", expand=True)
        self.notebook = notebook

        tab_mutate = ttk.Frame(notebook, padding=12)
        tab_combine = ttk.Frame(notebook, padding=12)
        tab_random = ttk.Frame(notebook, padding=12)
        tab_anomalize = ttk.Frame(notebook, padding=12)

        notebook.add(tab_mutate, text="Diffusion")
        notebook.add(tab_combine, text="Fusion")
        notebook.add(tab_random, text="Catalyze")
        notebook.add(tab_anomalize, text="Anomalize")
        notebook.bind("<<NotebookTabChanged>>", self._on_tab_changed)

        self._build_mutate_tab(tab_mutate)
        self._build_combine_tab(tab_combine)
        self._build_random_tab(tab_random)
        self._build_anomalize_tab(tab_anomalize)

        status = ttk.Frame(root)
        status.pack(fill="x")
        ttk.Label(status, textvariable=self.status_var, foreground="#1f5f3d").pack(anchor="w")

    def _add_labeled_entry(
        self,
        parent: ttk.Frame,
        row: int,
        label: str,
        variable: tk.StringVar,
        width: int = 16,
    ) -> None:
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", pady=6)
        ttk.Entry(parent, textvariable=variable, width=width).grid(row=row, column=1, sticky="w", pady=6)

    def _add_slider(self, parent: ttk.Frame, row: int, label: str, variable: tk.DoubleVar) -> None:
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", pady=6)
        holder = ttk.Frame(parent)
        holder.grid(row=row, column=1, sticky="w", pady=6)
        scale = ttk.Scale(holder, from_=0.0, to=1.0, variable=variable, orient="horizontal", length=260)
        scale.pack(side="left")
        value_label = ttk.Label(holder, text=f"{variable.get():.2f}", width=5)
        value_label.pack(side="left", padx=(8, 0))

        def sync_label(*_: object) -> None:
            value_label.configure(text=f"{variable.get():.2f}")

        variable.trace_add("write", sync_label)
        scale.configure(command=lambda _: sync_label())

    def _build_mutate_tab(self, parent: ttk.Frame) -> None:
        parent.columnconfigure(1, weight=1)
        ttk.Label(
            parent,
            text="Diffusion morphs one preset by mutating safe parameter values.",
            foreground="#8b9bb2",
        ).grid(row=0, column=0, columnspan=2, sticky="w", pady=6)
        self._add_slider(parent, 1, "Diffusion Amount (0..1)", self.mutate_amount)
        self._add_labeled_entry(parent, 2, "Seed (optional)", self.mutate_seed)
        self._add_labeled_entry(parent, 3, "How Many To Generate", self.mutate_count)
        ttk.Checkbutton(parent, text="Use Full Folder As Source Pool", variable=self.mutate_use_full_folder).grid(
            row=4, column=0, columnspan=2, sticky="w", pady=(2, 2)
        )
        ttk.Label(
            parent,
            text="Output name auto: <selected preset>.phaseplant (adds number if needed)",
            foreground="#8aa0bf",
        ).grid(row=5, column=0, columnspan=2, sticky="w", pady=(4, 0))

        ttk.Button(parent, text="Diffuse Preset", command=self.run_mutate).grid(
            row=6, column=0, columnspan=2, sticky="w", pady=(14, 0)
        )

    def _build_combine_tab(self, parent: ttk.Frame) -> None:
        parent.columnconfigure(1, weight=1)
        ttk.Label(
            parent,
            text="Fusion blends multiple presets into one hybrid sound.",
            foreground="#8b9bb2",
        ).grid(row=0, column=0, columnspan=2, sticky="w", pady=6)
        self._add_slider(parent, 1, "Mix Rate (0..1)", self.combine_mix_rate)
        self._add_slider(parent, 2, "Fusion Amount (0..1)", self.combine_mutate)
        self._add_labeled_entry(parent, 3, "Seed (optional)", self.combine_seed)
        self._add_labeled_entry(parent, 4, "How Many To Generate", self.combine_count)
        ttk.Checkbutton(parent, text="Use Full Folder Instead Of Selection", variable=self.combine_use_full_folder).grid(
            row=5, column=0, columnspan=2, sticky="w", pady=(2, 2)
        )
        ttk.Label(
            parent,
            text="Output name auto: mashed selected names.phaseplant (adds number if needed)",
            foreground="#8aa0bf",
        ).grid(row=6, column=0, columnspan=2, sticky="w", pady=(4, 0))

        ttk.Button(parent, text="Fuse Preset", command=self.run_combine).grid(
            row=7, column=0, columnspan=2, sticky="w", pady=(14, 0)
        )

    def _build_random_tab(self, parent: ttk.Frame) -> None:
        parent.columnconfigure(1, weight=1)

        ttk.Label(
            parent,
            text="Catalyze generates smart presets automatically and can use guidance from existing presets.",
            foreground="#8b9bb2",
        ).grid(row=0, column=0, columnspan=2, sticky="w", pady=6)
        self._add_slider(parent, 1, "Complexity (Simple -> Complex)", self.random_complexity)
        self._add_labeled_entry(parent, 2, "Seed (optional)", self.random_seed)
        self._add_labeled_entry(parent, 3, "How Many To Generate", self.random_count)
        ttk.Checkbutton(parent, text="Use Existing Presets As Structure Guidance (User + Factory)", variable=self.random_use_guidance).grid(
            row=4, column=0, columnspan=2, sticky="w", pady=(2, 2)
        )
        ttk.Label(
            parent,
            text="Output name auto: random.phaseplant (adds number if needed)",
            foreground="#8aa0bf",
        ).grid(row=5, column=0, columnspan=2, sticky="w", pady=(4, 0))

        ttk.Button(parent, text="Catalyze Preset", command=self.run_random).grid(
            row=6, column=0, columnspan=2, sticky="w", pady=(14, 0)
        )

    def _build_anomalize_tab(self, parent: ttk.Frame) -> None:
        parent.columnconfigure(1, weight=1)

        ttk.Label(
            parent,
            text=(
                "Anomalize is surprise mode: each output randomly uses Diffusion, Fusion, "
                "or Catalyze with randomized settings from the selected preset folder."
            ),
            foreground="#8b9bb2",
            wraplength=620,
        ).grid(row=0, column=0, columnspan=2, sticky="w", pady=6)
        self._add_labeled_entry(parent, 1, "How Many To Generate", self.anomalize_count)
        ttk.Label(
            parent,
            text="No manual controls here. PhaseLab randomizes mode and values each generation.",
            foreground="#8aa0bf",
        ).grid(row=2, column=0, columnspan=2, sticky="w", pady=(4, 0))
        ttk.Button(parent, text="Anomalize", command=self.run_anomalize).grid(
            row=3, column=0, columnspan=2, sticky="w", pady=(14, 0)
        )

    def get_preset_dir(self) -> Path:
        return Path(self.preset_dir_var.get()).expanduser()

    def get_output_dir(self) -> Path:
        return Path(self.output_dir_var.get()).expanduser()

    def _set_indicator(self, label: tk.Label, ok: bool) -> None:
        if ok:
            label.configure(text="✓", fg="#6ddf5b")
        else:
            label.configure(text="✗", fg="#e45d5d")

    def _update_folder_indicators(self) -> None:
        preset_ok = self.get_preset_dir().is_dir()
        output_dir = self.get_output_dir()
        output_ok = output_dir.is_dir() or output_dir.parent.is_dir()
        self._set_indicator(self.preset_folder_indicator, preset_ok)
        self._set_indicator(self.output_folder_indicator, output_ok)

    def choose_preset_dir(self) -> None:
        chosen = filedialog.askdirectory(initialdir=str(self.get_preset_dir()))
        if not chosen:
            return
        self.preset_dir_var.set(chosen)
        self._update_folder_indicators()
        self.refresh_preset_list()

    def choose_output_dir(self) -> None:
        chosen = filedialog.askdirectory(initialdir=str(self.get_output_dir()))
        if not chosen:
            return
        self.output_dir_var.set(chosen)
        self._update_folder_indicators()

    def _is_mutate_tab_active(self) -> bool:
        current = self.notebook.tab(self.notebook.select(), "text")
        return current == "Diffusion"

    def _enforce_mutate_single_selection(self) -> None:
        selected = list(self.preset_list.curselection())
        if len(selected) <= 1:
            return
        keep = selected[-1]
        self.preset_list.selection_clear(0, tk.END)
        self.preset_list.selection_set(keep)

    def _on_tab_changed(self, _event: object | None = None) -> None:
        if self._is_mutate_tab_active():
            self._enforce_mutate_single_selection()

    def _on_listbox_select(self, _event: object | None = None) -> None:
        if self._is_mutate_tab_active():
            self._enforce_mutate_single_selection()

    def refresh_preset_list(self) -> None:
        preset_dir = self.get_preset_dir()
        self.preset_list.delete(0, tk.END)
        self.preset_paths = []
        self._update_folder_indicators()
        if not preset_dir.is_dir():
            self.status_var.set("Preset folder not found. Select a valid preset folder.")
            return
        # Recursive scan so selecting a top-level factory folder includes category subfolders.
        all_paths = sorted(path for path in preset_dir.rglob("*.phaseplant") if path.is_file())
        editable = 0
        binary = 0
        for path in all_paths:
            self.preset_paths.append(path)
            rel_name = str(path.relative_to(preset_dir))
            if self._is_zip_preset(path):
                editable += 1
                display = rel_name
            else:
                binary += 1
                display = f"[factory] {rel_name}"
            self.preset_list.insert(tk.END, display)

        if binary:
            self.status_var.set(f"Loaded {len(all_paths)} presets ({editable} editable, {binary} factory/binary).")
        else:
            self.status_var.set(f"Loaded {len(all_paths)} presets.")

    def _is_zip_preset(self, path: Path) -> bool:
        try:
            with path.open("rb") as fh:
                return fh.read(4) == b"PK\x03\x04"
        except OSError:
            return False

    def selected_preset_paths(self) -> list[Path]:
        return [self.preset_paths[i] for i in self.preset_list.curselection() if 0 <= i < len(self.preset_paths)]

    def load_editable_preset(self, path: Path):
        if not self._is_zip_preset(path):
            raise ValueError(
                f"{path.name} is a factory/binary preset and cannot be edited directly by PhaseLab yet. "
                "Open it in Phase Plant and save/copy it to User Presets first."
            )
        try:
            return load_preset(path)
        except SystemExit as exc:
            raise ValueError(f"Failed to read preset: {path.name}") from exc

    def parse_seed(self, raw: str) -> int | None:
        text = raw.strip()
        if not text:
            return None
        return int(text)

    def parse_ratio(self, raw: str, field_name: str) -> float:
        value = float(raw.strip())
        if value < 0 or value > 1:
            raise ValueError(f"{field_name} must be between 0 and 1")
        return value

    def parse_count(self, raw: str, field_name: str) -> int:
        value = int(raw.strip())
        if value < 1:
            raise ValueError(f"{field_name} must be >= 1")
        if value > 500:
            raise ValueError(f"{field_name} must be <= 500")
        return value

    def _clean_stem(self, raw: str) -> str:
        stem = Path(raw).stem.strip()
        stem = re.sub(r"\s+", " ", stem)
        stem = stem.replace("/", " ").replace("\\", " ")
        stem = stem.strip(" ._-")
        return stem or "generated"

    def _unique_output_path(self, stem: str, output_dir: Path) -> Path:
        output_dir.mkdir(parents=True, exist_ok=True)
        clean = self._clean_stem(stem)
        candidate = output_dir / f"{clean}.phaseplant"
        if not candidate.exists():
            return candidate
        idx = 2
        while True:
            numbered = output_dir / f"{clean}{idx}.phaseplant"
            if not numbered.exists():
                return numbered
            idx += 1

    def mash_combine_name(self, preset_names: list[str]) -> str:
        stems = [self._clean_stem(name) for name in preset_names if self._clean_stem(name)]
        if not stems:
            return "combined"
        if len(stems) == 1:
            return stems[0]

        max_len = max(len(stem) for stem in stems)
        word_lists = [re.findall(r"[A-Za-z0-9]+", stem) or [stem] for stem in stems]

        result_words: list[str] = []
        seen: set[str] = set()
        index = 0
        while True:
            added = False
            for words in word_lists:
                if index >= len(words):
                    continue
                word = words[index]
                key = word.lower()
                if key in seen:
                    continue
                candidate = " ".join(result_words + [word]) if result_words else word
                if len(candidate) <= max_len:
                    result_words.append(word)
                    seen.add(key)
                    added = True
            if not added:
                break
            index += 1

        mashed = " ".join(result_words).strip()
        if not mashed:
            mashed = stems[0]
        # If mash collapsed to one parent-like name, blend compact chunks from all names.
        lowered_stems = {stem.lower() for stem in stems}
        if mashed.lower() in lowered_stems:
            compact = [re.sub(r"[^A-Za-z0-9]+", "", stem) for stem in stems]
            compact = [text for text in compact if text]
            if compact:
                chunk = max(1, min(4, max_len // max(2, len(compact) * 2)))
                pos = [0 for _ in compact]
                blended_parts: list[str] = []
                while len("".join(blended_parts)) < max_len:
                    progressed = False
                    for idx, text in enumerate(compact):
                        if pos[idx] >= len(text):
                            continue
                        part = text[pos[idx] : pos[idx] + chunk]
                        pos[idx] += len(part)
                        if not part:
                            continue
                        blended_parts.append(part)
                        progressed = True
                        if len("".join(blended_parts)) >= max_len:
                            break
                    if not progressed:
                        break
                blended = "".join(blended_parts)[:max_len]
                if blended:
                    mashed = blended
        if len(mashed) > max_len:
            mashed = mashed[:max_len].rstrip(" _-")
        return mashed or stems[0][:max_len]

    def build_mutate_output_path(self, preset_name: str, output_dir: Path) -> Path:
        return self._unique_output_path(self._clean_stem(preset_name), output_dir)

    def build_combine_output_path(self, preset_names: list[str], output_dir: Path) -> Path:
        mashed = self.mash_combine_name(preset_names)
        return self._unique_output_path(mashed, output_dir)

    def build_random_output_path(self, output_dir: Path) -> Path:
        return self._unique_output_path("random", output_dir)

    def summarize_outputs(self, outputs: list[Path]) -> str:
        if not outputs:
            return "No files created."
        if len(outputs) <= 3:
            return ", ".join(path.name for path in outputs)
        return f"{outputs[0].name}, {outputs[1].name}, {outputs[2].name}, ... ({len(outputs)} files)"

    def set_output_log(self, mode: str, outputs: list[Path]) -> None:
        if not outputs:
            self.output_log_var.set(f"{mode}: no files created.")
            return
        if len(outputs) <= 8:
            names = ", ".join(path.name for path in outputs)
        else:
            names = ", ".join(path.name for path in outputs[:8]) + f", ... ({len(outputs)} files)"
        self.output_log_var.set(f"{mode}: {names}")

    def _phase_lab_description(self, mode: str) -> str:
        if self._mode_description_override:
            return self._mode_description_override
        return f"Generated by PhaseLab ({mode})."

    def _stamp_output_state(self, state: dict, mode: str, output_stem: str) -> None:
        stamp_state_metadata(state, author="PhaseLab", description=self._phase_lab_description(mode), preset_name=output_stem)

    def _stamp_output_blob(self, blob: bytes, mode: str) -> bytes:
        return stamp_binary_metadata_blob(blob, author="PhaseLab", description=self._phase_lab_description(mode))

    def _set_listbox_selection(self, indices: list[int]) -> None:
        self.preset_list.selection_clear(0, tk.END)
        for idx in sorted(set(indices)):
            if 0 <= idx < len(self.preset_paths):
                self.preset_list.selection_set(idx)

    def _discover_global_editable_paths(self, limit: int = 1200) -> list[Path]:
        roots = [
            APP_DIR.parent,
            Path.home() / "Library/Audio/Presets/Kilohearts/Phase Plant/User Presets",
            self.get_preset_dir(),
        ]
        paths: list[Path] = []
        seen: set[str] = set()
        for root in roots:
            if not root.is_dir():
                continue
            for path in root.rglob("*.phaseplant"):
                if not path.is_file() or not self._is_zip_preset(path):
                    continue
                key = str(path.resolve())
                if key in seen:
                    continue
                seen.add(key)
                paths.append(path)
                if len(paths) >= limit:
                    return paths
        return paths

    def _load_valid_editable_presets(self, paths: list[Path]) -> tuple[list, list[str]]:
        loaded = [self.load_editable_preset(path) for path in paths]
        presets: list = []
        skipped_invalid: list[str] = []
        for preset in loaded:
            unresolved = find_unresolved_snapin_targets(preset.state)
            if unresolved:
                skipped_invalid.append(f"{preset.source.name} ({len(unresolved)} unresolved targets)")
                continue
            presets.append(preset)
        return presets, skipped_invalid

    def _merge_unique_presets(self, base: list, incoming: list, max_items: int | None = None) -> list:
        merged = list(base)
        seen = {str(Path(preset.source).resolve()) for preset in merged}
        for preset in incoming:
            key = str(Path(preset.source).resolve())
            if key in seen:
                continue
            merged.append(preset)
            seen.add(key)
            if max_items is not None and len(merged) >= max_items:
                break
        return merged

    def _load_internal_template_bank(self) -> tuple[list, int]:
        template_dir = APP_DIR / "assets" / "internal_templates"
        if not template_dir.is_dir():
            return [], 0
        paths = sorted(path for path in template_dir.glob("*.phaseplant") if path.is_file())
        presets: list = []
        skipped = 0
        for path in paths:
            if not self._is_zip_preset(path):
                skipped += 1
                continue
            try:
                preset = load_preset(path)
            except SystemExit:
                skipped += 1
                continue
            unresolved = find_unresolved_snapin_targets(preset.state)
            if unresolved:
                skipped += 1
                continue
            presets.append(preset)
        return presets, skipped

    def _generate_random_from_editable_pool(
        self,
        presets: list,
        rng: random.Random,
        count: int,
        complexity: float,
        output_dir: Path,
    ) -> tuple[list[Path], int, int, int]:
        donor_states = [preset.state for preset in presets]
        enum_pool = build_enum_pool(donor_states)
        reference_model = build_reference_model(donor_states)
        merged_assets, merged_asset_warnings = merge_assets(presets)
        outputs: list[Path] = []
        total_generated_changes = 0
        total_repairs = 0
        warning_count = len(merged_asset_warnings)
        for _ in range(count):
            base_preset = rng.choice(presets)
            protected_switches = capture_protected_switches(base_preset.state)
            state, generated_changes = synthesize_random_state(
                base_preset.state,
                reference_model,
                enum_pool,
                rng,
                complexity,
            )
            total_repairs += restore_protected_switches(state, protected_switches)
            generated_changes += catalyze_expand_structure(state, donor_states, rng, complexity)
            output_path = self.build_random_output_path(output_dir)
            self._stamp_output_state(state, "catalyze", output_path.stem)
            total_repairs += sanitize_state_for_output(state)
            total_repairs += apply_random_audibility_safety(state)
            total_generated_changes += generated_changes
            write_preset(output_path, state, merged_assets)
            outputs.append(output_path)
        return outputs, total_generated_changes, total_repairs, warning_count

    def _generate_random_from_binary_pool(
        self,
        binary_paths: list[Path],
        rng: random.Random,
        count: int,
        complexity: float,
        output_dir: Path,
    ) -> tuple[list[Path], int, int, int]:
        binary_blob_cache: dict[Path, bytes] = {}

        def blob_for(path: Path) -> bytes:
            if path not in binary_blob_cache:
                binary_blob_cache[path] = load_binary_preset_blob(path)
            return binary_blob_cache[path]

        outputs: list[Path] = []
        total_crossover = 0
        total_mutation = 0
        fallback_parent_selections = 0
        mix_rate = 0.20 + 0.55 * complexity
        mutate_amount = 0.10 + 0.45 * complexity
        for _ in range(count):
            output_path = self.build_random_output_path(output_dir)
            base_path = rng.choice(binary_paths)
            base_blob = blob_for(base_path)
            donor_pool = [path for path in binary_paths if path != base_path]
            donor_blobs: list[bytes] = []
            if donor_pool:
                donor_take = min(max(1, int(round(1 + complexity * 3))), len(donor_pool))
                donor_paths = rng.sample(donor_pool, donor_take)
                donor_blobs = [blob_for(path) for path in donor_paths]

            if donor_blobs:
                out_blob, cross_changes, mut_changes = combine_binary_preset_blobs(
                    base_blob,
                    donor_blobs,
                    rng,
                    mix_rate=mix_rate,
                    mutate_amount=mutate_amount,
                )
            else:
                out_blob, mut_changes = mutate_binary_preset_blob(
                    base_blob,
                    rng,
                    amount=mutate_amount,
                    donor_blobs=[blob_for(path) for path in binary_paths if path != base_path][:16],
                )
                cross_changes = 0

            if (cross_changes + mut_changes == 0 or out_blob == base_blob) and len(binary_paths) > 1:
                alternatives = [path for path in binary_paths if path != base_path]
                if alternatives:
                    out_blob = blob_for(rng.choice(alternatives))
                    fallback_parent_selections += 1

            out_blob = self._stamp_output_blob(out_blob, "catalyze")
            write_binary_preset_blob(output_path, out_blob)
            outputs.append(output_path)
            total_crossover += cross_changes
            total_mutation += mut_changes

        return outputs, total_crossover, total_mutation, fallback_parent_selections

    def _tokenize_text(self, text: str) -> set[str]:
        return {token for token in re.findall(r"[a-z0-9]+", text.lower()) if len(token) >= 2}

    def _extract_factory_source_tokens(self, path: Path) -> set[str]:
        tokens = self._tokenize_text(path.stem)
        try:
            blob = path.read_bytes()
        except OSError:
            return tokens
        for pat in (rb'"description"\s*:\s*"([^"]*)"', rb'"author"\s*:\s*"([^"]*)"'):
            match = re.search(pat, blob)
            if not match:
                continue
            try:
                text = match.group(1).decode("utf-8", errors="ignore")
            except Exception:
                continue
            tokens.update(self._tokenize_text(text))
        return tokens

    def _editable_preset_tokens(self, preset: object) -> set[str]:
        state = getattr(preset, "state", {})
        source = getattr(preset, "source", None)
        tokens: set[str] = set()
        if source is not None:
            tokens.update(self._tokenize_text(Path(source).stem))
        if isinstance(state, dict):
            meta = state.get("meta")
            if isinstance(meta, dict):
                author = meta.get("author")
                description = meta.get("description")
                if isinstance(author, str):
                    tokens.update(self._tokenize_text(author))
                if isinstance(description, str):
                    tokens.update(self._tokenize_text(description))
            preset_obj = state.get("preset")
            if isinstance(preset_obj, dict):
                name = preset_obj.get("name")
                if isinstance(name, str):
                    tokens.update(self._tokenize_text(name))
        return tokens

    def _is_phaselab_generated_preset(self, preset: object) -> bool:
        source = getattr(preset, "source", None)
        if source is not None:
            source_path = str(Path(source).as_posix()).lower()
            if "phaselab output" in source_path:
                return True
        state = getattr(preset, "state", {})
        if isinstance(state, dict):
            meta = state.get("meta")
            if isinstance(meta, dict):
                author = meta.get("author")
                if isinstance(author, str) and author.strip().lower() == "phaselab":
                    return True
        return False

    def _normalized_stem(self, value: str) -> str:
        stem = Path(value).stem.lower()
        stem = re.sub(r"\s+", " ", stem)
        return stem.strip()

    def _factory_name_match_level_from_stems(self, factory_stem: str, candidate_stem: str) -> int:
        fstem = self._normalized_stem(factory_stem)
        cstem = self._normalized_stem(candidate_stem)
        if not fstem or not cstem:
            return 0
        if cstem == fstem:
            return 3
        if cstem.startswith(fstem + " "):
            suffix = cstem[len(fstem) :].strip()
            if "converted" in suffix or "phase plant" in suffix or "user" in suffix:
                return 2
        ftoks = self._tokenize_text(fstem)
        ctoks = self._tokenize_text(cstem)
        if ftoks and ftoks.issubset(ctoks) and ("converted" in ctoks or ("phase" in ctoks and "plant" in ctoks)):
            return 1
        return 0

    def _factory_name_match_level(self, factory_path: Path, preset: object) -> int:
        source = getattr(preset, "source", None)
        if source is None:
            return 0
        return self._factory_name_match_level_from_stems(factory_path.name, str(source))

    def _is_exact_factory_proxy_match(self, factory_path: Path, preset: object) -> bool:
        source = getattr(preset, "source", None)
        if source is None:
            return False
        return self._factory_name_match_level(factory_path, preset) >= 2

    def _pick_exact_factory_proxy(self, factory_path: Path, pool: list) -> object | None:
        matched = [preset for preset in pool if self._is_exact_factory_proxy_match(factory_path, preset)]
        if not matched:
            return None
        nongenerated = [preset for preset in matched if not self._is_phaselab_generated_preset(preset)]
        if nongenerated:
            matched = nongenerated
        else:
            # If all exact matches are PhaseLab-generated, refuse proxy mode for factory safety.
            return None

        def rank(preset: object) -> tuple[int, int]:
            source_path = str(Path(getattr(preset, "source", "")).as_posix()).lower()
            match_level = self._factory_name_match_level(factory_path, preset)
            converted_rank = 0 if "converted factory presets" in source_path else 1
            return -match_level, converted_rank, len(source_path)

        matched.sort(key=rank)
        return matched[0]

    def _factory_proxy_candidates(self, factory_path: Path, pool: list) -> list[tuple[int, int, object]]:
        factory_tokens = self._tokenize_text(factory_path.stem)
        ranked: list[tuple[int, int, int, int, int, object]] = []
        for preset in pool:
            level = self._factory_name_match_level(factory_path, preset)
            preset_tokens = self._editable_preset_tokens(preset)
            overlap = len(factory_tokens & preset_tokens) if factory_tokens else 0
            if level <= 0 and overlap <= 0:
                continue
            source_path = str(Path(getattr(preset, "source", "")).as_posix()).lower()
            converted_rank = 0 if "converted factory presets" in source_path else 1
            generated_rank = 1 if self._is_phaselab_generated_preset(preset) else 0
            ranked.append((level, overlap, generated_rank, converted_rank, len(source_path), preset))

        if not ranked:
            # Fallback ranking: allow contextual pool usage even with weak/no token overlap.
            for preset in pool:
                source_path = str(Path(getattr(preset, "source", "")).as_posix()).lower()
                converted_rank = 0 if "converted factory presets" in source_path else 1
                generated_rank = 1 if self._is_phaselab_generated_preset(preset) else 0
                ranked.append((0, 0, generated_rank, converted_rank, len(source_path), preset))

        ranked.sort(key=lambda item: (-item[0], -item[1], item[2], item[3], item[4]))
        return [(level, overlap, preset) for level, overlap, _generated, _converted, _len, preset in ranked]

    def _build_factory_proxy_pool(
        self,
        factory_paths: list[Path],
        rng: random.Random,
        max_candidates: int = 300,
        max_pool: int = 12,
    ) -> tuple[list, str]:
        source_tokens: set[str] = set()
        source_name_tokens: set[str] = set()
        for path in factory_paths:
            source_tokens.update(self._extract_factory_source_tokens(path))
            source_name_tokens.update(self._tokenize_text(path.stem))

        editable_paths = self._discover_global_editable_paths(limit=1800)
        if not editable_paths:
            return [], "No editable presets available for proxy synthesis."
        if len(editable_paths) > max_candidates:
            matched_name_paths = []
            for candidate_path in editable_paths:
                cand_stem = candidate_path.name
                if any(self._factory_name_match_level_from_stems(factory_path.name, cand_stem) >= 2 for factory_path in factory_paths):
                    matched_name_paths.append(candidate_path)
            exact_keys = {str(path.resolve()) for path in matched_name_paths}
            remainder = [path for path in editable_paths if str(path.resolve()) not in exact_keys]
            keep = matched_name_paths[:max_candidates]
            slots_left = max(0, max_candidates - len(keep))
            if slots_left and remainder:
                keep.extend(rng.sample(remainder, min(slots_left, len(remainder))))
            editable_paths = keep
        presets, skipped = self._load_valid_editable_presets(editable_paths)
        if not presets:
            return [], "No valid editable presets available for proxy synthesis."

        presets_by_stem: dict[str, list] = {}
        for preset in presets:
            presets_by_stem.setdefault(Path(preset.source).stem.lower(), []).append(preset)

        def proxy_rank(preset: object) -> tuple[int, int]:
            source_path = str(Path(getattr(preset, "source", "")).as_posix()).lower()
            converted_rank = 0 if "converted factory presets" in source_path else 1
            generated_rank = 1 if self._is_phaselab_generated_preset(preset) else 0
            return converted_rank, generated_rank, len(source_path)

        direct_matches: list = []
        used_ids: set[int] = set()
        for factory_path in factory_paths:
            stem = factory_path.name
            candidates = [
                preset
                for key, key_presets in presets_by_stem.items()
                if self._factory_name_match_level_from_stems(stem, key) >= 2
                for preset in key_presets
                if id(preset) not in used_ids
            ]
            if not candidates:
                continue
            nongenerated = [preset for preset in candidates if not self._is_phaselab_generated_preset(preset)]
            if nongenerated:
                candidates = nongenerated
            candidates.sort(key=proxy_rank)
            picked = candidates[0]
            direct_matches.append(picked)
            used_ids.add(id(picked))

        scored: list[tuple[int, int, object]] = []
        for idx, preset in enumerate(presets):
            ptoks = self._editable_preset_tokens(preset)
            contextual_score = len(source_tokens & ptoks) if source_tokens else 0
            name_score = len(source_name_tokens & ptoks) if source_name_tokens else 0
            score = contextual_score + (3 * name_score)
            scored.append((score, idx, preset))

        scored.sort(key=lambda item: (item[0], -item[1]), reverse=True)
        if direct_matches:
            pool = direct_matches[:max_pool]
            for _score, _idx, preset in scored:
                if len(pool) >= max_pool:
                    break
                if id(preset) in used_ids:
                    continue
                pool.append(preset)
                used_ids.add(id(preset))
            note = (
                f"Proxy synthesis used {len(direct_matches)} factory-name matched editable preset(s) "
                f"and filled the rest from contextual matches ({len(pool)} total)."
            )
            unmatched = max(0, len(factory_paths) - len(direct_matches))
            if unmatched:
                note += f" Unmatched factory source names: {unmatched}."
        elif source_tokens and scored and scored[0][0] > 0:
            pool = [preset for _, _, preset in scored[:max_pool]]
            note = f"Proxy synthesis matched {len(pool)} editable preset(s) from factory source context."
        else:
            pool = rng.sample(presets, min(max_pool, len(presets)))
            note = "Proxy synthesis used random editable presets (no strong factory metadata match found)."
        if skipped:
            note += f" Skipped invalid editable presets: {len(skipped)}."
        return pool, note

    def _run_diffusion_pass(
        self,
        state: dict,
        rng: random.Random,
        amount: float,
        enum_pool: dict[str, list[str]],
        donor_states: list[dict],
        preserve_similarity: float,
    ) -> int:
        changes = 0
        changes += mutate_state(
            state,
            rng,
            amount=amount,
            enum_pool=enum_pool,
            preserve_pitch_low=True,
            preserve_similarity=preserve_similarity,
        )
        changes += mutate_active_generator_parameters(
            state,
            rng,
            amount=amount,
            enum_pool=enum_pool,
            preserve_pitch_low=True,
        )
        changes += mutate_generator_types(
            state,
            rng,
            amount=amount,
            donor_states=donor_states,
        )
        changes += mutate_global_modulation_amounts(
            state,
            rng,
            amount=amount,
            preserve_pitch_low=True,
        )
        changes += diffuse_lane_fx_and_lfo(
            state,
            rng,
            amount=amount,
            donor_states=donor_states,
            preserve_similarity=preserve_similarity,
        )
        return changes

    def _finalize_diffusion_output(
        self,
        state: dict,
        source_state: dict,
        rng: random.Random,
        amount: float,
        enum_pool: dict[str, list[str]],
        donor_states: list[dict],
        preserve_similarity: float,
        protected_switches: dict[str, str],
        generator_guard: dict[int, dict],
        allowed_master_pitch: tuple[str, ...],
    ) -> tuple[int, int]:
        total_changes = 0
        total_repairs = 0
        target_delta = max(12, int(round(42 * amount)))

        pre_delta = count_changed_leaf_values(source_state, state)
        attempts = 0
        while pre_delta < target_delta and attempts < 2:
            attempts += 1
            boost_amount = min(1.0, max(amount + (0.20 * attempts), 0.55))
            boost_similarity = max(0.20, preserve_similarity - (0.22 * attempts))
            total_changes += self._run_diffusion_pass(
                state,
                rng,
                amount=boost_amount,
                enum_pool=enum_pool,
                donor_states=donor_states,
                preserve_similarity=boost_similarity,
            )
            pre_delta = count_changed_leaf_values(source_state, state)

        total_repairs += restore_protected_switches(state, protected_switches)
        total_repairs += enforce_generator_ingredient_guard(state, generator_guard)
        total_repairs += sanitize_state_for_output(state)
        total_repairs += apply_output_audibility_safety(state)
        total_repairs += remove_new_master_pitch_modulations(state, allowed_master_pitch)

        post_delta = count_changed_leaf_values(source_state, state)
        if post_delta < target_delta:
            forced_amount = min(1.0, max(0.80, amount))
            total_changes += mutate_active_generator_parameters(
                state,
                rng,
                amount=forced_amount,
                enum_pool=enum_pool,
                preserve_pitch_low=True,
            )
            total_changes += mutate_global_modulation_amounts(
                state,
                rng,
                amount=forced_amount,
                preserve_pitch_low=True,
            )
            total_changes += diffuse_lane_fx_and_lfo(
                state,
                rng,
                amount=max(0.70, forced_amount),
                donor_states=donor_states,
                preserve_similarity=max(0.20, preserve_similarity - 0.25),
            )
            total_repairs += restore_protected_switches(state, protected_switches)
            total_repairs += enforce_generator_ingredient_guard(state, generator_guard)
            total_repairs += sanitize_state_for_output(state)
            total_repairs += apply_output_audibility_safety(state)
            total_repairs += remove_new_master_pitch_modulations(state, allowed_master_pitch)
            post_delta = count_changed_leaf_values(source_state, state)

        return total_changes, total_repairs

    def run_mutate(self) -> None:
        try:
            selected_paths = self.selected_preset_paths()
            if len(selected_paths) != 1:
                raise ValueError("Select exactly 1 preset for mutate")
            source_path = selected_paths[0]

            amount = float(self.mutate_amount.get())
            if amount < 0 or amount > 1:
                raise ValueError("Mutation amount must be between 0 and 1")
            seed = self.parse_seed(self.mutate_seed.get())
            count = self.parse_count(self.mutate_count.get(), "How many to generate")
            preset_dir = self.get_preset_dir()
            output_dir = self.get_output_dir()
            if not preset_dir.is_dir():
                raise ValueError("Preset folder not found. Select a valid preset folder.")

            rng = random.Random(seed)
            outputs: list[Path] = []
            use_full_folder = bool(self.mutate_use_full_folder.get())
            if self._is_zip_preset(source_path):
                preset = self.load_editable_preset(source_path)
                source_unresolved = find_unresolved_snapin_targets(preset.state)
                enum_states = [preset.state]
                extra_valid = 0
                if use_full_folder:
                    folder_zip_paths = [path for path in self.preset_paths if self._is_zip_preset(path)]
                    folder_presets, _skipped = self._load_valid_editable_presets(folder_zip_paths)
                    if folder_presets:
                        enum_states = [p.state for p in folder_presets]
                        extra_valid = max(0, len(folder_presets) - 1)
                enum_pool = build_enum_pool(enum_states)
                protected_switches = capture_protected_switches(preset.state)
                generator_guard = capture_generator_ingredient_guard(preset.state)
                allowed_master_pitch = capture_master_pitch_modulation_signatures(preset.state)
                preserve_similarity = max(0.35, 0.90 - 0.45 * amount)
                total_changes = 0
                total_repairs = 0
                for _ in range(count):
                    output_path = self.build_mutate_output_path(source_path.name, output_dir)
                    state = copy.deepcopy(preset.state)
                    changes = self._run_diffusion_pass(
                        state,
                        rng,
                        amount=amount,
                        enum_pool=enum_pool,
                        donor_states=enum_states,
                        preserve_similarity=preserve_similarity,
                    )
                    extra_changes, extra_repairs = self._finalize_diffusion_output(
                        state,
                        preset.state,
                        rng,
                        amount=amount,
                        enum_pool=enum_pool,
                        donor_states=enum_states,
                        preserve_similarity=preserve_similarity,
                        protected_switches=protected_switches,
                        generator_guard=generator_guard,
                        allowed_master_pitch=allowed_master_pitch,
                    )
                    changes += extra_changes
                    total_repairs += extra_repairs
                    self._stamp_output_state(state, "mutate", output_path.stem)
                    total_changes += changes
                    write_preset(output_path, state, preset.assets)
                    outputs.append(output_path)

                self.refresh_preset_list()
                summary = f"Created {len(outputs)} diffused preset(s), total changed values: {total_changes}. {self.summarize_outputs(outputs)}"
                if source_unresolved:
                    summary += f". Source had {len(source_unresolved)} unresolved snapin targets; sanitized on output"
                if use_full_folder:
                    summary += f". Full-folder mutation pool active ({extra_valid + 1} valid editable source(s))."
                if total_repairs:
                    summary += f". Integrity fixes applied: {total_repairs}"
            else:
                proxy_pool, proxy_note = self._build_factory_proxy_pool([source_path], rng, max_pool=10)
                base_preset = self._pick_exact_factory_proxy(source_path, proxy_pool)
                proxy_match_label = "exact"
                if base_preset is None and proxy_pool:
                    soft_ranked = self._factory_proxy_candidates(source_path, proxy_pool)
                    if soft_ranked:
                        base_preset = soft_ranked[0][2]
                        proxy_match_label = "soft/contextual"

                has_proxy_base = base_preset is not None
                if has_proxy_base and base_preset is not None:
                    ordered_proxy_pool = [base_preset] + [preset for preset in proxy_pool if id(preset) != id(base_preset)]
                    enum_pool = build_enum_pool([p.state for p in ordered_proxy_pool])
                    assets, warnings = merge_assets([base_preset])
                    protected_switches = capture_protected_switches(base_preset.state)
                    generator_guard = capture_generator_ingredient_guard(base_preset.state)
                    allowed_master_pitch = capture_master_pitch_modulation_signatures(base_preset.state)
                    total_changes = 0
                    total_repairs = 0
                    if proxy_match_label == "exact":
                        effective_amount = max(0.35, amount)
                        preserve_similarity = max(0.35, 0.90 - 0.45 * effective_amount)
                    else:
                        effective_amount = max(0.50, amount)
                        preserve_similarity = max(0.20, 0.60 - 0.30 * effective_amount)
                    proxy_donor_states = [p.state for p in ordered_proxy_pool]
                    for _ in range(count):
                        output_path = self.build_mutate_output_path(source_path.name, output_dir)
                        state = copy.deepcopy(base_preset.state)
                        changes = self._run_diffusion_pass(
                            state,
                            rng,
                            amount=effective_amount,
                            enum_pool=enum_pool,
                            donor_states=proxy_donor_states,
                            preserve_similarity=preserve_similarity,
                        )
                        extra_changes, extra_repairs = self._finalize_diffusion_output(
                            state,
                            base_preset.state,
                            rng,
                            amount=effective_amount,
                            enum_pool=enum_pool,
                            donor_states=proxy_donor_states,
                            preserve_similarity=preserve_similarity,
                            protected_switches=protected_switches,
                            generator_guard=generator_guard,
                            allowed_master_pitch=allowed_master_pitch,
                        )
                        changes += extra_changes
                        total_repairs += extra_repairs
                        self._stamp_output_state(state, "mutate", output_path.stem)
                        total_changes += changes
                        write_preset(output_path, state, assets)
                        outputs.append(output_path)
                    self.refresh_preset_list()
                    summary = (
                        f"Created {len(outputs)} factory-based diffused preset(s) via proxy synthesis, total changed values: {total_changes}. "
                        f"{self.summarize_outputs(outputs)}"
                    )
                    summary += f" Proxy base ({proxy_match_label}): {Path(base_preset.source).name}."
                    summary += f" {proxy_note}"
                    if use_full_folder:
                        summary += " Full-folder option is not required for factory proxy mutate."
                    if total_repairs:
                        summary += f" Integrity fixes applied: {total_repairs}."
                    if warnings:
                        summary += f" Asset warnings: {len(warnings)}."
                else:
                    total_changes = 0
                    unchanged_outputs = 0
                    source_blob = load_binary_preset_blob(source_path)
                    binary_pool = [path for path in self.preset_paths if not self._is_zip_preset(path)] if use_full_folder else [source_path]
                    alternatives = [path for path in binary_pool if path != source_path]
                    donor_paths = alternatives[:]
                    if not donor_paths:
                        sibling_paths = [
                            path
                            for path in source_path.parent.glob("*.phaseplant")
                            if path.is_file() and path != source_path and not self._is_zip_preset(path)
                        ]
                        donor_paths = sibling_paths
                    if not donor_paths and self.preset_paths:
                        donor_paths = [
                            path
                            for path in self.preset_paths
                            if path != source_path and path.is_file() and not self._is_zip_preset(path)
                        ]
                    rng.shuffle(donor_paths)
                    donor_paths = donor_paths[:16]
                    donor_blobs = [load_binary_preset_blob(path) for path in donor_paths]
                    for _ in range(count):
                        output_path = self.build_mutate_output_path(source_path.name, output_dir)
                        out_blob = source_blob
                        changes = 0
                        # Keep original factory topology by mutating in-place only.
                        for attempt in range(6):
                            try_amount = min(1.0, max(amount, 0.30) + (0.10 * attempt))
                            candidate_blob, candidate_changes = mutate_binary_preset_blob(
                                source_blob,
                                rng,
                                amount=try_amount,
                                donor_blobs=donor_blobs,
                            )
                            if candidate_changes > 0 and candidate_blob != source_blob:
                                out_blob = candidate_blob
                                changes = candidate_changes
                                break
                        if changes == 0 or out_blob == source_blob:
                            unchanged_outputs += 1
                        out_blob = self._stamp_output_blob(out_blob, "mutate")
                        write_binary_preset_blob(output_path, out_blob)
                        outputs.append(output_path)
                        total_changes += changes
                    self.refresh_preset_list()
                    summary = (
                        f"Created {len(outputs)} factory-binary diffused preset(s) in safe mode, total changed values: {total_changes}. "
                        f"{self.summarize_outputs(outputs)}"
                    )
                    summary += f" {proxy_note}"
                    if use_full_folder:
                        summary += f" Full-folder binary pool: {len(binary_pool)} source(s)."
                    if not has_proxy_base:
                        summary += (
                            " No suitable editable proxy base found for this factory preset; "
                            "used in-place binary-safe diffusion to preserve original groups/generators."
                        )
                    if unchanged_outputs:
                        summary += (
                            f" {unchanged_outputs} output(s) had no safe mutable binary fields and were kept structurally identical "
                            "except metadata stamp."
                        )
                    elif total_changes == 0:
                        summary += " No safe binary fields were changed."
            self.status_var.set(summary)
            self.set_output_log("Diffusion", outputs)
        except Exception as exc:  # noqa: BLE001
            self.status_var.set(f"Diffusion error: {exc}")
            self.output_log_var.set(f"Diffusion error: {exc}")

    def run_combine(self) -> None:
        try:
            mix_rate = float(self.combine_mix_rate.get())
            if mix_rate < 0 or mix_rate > 1:
                raise ValueError("Mix rate must be between 0 and 1")
            mutate_amount = float(self.combine_mutate.get())
            if mutate_amount < 0 or mutate_amount > 1:
                raise ValueError("Post-mutate amount must be between 0 and 1")
            seed = self.parse_seed(self.combine_seed.get())
            count = self.parse_count(self.combine_count.get(), "How many to generate")
            preset_dir = self.get_preset_dir()
            output_dir = self.get_output_dir()
            if not preset_dir.is_dir():
                raise ValueError("Preset folder not found. Select a valid preset folder.")

            rng = random.Random(seed)
            outputs: list[Path] = []
            use_full_folder = bool(self.combine_use_full_folder.get())
            source_paths = list(self.preset_paths) if use_full_folder else self.selected_preset_paths()
            if len(source_paths) < 2:
                raise ValueError("Select at least 2 presets for combine")
            if use_full_folder and len(source_paths) > 48:
                source_paths = rng.sample(source_paths, 48)

            zip_paths = [path for path in source_paths if self._is_zip_preset(path)]
            binary_paths = [path for path in source_paths if not self._is_zip_preset(path)]
            mixed_source_note = ""
            if zip_paths and binary_paths:
                if use_full_folder:
                    if len(zip_paths) >= 2:
                        binary_paths = []
                        mixed_source_note = " Mixed folder detected; using editable presets only."
                    elif len(binary_paths) >= 2:
                        zip_paths = []
                        mixed_source_note = " Mixed folder detected; using factory/binary presets only."
                    else:
                        raise ValueError("Need at least 2 compatible presets in selected folder for combine")
                else:
                    raise ValueError("Cannot combine editable(zip) and factory(binary) presets together yet. Use one format at a time.")

            if binary_paths:
                if len(binary_paths) < 2:
                    raise ValueError("Need at least 2 factory/binary presets for binary combine")
                proxy_pool, proxy_note = self._build_factory_proxy_pool(binary_paths, rng, max_pool=max(4, min(20, len(binary_paths) * 3)))
                source_proxy_pairs: list[tuple[Path, object, int, int]] = []
                used_proxy_ids: set[int] = set()
                for path in binary_paths:
                    candidates = self._factory_proxy_candidates(path, proxy_pool)
                    if not candidates:
                        continue
                    chosen: tuple[int, int, object] | None = None
                    for level, overlap, preset in candidates:
                        if id(preset) not in used_proxy_ids:
                            chosen = (level, overlap, preset)
                            break
                    if chosen is None:
                        # Allow controlled reuse when there are fewer unique candidates than sources.
                        chosen = candidates[0]
                    if chosen is None:
                        continue
                    level, overlap, preset = chosen
                    used_proxy_ids.add(id(preset))
                    source_proxy_pairs.append((path, preset, level, overlap))

                if len(source_proxy_pairs) >= 2:
                    selected_proxies = [preset for _path, preset, _level, _overlap in source_proxy_pairs]
                    enum_pool = build_enum_pool([p.state for p in selected_proxies])
                    assets, warnings = merge_assets(selected_proxies)
                    total_crossover = 0
                    total_mutation = 0
                    total_repairs = 0
                    combine_names = [path.name for path in binary_paths]
                    weak_match_count = sum(1 for _path, _preset, level, _overlap in source_proxy_pairs if level < 2)
                    effective_mix = max(0.65, mix_rate)
                    effective_mut = max(0.30, mutate_amount)
                    exact_match_count = sum(1 for _path, _preset, level, _overlap in source_proxy_pairs if level >= 2)
                    for _ in range(count):
                        base_preset = rng.choice(selected_proxies)
                        base_state = base_preset.state
                        donor_states = [p.state for p in selected_proxies if id(p) != id(base_preset)]
                        if not donor_states:
                            donor_states = [p.state for p in selected_proxies]
                        protected_switches = capture_protected_switches(base_state)
                        generator_guard = capture_generator_ingredient_guard(base_state)
                        output_path = self.build_combine_output_path(combine_names, output_dir)
                        base = copy.deepcopy(base_state)
                        crossover_changes = crossover_states(base, donor_states, rng, mix_rate=effective_mix, enum_pool=enum_pool)
                        mutation_changes = mutate_state(base, rng, amount=effective_mut, enum_pool=enum_pool)
                        mutation_changes += mutate_active_generator_parameters(
                            base,
                            rng,
                            amount=effective_mut,
                            enum_pool=enum_pool,
                            preserve_pitch_low=True,
                        )
                        mutation_changes += mutate_global_modulation_amounts(
                            base,
                            rng,
                            amount=effective_mut,
                            preserve_pitch_low=True,
                        )
                        mutation_changes += diffuse_lane_fx_and_lfo(
                            base,
                            rng,
                            amount=effective_mut,
                            donor_states=donor_states,
                            preserve_similarity=0.45,
                        )

                        # Ensure factory fusion does not collapse into near-parent copies.
                        changed_delta = count_changed_leaf_values(base_state, base)
                        if changed_delta < 24:
                            crossover_changes += crossover_states(
                                base,
                                donor_states,
                                rng,
                                mix_rate=min(1.0, effective_mix + 0.20),
                                enum_pool=enum_pool,
                            )
                            mutation_changes += mutate_state(
                                base,
                                rng,
                                amount=min(1.0, effective_mut + 0.18),
                                enum_pool=enum_pool,
                            )
                            mutation_changes += mutate_active_generator_parameters(
                                base,
                                rng,
                                amount=min(1.0, effective_mut + 0.18),
                                enum_pool=enum_pool,
                                preserve_pitch_low=True,
                            )
                        total_repairs += restore_protected_switches(base, protected_switches)
                        total_repairs += enforce_generator_ingredient_guard(base, generator_guard)
                        self._stamp_output_state(base, "combine", output_path.stem)
                        total_repairs += sanitize_state_for_output(base)
                        total_repairs += apply_output_audibility_safety(base)
                        total_crossover += crossover_changes
                        total_mutation += mutation_changes
                        write_preset(output_path, base, assets)
                        outputs.append(output_path)
                    self.refresh_preset_list()
                    summary = (
                        f"Created {len(outputs)} factory-based fused preset(s) via proxy synthesis from {len(binary_paths)} sources "
                        f"(total crossover: {total_crossover}, total mutate: {total_mutation}). "
                        f"{self.summarize_outputs(outputs)}"
                    )
                    summary += (
                        f" Proxy matches used: {len(source_proxy_pairs)} "
                        f"(exact/strong: {exact_match_count}, soft/contextual: {weak_match_count})."
                    )
                    summary += f" {proxy_note}"
                    if use_full_folder:
                        summary += f" Full-folder combine pool active ({len(binary_paths)} binary source(s))."
                        if mixed_source_note:
                            summary += mixed_source_note
                    if total_repairs:
                        summary += f" Integrity fixes applied: {total_repairs}."
                    if warnings:
                        summary += f" Asset warnings: {len(warnings)}."
                else:
                    base_blob = load_binary_preset_blob(binary_paths[0])
                    donor_pairs = [(path, load_binary_preset_blob(path)) for path in binary_paths[1:]]
                    donor_blobs = [blob for _, blob in donor_pairs]
                    fallback_parent_copies = 0
                    total_crossover = 0
                    total_mutation = 0
                    combine_names = [path.name for path in binary_paths]
                    for _ in range(count):
                        output_path = self.build_combine_output_path(combine_names, output_dir)
                        out_blob, cross_changes, mut_changes = combine_binary_preset_blobs(
                            base_blob,
                            donor_blobs,
                            rng,
                            mix_rate=mix_rate,
                            mutate_amount=mutate_amount,
                        )
                        if cross_changes + mut_changes == 0 or out_blob == base_blob:
                            parent_blobs = [base_blob] + donor_blobs
                            if len(parent_blobs) > 1:
                                out_blob = rng.choice(parent_blobs[1:] if rng.random() < 0.6 else parent_blobs)
                            fallback_parent_copies += 1
                        out_blob = self._stamp_output_blob(out_blob, "combine")
                        write_binary_preset_blob(output_path, out_blob)
                        outputs.append(output_path)
                        total_crossover += cross_changes
                        total_mutation += mut_changes

                    self.refresh_preset_list()
                    summary = (
                        f"Created {len(outputs)} factory-binary fused preset(s) in safe mode from {len(binary_paths)} sources "
                        f"(total crossover: {total_crossover}, total mutate: {total_mutation}). "
                        f"{self.summarize_outputs(outputs)}"
                    )
                    summary += f" {proxy_note}"
                    if fallback_parent_copies:
                        summary += (
                            f" Applied fallback parent selection on {fallback_parent_copies} file(s) "
                            "where safe binary crossover found no editable fields."
                        )
                    if use_full_folder:
                        summary += f" Full-folder combine pool active ({len(binary_paths)} binary source(s))."
                        if mixed_source_note:
                            summary += mixed_source_note
                    elif total_crossover + total_mutation == 0:
                        summary += " No safe binary fields were changed; save/copy presets to User Presets for full combine."
            else:
                loaded = [self.load_editable_preset(path) for path in zip_paths]
                presets: list = []
                skipped_invalid: list[str] = []
                for preset in loaded:
                    unresolved = find_unresolved_snapin_targets(preset.state)
                    if unresolved:
                        skipped_invalid.append(f"{preset.source.name} ({len(unresolved)} unresolved targets)")
                        continue
                    presets.append(preset)
                if len(presets) < 2:
                    raise ValueError("Need at least 2 valid selected presets after skipping unresolved snapin references")

                combine_names = [preset.source.name for preset in presets]
                base_state = presets[0].state
                donor_states = [p.state for p in presets[1:]]
                enum_pool = build_enum_pool([p.state for p in presets])
                assets, warnings = merge_assets(presets)
                protected_switches = capture_protected_switches(base_state)
                generator_guard = capture_generator_ingredient_guard(base_state)
                total_crossover = 0
                total_mutation = 0
                total_repairs = 0
                for _ in range(count):
                    output_path = self.build_combine_output_path(combine_names, output_dir)
                    base = copy.deepcopy(base_state)
                    crossover_changes = crossover_states(base, donor_states, rng, mix_rate=mix_rate, enum_pool=enum_pool)
                    mutation_changes = 0
                    if mutate_amount > 0:
                        mutation_changes = mutate_state(base, rng, amount=mutate_amount, enum_pool=enum_pool)
                    total_repairs += restore_protected_switches(base, protected_switches)
                    total_repairs += enforce_generator_ingredient_guard(base, generator_guard)
                    self._stamp_output_state(base, "combine", output_path.stem)
                    total_repairs += sanitize_state_for_output(base)
                    total_repairs += apply_output_audibility_safety(base)
                    total_crossover += crossover_changes
                    total_mutation += mutation_changes
                    write_preset(output_path, base, assets)
                    outputs.append(output_path)

                self.refresh_preset_list()
                summary = (
                    f"Created {len(outputs)} fused preset(s) from {len(presets)} valid sources "
                    f"(total crossover: {total_crossover}, total mutate: {total_mutation}). "
                    f"{self.summarize_outputs(outputs)}"
                )
                if skipped_invalid:
                    summary += f". Skipped invalid sources: {len(skipped_invalid)}"
                if use_full_folder:
                    summary += f". Full-folder combine pool active ({len(presets)} valid editable source(s))."
                    if mixed_source_note:
                        summary += mixed_source_note
                if total_repairs:
                    summary += f". Integrity fixes applied: {total_repairs}"
                if warnings:
                    summary += f". Asset warnings: {len(warnings)}"
            self.status_var.set(summary)
            self.set_output_log("Fusion", outputs)
        except Exception as exc:  # noqa: BLE001
            self.status_var.set(f"Fusion error: {exc}")
            self.output_log_var.set(f"Fusion error: {exc}")

    def run_random(self) -> None:
        try:
            complexity = float(self.random_complexity.get())
            if complexity < 0 or complexity > 1:
                raise ValueError("Complexity must be between 0 and 1")
            seed = self.parse_seed(self.random_seed.get())
            count = self.parse_count(self.random_count.get(), "How many to generate")
            output_dir = self.get_output_dir()
            rng = random.Random(seed)
            use_guidance = bool(self.random_use_guidance.get())
            internal_presets, internal_skipped = self._load_internal_template_bank()
            selected_paths = list(self.preset_paths)
            selected_editable_paths = [path for path in selected_paths if self._is_zip_preset(path)]
            selected_binary_paths = [path for path in selected_paths if not self._is_zip_preset(path)]

            guidance_presets: list = list(internal_presets)
            notes: list[str] = []
            skipped_invalid = internal_skipped

            if internal_presets:
                notes.append(f"Internal template bank active: {len(internal_presets)} preset(s).")

            if use_guidance and selected_editable_paths:
                folder_presets, folder_skipped = self._load_valid_editable_presets(selected_editable_paths)
                skipped_invalid += len(folder_skipped)
                guidance_presets = self._merge_unique_presets(guidance_presets, folder_presets, max_items=64)
                if folder_presets:
                    notes.append(f"Selected folder guidance: {len(folder_presets)} editable source(s).")

            if use_guidance and selected_binary_paths:
                proxy_pool, proxy_note = self._build_factory_proxy_pool(
                    selected_binary_paths,
                    rng,
                    max_pool=max(8, min(24, len(selected_binary_paths) * 2)),
                )
                if proxy_pool:
                    guidance_presets = self._merge_unique_presets(guidance_presets, proxy_pool, max_items=64)
                if proxy_note:
                    notes.append(proxy_note)

            global_paths = self._discover_global_editable_paths(limit=1800)
            global_presets, global_skipped = self._load_valid_editable_presets(global_paths) if global_paths else ([], [])
            skipped_invalid += len(global_skipped)

            if use_guidance:
                if len(guidance_presets) < 12 and global_presets:
                    guidance_presets = self._merge_unique_presets(guidance_presets, global_presets, max_items=64)
                    notes.append("Supplemented guidance with global editable preset library.")
            else:
                guidance_presets = list(internal_presets)
                if guidance_presets:
                    notes.append("Structure guidance disabled; using only internal template bank.")
                elif global_presets:
                    guidance_presets = self._merge_unique_presets([], global_presets, max_items=64)
                    notes.append("Structure guidance disabled; internal templates missing, used global editable fallback.")

            if guidance_presets:
                outputs, total_generated_changes, total_repairs, warning_count = self._generate_random_from_editable_pool(
                    guidance_presets,
                    rng,
                    count,
                    complexity,
                    output_dir,
                )
                self.refresh_preset_list()
                summary = (
                    f"Created {len(outputs)} catalyzed preset(s) "
                    f"(total generated parameter changes: {total_generated_changes}, complexity: {complexity:.2f}). "
                    f"{self.summarize_outputs(outputs)}"
                )
                summary += f". Reference pool: {len(guidance_presets)} editable preset(s)."
                if selected_binary_paths and use_guidance:
                    summary += f" Factory context sources considered: {len(selected_binary_paths)}."
                if skipped_invalid:
                    summary += f" Skipped invalid source presets: {skipped_invalid}."
                if total_repairs:
                    summary += f" Integrity fixes applied: {total_repairs}."
                if warning_count:
                    summary += f" Asset warnings: {warning_count}."
                if notes:
                    summary += f" {' '.join(notes[:3])}"
            elif selected_binary_paths:
                outputs, total_crossover, total_mutation, fallback_parent_selections = self._generate_random_from_binary_pool(
                    selected_binary_paths,
                    rng,
                    count,
                    complexity,
                    output_dir,
                )
                self.refresh_preset_list()
                summary = (
                    f"Created {len(outputs)} factory-binary catalyzed preset(s) in safe mode "
                    f"(total crossover: {total_crossover}, total mutate: {total_mutation}, complexity: {complexity:.2f}). "
                    f"{self.summarize_outputs(outputs)}"
                )
                summary += " Used binary-safe generation because no editable guidance presets were available."
                if fallback_parent_selections:
                    summary += (
                        f" Applied fallback parent selection on {fallback_parent_selections} file(s) "
                        "where safe binary edits found no editable fields."
                    )
                elif total_crossover + total_mutation == 0:
                    summary += " No safe binary fields were changed."
            else:
                raise ValueError(
                    "Catalyze could not find usable source references. "
                    "Add presets in selected folder or bundle internal templates."
                )
            self.status_var.set(summary)
            self.set_output_log("Catalyze", outputs)
        except Exception as exc:  # noqa: BLE001
            self.status_var.set(f"Catalyze error: {exc}")
            self.output_log_var.set(f"Catalyze error: {exc}")

    def run_anomalize(self) -> None:
        saved_selection = list(self.preset_list.curselection())
        saved_values = {
            "mutate_amount": float(self.mutate_amount.get()),
            "mutate_seed": self.mutate_seed.get(),
            "mutate_count": self.mutate_count.get(),
            "mutate_use_full_folder": bool(self.mutate_use_full_folder.get()),
            "combine_mix_rate": float(self.combine_mix_rate.get()),
            "combine_mutate": float(self.combine_mutate.get()),
            "combine_seed": self.combine_seed.get(),
            "combine_count": self.combine_count.get(),
            "combine_use_full_folder": bool(self.combine_use_full_folder.get()),
            "random_complexity": float(self.random_complexity.get()),
            "random_seed": self.random_seed.get(),
            "random_count": self.random_count.get(),
            "random_use_guidance": bool(self.random_use_guidance.get()),
            "mode_desc_override": self._mode_description_override,
        }

        try:
            count = self.parse_count(self.anomalize_count.get(), "How many to generate")
            preset_dir = self.get_preset_dir()
            output_dir = self.get_output_dir()
            if not preset_dir.is_dir():
                raise ValueError("Preset folder not found. Select a valid preset folder.")
            if not self.preset_paths:
                raise ValueError("No presets found in selected preset folder.")

            rng = random.Random()
            outputs: list[Path] = []
            mode_counts = {"diffusion": 0, "fusion": 0, "catalyze": 0}
            failed_attempts = 0

            for _ in range(count):
                created = False
                for _attempt in range(5):
                    available_modes = ["diffusion", "catalyze"]
                    if len(self.preset_paths) >= 2:
                        available_modes.append("fusion")
                    mode = rng.choice(available_modes)

                    before = {str(path.resolve()) for path in output_dir.glob("*.phaseplant")}
                    self._mode_description_override = f"Generated by PhaseLab (anomalize surprise mode: {mode})."

                    if mode == "diffusion":
                        self.mutate_count.set("1")
                        self.mutate_seed.set("")
                        self.mutate_amount.set(rng.uniform(0.12, 0.95))
                        self.mutate_use_full_folder.set(rng.random() < 0.65 and len(self.preset_paths) > 1)
                        self._set_listbox_selection([rng.randrange(len(self.preset_paths))])
                        self.run_mutate()
                    elif mode == "fusion":
                        self.combine_count.set("1")
                        self.combine_seed.set("")
                        self.combine_mix_rate.set(rng.uniform(0.20, 0.92))
                        self.combine_mutate.set(rng.uniform(0.08, 0.78))
                        use_full_folder = rng.random() < 0.55 and len(self.preset_paths) >= 3
                        self.combine_use_full_folder.set(use_full_folder)
                        if use_full_folder:
                            self._set_listbox_selection([])
                        else:
                            take = 2 if len(self.preset_paths) == 2 else rng.randint(2, min(4, len(self.preset_paths)))
                            self._set_listbox_selection(rng.sample(list(range(len(self.preset_paths))), take))
                        self.run_combine()
                    else:
                        self.random_count.set("1")
                        self.random_seed.set("")
                        self.random_complexity.set(rng.uniform(0.08, 0.98))
                        self.random_use_guidance.set(rng.random() < 0.85)
                        self._set_listbox_selection([])
                        self.run_random()

                    self._mode_description_override = None
                    after_paths = {str(path.resolve()): path for path in output_dir.glob("*.phaseplant")}
                    created_now = sorted(
                        (after_paths[key] for key in set(after_paths) - before),
                        key=lambda path: path.stat().st_mtime,
                    )
                    if created_now:
                        outputs.extend(created_now)
                        mode_counts[mode] += len(created_now)
                        created = True
                        break

                if not created:
                    failed_attempts += 1

            if not outputs:
                raise ValueError("Anomalize could not generate presets from the selected folder.")

            self.refresh_preset_list()
            summary = (
                f"Created {len(outputs)} anomalized preset(s) "
                f"(Diffusion: {mode_counts['diffusion']}, Fusion: {mode_counts['fusion']}, Catalyze: {mode_counts['catalyze']}). "
                f"{self.summarize_outputs(outputs)}"
            )
            if failed_attempts:
                summary += f" {failed_attempts} generation attempt(s) produced no file."
            self.status_var.set(summary)
            self.set_output_log("Anomalize", outputs)
        except Exception as exc:  # noqa: BLE001
            self.status_var.set(f"Anomalize error: {exc}")
            self.output_log_var.set(f"Anomalize error: {exc}")
        finally:
            self.mutate_amount.set(saved_values["mutate_amount"])
            self.mutate_seed.set(saved_values["mutate_seed"])
            self.mutate_count.set(saved_values["mutate_count"])
            self.mutate_use_full_folder.set(saved_values["mutate_use_full_folder"])
            self.combine_mix_rate.set(saved_values["combine_mix_rate"])
            self.combine_mutate.set(saved_values["combine_mutate"])
            self.combine_seed.set(saved_values["combine_seed"])
            self.combine_count.set(saved_values["combine_count"])
            self.combine_use_full_folder.set(saved_values["combine_use_full_folder"])
            self.random_complexity.set(saved_values["random_complexity"])
            self.random_seed.set(saved_values["random_seed"])
            self.random_count.set(saved_values["random_count"])
            self.random_use_guidance.set(saved_values["random_use_guidance"])
            self._mode_description_override = saved_values["mode_desc_override"]
            self._set_listbox_selection(saved_selection)


def main() -> None:
    app = PhasePlantLabGUI()
    app.mainloop()


if __name__ == "__main__":
    main()
