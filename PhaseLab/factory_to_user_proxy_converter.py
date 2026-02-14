#!/usr/bin/env python3
"""
Build user-format preset proxies from existing converted references.

This is not a binary factory decoder. It copies known-good user-format
`.phaseplant` files (zip-based) that match factory preset names.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any


def is_user_format(path: Path) -> bool:
    try:
        with path.open("rb") as handle:
            return handle.read(4) == b"PK\x03\x04"
    except OSError:
        return False


def normalize_name(name: str) -> str:
    base = name.lower()
    return re.sub(r"[^a-z0-9]+", "", base)


def normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip().lower())


def parse_factory_meta(path: Path) -> tuple[str, str]:
    """
    Best-effort extraction from proprietary factory container.
    Known files include a small JSON object near start with author/description.
    """
    try:
        blob = path.read_bytes()[:8192]
    except OSError:
        return "", ""

    start = blob.find(b"{")
    if start < 0:
        return "", ""
    end = blob.find(b"}", start)
    if end < 0:
        return "", ""

    chunk = blob[start : end + 1]
    try:
        text = chunk.decode("utf-8", errors="replace")
        parsed = json.loads(text)
    except Exception:  # noqa: BLE001
        return "", ""

    if not isinstance(parsed, dict):
        return "", ""

    author = str(parsed.get("author", "")).strip()
    description = str(parsed.get("description", "")).strip()
    return author, description


def parse_user_meta(path: Path) -> tuple[str, str]:
    """
    Best-effort extraction from user-format zip preset.
    """
    import zipfile

    try:
        with zipfile.ZipFile(path, "r") as zip_handle:
            payload = zip_handle.read("state.json")
        obj: dict[str, Any] = json.loads(payload)
        meta = obj.get("meta", {})
        if not isinstance(meta, dict):
            return "", ""
        author = str(meta.get("author", "")).strip()
        description = str(meta.get("description", "")).strip()
        return author, description
    except Exception:  # noqa: BLE001
        return "", ""


@dataclass
class MatchRow:
    factory_path: str
    output_path: str
    reference_path: str
    match_type: str


def collect_user_references(
    reference_roots: list[Path],
) -> tuple[dict[str, Path], dict[str, list[Path]], dict[tuple[str, str], list[Path]]]:
    exact: dict[str, Path] = {}
    normalized: dict[str, list[Path]] = {}
    by_meta: dict[tuple[str, str], list[Path]] = {}

    for root in reference_roots:
        if not root.exists():
            continue
        for path in root.rglob("*.phaseplant"):
            if not path.is_file():
                continue
            if not is_user_format(path):
                continue
            key = path.name.casefold()
            exact.setdefault(key, path)
            norm = normalize_name(path.stem)
            normalized.setdefault(norm, []).append(path)
            author, description = parse_user_meta(path)
            if author or description:
                meta_key = (normalize_text(author), normalize_text(description))
                by_meta.setdefault(meta_key, []).append(path)
    return exact, normalized, by_meta


def convert(
    factory_root: Path,
    output_root: Path,
    reference_roots: list[Path],
    preserve_tree: bool,
    overwrite: bool,
    allow_metadata_match: bool = True,
) -> dict:
    if not factory_root.exists():
        raise FileNotFoundError(f"Factory root not found: {factory_root}")

    exact_refs, normalized_refs, meta_refs = collect_user_references(reference_roots)
    factory_files = sorted([p for p in factory_root.rglob("*.phaseplant") if p.is_file()])

    output_root.mkdir(parents=True, exist_ok=True)
    rows: list[MatchRow] = []
    missing: list[Path] = []

    for factory_path in factory_files:
        key = factory_path.name.casefold()
        ref_path: Path | None = None
        match_type = ""

        if key in exact_refs:
            ref_path = exact_refs[key]
            match_type = "exact-name"
        else:
            norm = normalize_name(factory_path.stem)
            candidates = normalized_refs.get(norm, [])
            if len(candidates) == 1:
                ref_path = candidates[0]
                match_type = "normalized-name"
            elif allow_metadata_match:
                author, description = parse_factory_meta(factory_path)
                meta_key = (normalize_text(author), normalize_text(description))
                meta_candidates = meta_refs.get(meta_key, [])
                if len(meta_candidates) == 1:
                    ref_path = meta_candidates[0]
                    match_type = "factory-meta"

        if ref_path is None:
            missing.append(factory_path)
            continue

        rel = factory_path.relative_to(factory_root)
        out_path = output_root / rel if preserve_tree else (output_root / factory_path.name)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        if out_path.exists() and not overwrite:
            # Keep existing file and still treat as matched.
            rows.append(
                MatchRow(
                    factory_path=str(factory_path),
                    output_path=str(out_path),
                    reference_path=str(ref_path),
                    match_type=f"{match_type}-existing",
                )
            )
            continue

        shutil.copy2(ref_path, out_path)
        rows.append(
            MatchRow(
                factory_path=str(factory_path),
                output_path=str(out_path),
                reference_path=str(ref_path),
                match_type=match_type,
            )
        )

    matched_csv = output_root / "matched_factory_to_user.csv"
    with matched_csv.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["factory_path", "output_path", "reference_path", "match_type"])
        for row in rows:
            writer.writerow([row.factory_path, row.output_path, row.reference_path, row.match_type])

    missing_txt = output_root / "missing_factory_presets.txt"
    with missing_txt.open("w", encoding="utf-8") as handle:
        for path in missing:
            handle.write(f"{path}\n")

    summary = {
        "factory_root": str(factory_root),
        "output_root": str(output_root),
        "reference_roots": [str(p) for p in reference_roots],
        "total_factory_files": len(factory_files),
        "matched": len(rows),
        "missing": len(missing),
        "coverage_percent": round((len(rows) / len(factory_files) * 100.0), 2) if factory_files else 0.0,
        "metadata_match_enabled": bool(allow_metadata_match),
        "matched_csv": str(matched_csv),
        "missing_txt": str(missing_txt),
    }
    (output_root / "conversion_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return summary


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Create user-format proxy presets by matching factory filenames to existing user-format references."
        )
    )
    parser.add_argument(
        "--factory-root",
        type=Path,
        default=Path("factory copy"),
        help="Folder containing factory presets (default: ./factory copy)",
    )
    parser.add_argument(
        "--output-root",
        type=Path,
        default=Path("converted factory proxies"),
        help="Destination for generated user-format proxies (default: ./converted factory proxies)",
    )
    parser.add_argument(
        "--reference-root",
        type=Path,
        action="append",
        default=[],
        help=(
            "Folder with existing user-format references (repeatable). "
            "Defaults: ./converted factory banks and presets, ./converted factory presets, ./user presets"
        ),
    )
    parser.add_argument(
        "--flat",
        action="store_true",
        help="Write all output files directly into output root (no category folders).",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Overwrite existing output files.",
    )
    parser.add_argument(
        "--no-metadata-match",
        action="store_true",
        help="Disable fallback matching by factory/user metadata (author+description).",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    refs = list(args.reference_root)
    if not refs:
        refs = [
            Path("converted factory banks and presets"),
            Path("converted factory presets"),
            Path("user presets"),
        ]

    summary = convert(
        factory_root=args.factory_root,
        output_root=args.output_root,
        reference_roots=refs,
        preserve_tree=not args.flat,
        overwrite=args.overwrite,
        allow_metadata_match=not args.no_metadata_match,
    )

    print("Factory -> User proxy conversion complete")
    print(f"Factory total : {summary['total_factory_files']}")
    print(f"Matched       : {summary['matched']}")
    print(f"Missing       : {summary['missing']}")
    print(f"Coverage      : {summary['coverage_percent']}%")
    print(f"Summary JSON  : {summary['output_root']}/conversion_summary.json")
    print(f"Matched CSV   : {summary['matched_csv']}")
    print(f"Missing list  : {summary['missing_txt']}")
    if summary["missing"] > 0:
        print("Note: missing entries need manual Save As in the plugin, or more reference presets.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
