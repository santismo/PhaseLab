#!/usr/bin/env python3
"""GUI for factory-to-user proxy conversion."""

from __future__ import annotations

import subprocess
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from factory_to_user_proxy_converter import convert


DEFAULT_FACTORY_ROOT = Path("/Library/Application Support/Kilohearts/presets/kphp/Factory Presets")
DEFAULT_OUTPUT_ROOT = Path.home() / "Library/Audio/Presets/Kilohearts/Phase Plant/User Presets/converted presets from converter"
DEFAULT_USER_PRESETS_ROOT = Path.home() / "Library/Audio/Presets/Kilohearts/Phase Plant/User Presets"


class FactoryToUserProxyConverterGUI(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("Factory To User Preset Converter")
        self.geometry("1040x760")
        self.minsize(920, 680)

        self.script_dir = Path(__file__).resolve().parent
        self.workspace_root = self._detect_workspace_root()

        self.factory_root_var = tk.StringVar(value="")
        self.output_root_var = tk.StringVar(value="")
        self.ref1_var = tk.StringVar(value="")
        self.ref2_var = tk.StringVar(value="")
        self.ref3_var = tk.StringVar(value="")
        self.ref4_var = tk.StringVar(value="")
        self.ref5_var = tk.StringVar(value="")
        self.preserve_tree_var = tk.BooleanVar(value=True)
        self.overwrite_var = tk.BooleanVar(value=False)
        self.metadata_match_var = tk.BooleanVar(value=True)
        self.status_var = tk.StringVar(value="Idle")
        self.summary_var = tk.StringVar(value="")

        self.worker_thread: threading.Thread | None = None
        self.last_output_root: Path | None = None

        self._apply_recommended_paths()
        self._build_ui()
        self._log(
            "Ready. This accepts proprietary factory-container presets as input and builds valid user-format "
            "proxies by matching against user-format references."
        )
        self._log(
            "Note: without the official translator, full 100% binary decode is not guaranteed. "
            "Use more user-format references for higher coverage."
        )

    def _detect_workspace_root(self) -> Path:
        candidates = [Path.cwd(), self.script_dir, self.script_dir.parent, self.script_dir.parent.parent]
        for base in candidates:
            if (base / "factory copy").exists():
                return base
        return Path.cwd()

    def _default_path(self, name: str) -> Path:
        candidate = self.workspace_root / name
        return candidate

    def _apply_recommended_paths(self) -> None:
        # User-requested fixed defaults first.
        factory = DEFAULT_FACTORY_ROOT if DEFAULT_FACTORY_ROOT.exists() else self._default_path("factory copy")
        output = DEFAULT_OUTPUT_ROOT
        self.factory_root_var.set(str(factory))
        self.output_root_var.set(str(output))

        # Best-effort reference pool defaults (highest practical coverage).
        refs = [
            DEFAULT_USER_PRESETS_ROOT / "converted factory banks and presets",
            DEFAULT_USER_PRESETS_ROOT / "converted factory presets",
            DEFAULT_USER_PRESETS_ROOT / "converted factory proxies",
            DEFAULT_USER_PRESETS_ROOT / "converted presets from converter",
            DEFAULT_USER_PRESETS_ROOT,
        ]
        vars_ = [self.ref1_var, self.ref2_var, self.ref3_var, self.ref4_var, self.ref5_var]
        for var, ref in zip(vars_, refs):
            if ref.exists():
                var.set(str(ref))
            else:
                var.set("")

    def _build_ui(self) -> None:
        root = ttk.Frame(self, padding=12)
        root.pack(fill="both", expand=True)
        root.columnconfigure(1, weight=1)
        root.rowconfigure(14, weight=1)

        ttk.Label(root, text="Factory To User Preset Converter", font=("TkDefaultFont", 13, "bold")).grid(
            row=0, column=0, columnspan=3, sticky="w"
        )
        ttk.Label(
            root,
            text=(
                "Converts factory presets into user-format proxies for PhaseLab workflows.\n"
                "Factory files can be proprietary-container format. Conversion is reference-based (not binary decode).\n"
                "Best coverage requires as many already-converted user-format presets as possible."
            ),
            justify="left",
        ).grid(row=1, column=0, columnspan=3, sticky="w", pady=(4, 10))

        self._path_row(root, 2, "Factory preset folder", self.factory_root_var)
        self._path_row(root, 3, "Output folder", self.output_root_var)
        self._path_row(root, 4, "Reference folder 1", self.ref1_var)
        self._path_row(root, 5, "Reference folder 2", self.ref2_var)
        self._path_row(root, 6, "Reference folder 3", self.ref3_var)
        self._path_row(root, 7, "Reference folder 4", self.ref4_var)
        self._path_row(root, 8, "Reference folder 5", self.ref5_var)

        opts = ttk.Frame(root)
        opts.grid(row=9, column=0, columnspan=3, sticky="w", pady=(10, 0))
        ttk.Checkbutton(opts, text="Preserve category folder tree", variable=self.preserve_tree_var).grid(
            row=0, column=0, sticky="w", padx=(0, 16)
        )
        ttk.Checkbutton(opts, text="Overwrite existing output files", variable=self.overwrite_var).grid(
            row=0, column=1, sticky="w"
        )
        ttk.Checkbutton(opts, text="Enable metadata matching (author + description)", variable=self.metadata_match_var).grid(
            row=0, column=2, sticky="w", padx=(16, 0)
        )

        btns = ttk.Frame(root)
        btns.grid(row=10, column=0, columnspan=3, sticky="w", pady=(10, 0))
        self.run_btn = ttk.Button(btns, text="Run Conversion", command=self._run_conversion)
        self.run_btn.grid(row=0, column=0, padx=(0, 8))
        ttk.Button(btns, text="Open Output Folder", command=self._open_output_folder).grid(row=0, column=1, padx=(0, 8))
        ttk.Button(btns, text="Open Missing Report", command=self._open_missing_report).grid(row=0, column=2)
        ttk.Button(btns, text="Use Recommended Paths", command=self._apply_recommended_paths).grid(row=0, column=3, padx=(8, 0))

        ttk.Label(root, text="Status").grid(row=11, column=0, sticky="w", pady=(10, 0))
        ttk.Label(root, textvariable=self.status_var).grid(row=11, column=1, columnspan=2, sticky="w", pady=(10, 0))
        ttk.Label(root, textvariable=self.summary_var, font=("TkDefaultFont", 10, "bold")).grid(
            row=12, column=0, columnspan=3, sticky="w", pady=(6, 0)
        )

        ttk.Label(root, text="Log").grid(row=13, column=0, sticky="w", pady=(10, 0))
        self.log_text = tk.Text(root, height=16, wrap="word")
        self.log_text.grid(row=14, column=0, columnspan=3, sticky="nsew")

    def _path_row(self, parent: ttk.Frame, row: int, label: str, var: tk.StringVar) -> None:
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", pady=3)
        ttk.Entry(parent, textvariable=var).grid(row=row, column=1, sticky="ew", padx=(8, 8), pady=3)
        ttk.Button(parent, text="Browse", command=lambda v=var: self._pick_folder(v)).grid(row=row, column=2, pady=3)

    def _pick_folder(self, var: tk.StringVar) -> None:
        initial = var.get().strip() or str(self.workspace_root)
        folder = filedialog.askdirectory(title="Select folder", initialdir=initial)
        if folder:
            var.set(folder)

    def _log(self, msg: str) -> None:
        ts = time.strftime("%H:%M:%S")
        self.log_text.insert("end", f"[{ts}] {msg}\n")
        self.log_text.see("end")

    def _set_running(self, running: bool) -> None:
        self.run_btn.configure(state="disabled" if running else "normal")
        self.status_var.set("Running conversion..." if running else "Idle")

    def _collect_paths(self) -> tuple[Path, Path, list[Path]]:
        factory_root = Path(self.factory_root_var.get().strip()).expanduser()
        output_root = Path(self.output_root_var.get().strip()).expanduser()
        refs: list[Path] = []
        seen: set[str] = set()
        for raw in (self.ref1_var.get(), self.ref2_var.get(), self.ref3_var.get(), self.ref4_var.get(), self.ref5_var.get()):
            value = raw.strip()
            if value:
                resolved = str(Path(value).expanduser())
                if resolved in seen:
                    continue
                seen.add(resolved)
                refs.append(Path(resolved))
        return factory_root, output_root, refs

    def _factory_container_counts(self, factory_root: Path) -> tuple[int, int]:
        proprietary = 0
        user_zip = 0
        for path in factory_root.rglob("*.phaseplant"):
            if not path.is_file():
                continue
            try:
                with path.open("rb") as handle:
                    head = handle.read(4)
            except OSError:
                continue
            if head == b"PK\x03\x04":
                user_zip += 1
            else:
                proprietary += 1
        return proprietary, user_zip

    def _run_conversion(self) -> None:
        if self.worker_thread and self.worker_thread.is_alive():
            messagebox.showinfo("In progress", "Conversion is already running.")
            return

        factory_root, output_root, refs = self._collect_paths()
        if not factory_root.exists():
            messagebox.showerror("Invalid folder", f"Factory folder not found:\n{factory_root}")
            return
        if not refs:
            messagebox.showerror("Invalid setup", "Add at least one reference folder.")
            return
        missing_refs = [str(path) for path in refs if not path.exists()]
        if missing_refs:
            messagebox.showerror("Invalid references", "These reference folders do not exist:\n" + "\n".join(missing_refs))
            return

        self._set_running(True)
        self.summary_var.set("")
        self._log(f"Factory root: {factory_root}")
        self._log(f"Output root: {output_root}")
        self._log(f"Reference roots: {', '.join(str(x) for x in refs)}")
        proprietary_count, user_zip_count = self._factory_container_counts(factory_root)
        self._log(
            "Factory container scan: "
            f"{proprietary_count} proprietary-container files, {user_zip_count} user-format zip files."
        )

        def worker() -> None:
            try:
                summary = convert(
                    factory_root=factory_root,
                    output_root=output_root,
                    reference_roots=refs,
                    preserve_tree=self.preserve_tree_var.get(),
                    overwrite=self.overwrite_var.get(),
                    allow_metadata_match=self.metadata_match_var.get(),
                )
                self.after(0, lambda: self._on_conversion_success(summary))
            except Exception as exc:  # noqa: BLE001
                self.after(0, lambda: self._on_conversion_error(exc))

        self.worker_thread = threading.Thread(target=worker, daemon=True)
        self.worker_thread.start()

    def _on_conversion_success(self, summary: dict) -> None:
        self._set_running(False)
        self.last_output_root = Path(summary["output_root"])
        coverage = summary.get("coverage_percent", 0.0)
        matched = summary.get("matched", 0)
        total = summary.get("total_factory_files", 0)
        missing = summary.get("missing", 0)
        self.summary_var.set(f"Coverage: {coverage}% ({matched}/{total})  Missing: {missing}")
        self._log("Conversion complete.")
        self._log(f"Matched: {matched}, Missing: {missing}, Coverage: {coverage}%")
        self._log(f"Summary: {summary.get('output_root')}/conversion_summary.json")
        self._log(f"Missing report: {summary.get('missing_txt')}")

    def _on_conversion_error(self, exc: Exception) -> None:
        self._set_running(False)
        messagebox.showerror("Conversion failed", str(exc))
        self._log(f"Conversion failed: {exc}")

    def _open_output_folder(self) -> None:
        output = Path(self.output_root_var.get().strip()).expanduser()
        if not output.exists():
            messagebox.showinfo("Not found", f"Output folder not found:\n{output}")
            return
        subprocess.run(["open", str(output)], check=False)

    def _open_missing_report(self) -> None:
        output = Path(self.output_root_var.get().strip()).expanduser()
        missing = output / "missing_factory_presets.txt"
        if not missing.exists():
            messagebox.showinfo("Not found", f"Missing report not found:\n{missing}")
            return
        subprocess.run(["open", str(missing)], check=False)


def main() -> None:
    app = FactoryToUserProxyConverterGUI()
    app.mainloop()


if __name__ == "__main__":
    main()
