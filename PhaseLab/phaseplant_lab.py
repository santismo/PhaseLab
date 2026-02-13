#!/usr/bin/env python3
"""
Phase Plant preset lab.

Operations:
- mutate: mutate one .phaseplant preset
- combine: crossover values from multiple .phaseplant presets
- random: create a random preset from a preset pool

A .phaseplant file is treated as a zip archive containing state.json and
optional embedded assets (for example wav files). This tool always keeps
state.json valid JSON and preserves assets.
"""

from __future__ import annotations

import argparse
import copy
import fnmatch
import hashlib
import json
import math
import random
import re
import struct
import sys
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, Iterable, List, Sequence, Tuple
from zipfile import ZIP_DEFLATED, ZipFile


NUMERIC_RE = re.compile(r"^[+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?$")
INT_RE = re.compile(r"^[+-]?\d+$")
INDEX_RE = re.compile(r"\[\d+\]")

# Paths that often hold references or external resources.
SKIP_PATH_FRAGMENTS = (
    ".path",
    ".resource",
    ".sample_name",
    ".wavetable_name",
    ".preset_path",
    ".group_name",
    ".version",
    ".schema",
    ".build",
    ".author",
    ".comment",
    ".name",
)

# These string fields are safe/useful to mutate with controlled enum pools.
ENUM_MUTATION_SUFFIXES = (
    ".analog.waveform",
    ".wavetable.wavetable_name",
    ".unison.mode",
    ".noise.type",
    ".distortion.type",
    ".nonlinear_filter.type",
    ".nonlinear_filter.dirt_type",
    ".filter.type",
    ".modulators[].trigger.note_trigger",
    ".rate.sync_mode",
    ".rate.rate_denominator",
    ".granular.rate_denominator",
)

DEFAULT_ANALOG_WAVEFORMS = ("sine", "saw", "square", "triangle", "pulse", "noise")
SOUND_GENERATOR_TYPES = {
    "analog_generator",
    "wavetable_generator",
    "sampler_generator",
    "granular_generator",
    "noise_generator",
    "oscillator",
}
LOGICAL_MAX_ACTIVE_MODULES = 20
LOGICAL_MAX_GENERATORS = 8
LOGICAL_MAX_TOTAL_SNAPINS = 14
LOGICAL_MAX_SNAPINS_PER_LANE = 12
SAFE_WAVETABLE_FRAME_MAX = 256.0
SYSTEM_PRESET_ROOTS = (
    Path("/Library/Application Support/Kilohearts/presets/kphp"),
    Path.home() / "Library/Audio/Presets/Kilohearts/Phase Plant",
)
ASCII_TOKEN_RE = re.compile(rb"[A-Za-z][A-Za-z0-9 '&+_/\-]{2,63}")
SYSTEM_PRESET_SCAN_LIMIT = 2000
SYSTEM_PRESET_MAX_BYTES = 2_000_000
META_AUTHOR_BYTES_RE = re.compile(rb'("author"\s*:\s*")([^"]*)(")')
META_DESCRIPTION_BYTES_RE = re.compile(rb'("description"\s*:\s*")([^"]*)(")')


@dataclass
class Preset:
    source: Path
    state: Dict[str, Any]
    assets: Dict[str, bytes]


def fail(message: str) -> None:
    print(f"error: {message}", file=sys.stderr)
    raise SystemExit(2)


def load_preset(path: Path) -> Preset:
    if not path.exists():
        fail(f"preset not found: {path}")
    assets: Dict[str, bytes] = {}
    with ZipFile(path, "r") as zf:
        if "state.json" not in zf.namelist():
            fail(f"{path} has no state.json")
        state = json.loads(zf.read("state.json").decode("utf-8"))
        for name in zf.namelist():
            if name == "state.json" or name.endswith("/"):
                continue
            assets[name] = zf.read(name)
    return Preset(source=path, state=state, assets=assets)


def write_preset(path: Path, state: Dict[str, Any], assets: Dict[str, bytes]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    state_blob = json.dumps(state, indent="\t", ensure_ascii=False).encode("utf-8")
    with ZipFile(path, "w", compression=ZIP_DEFLATED) as zf:
        zf.writestr("state.json", state_blob)
        for name in sorted(assets):
            zf.writestr(name, assets[name])


def value_kind(value: str) -> str:
    if value in ("true", "false"):
        return "bool"
    if NUMERIC_RE.fullmatch(value):
        return "num"
    return "str"


def normalize_path(path: str) -> str:
    return INDEX_RE.sub("[]", path)


def is_enum_mutation_path(path: str) -> bool:
    canonical = normalize_path(path).lower()
    return any(canonical.endswith(suffix) for suffix in ENUM_MUTATION_SUFFIXES)


def is_wavetable_frame_path(path: str) -> bool:
    return normalize_path(path).lower().endswith(".wavetable.frame")


def is_wavetable_name_path(path: str) -> bool:
    return normalize_path(path).lower().endswith(".wavetable.wavetable_name")


def is_mutation_path(path: str) -> bool:
    low = path.lower()
    canonical = normalize_path(low)
    if is_enum_mutation_path(path):
        return True
    if any(fragment in low for fragment in SKIP_PATH_FRAGMENTS):
        return False
    if low.endswith(".$type") or low.endswith(".$id") or low.endswith(".id"):
        return False
    if ".globals.modulations[" in low:
        return False
    if ".lanes[" in low and (low.endswith(".solo") or low.endswith(".mute")):
        return False
    if canonical.endswith(".enabled"):
        return False
    if canonical.endswith(".send_enabled"):
        return False
    if canonical.endswith(".mute"):
        return False
    if canonical.endswith(".solo"):
        return False
    if canonical.endswith(".bypass"):
        return False
    if canonical.endswith(".active"):
        return False
    if canonical.endswith(".lanes[].enabled"):
        return False
    if low.endswith(".output.send_enabled"):
        return False
    if ".modulators[" in low and low.endswith(".enabled"):
        return False
    # Preserve module/group topology and core routing in diffusion/fusion.
    if ".voice.modules[" in low:
        if low.endswith(".type") or low.endswith(".index"):
            return False
        if ".group." in low:
            return False
        if low.endswith(".output.target"):
            return False
    return True


def is_pitch_path(path: str) -> bool:
    canonical = normalize_path(path).lower()
    pitch_tags = (
        ".pitch",
        "transpose",
        "semitone",
        "detune",
        "octave",
        "coarse",
        "fine_tune",
        ".tune",
        ".note",
        ".keytrack",
    )
    return any(tag in canonical for tag in pitch_tags)


def is_sync_path(path: str) -> bool:
    canonical = normalize_path(path).lower()
    sync_tags = (
        ".sync",
        "sync_mode",
        "synced",
        ".rate",
        ".tempo",
        "rate_denominator",
        "rate_numerator",
        ".length",
    )
    return any(tag in canonical for tag in sync_tags)


def is_protected_switch_path(path: str) -> bool:
    canonical = normalize_path(path).lower()
    return (
        canonical.endswith(".enabled")
        or canonical.endswith(".send_enabled")
        or canonical.endswith(".mute")
        or canonical.endswith(".solo")
        or canonical.endswith(".bypass")
        or canonical.endswith(".active")
    )


def capture_protected_switches(state: Dict[str, Any]) -> Dict[str, str]:
    captured: Dict[str, str] = {}
    for path, value in walk_leaves(state):
        if not isinstance(value, str):
            continue
        if value not in ("true", "false"):
            continue
        if not is_protected_switch_path(path):
            continue
        captured[path] = value
    return captured


def restore_protected_switches(state: Dict[str, Any], switches: Dict[str, str]) -> int:
    restored = 0
    for path, expected in switches.items():
        if set_by_path(state, path, expected):
            restored += 1
    return restored


def _iter_lane_snapins(state: Dict[str, Any]) -> Iterable[Tuple[Dict[str, Any], List[Dict[str, Any]]]]:
    lanes = state.get("model", {}).get("lanes")
    if not isinstance(lanes, list):
        return
    for lane in lanes:
        if not isinstance(lane, dict):
            continue
        holder = lane.get("snapins")
        if not isinstance(holder, dict):
            continue
        snapins = holder.get("snapins")
        if not isinstance(snapins, list):
            continue
        yield lane, [s for s in snapins if isinstance(s, dict)]


def collect_snapin_templates(states: Sequence[Dict[str, Any]], max_items: int = 256) -> List[Dict[str, Any]]:
    templates: List[Dict[str, Any]] = []
    for state in states:
        for _lane, snapins in _iter_lane_snapins(state):
            for snapin in snapins:
                if not isinstance(snapin.get("state"), dict):
                    continue
                if not isinstance(snapin.get("type_id"), str):
                    continue
                templates.append(copy.deepcopy(snapin))
                if len(templates) >= max_items:
                    return templates
    return templates


def next_snapin_instance_id(state: Dict[str, Any]) -> str:
    max_id = 0
    for _lane, snapins in _iter_lane_snapins(state):
        for snapin in snapins:
            value = snapin.get("instance_id")
            if isinstance(value, int):
                max_id = max(max_id, value)
            elif isinstance(value, str) and value.isdigit():
                max_id = max(max_id, int(value))
    return str(max_id + 1)


def collect_lfo_templates(states: Sequence[Dict[str, Any]], max_items: int = 256) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    curve_pool: List[Dict[str, Any]] = []
    table_pool: List[Dict[str, Any]] = []
    for state in states:
        modulators = state.get("model", {}).get("modulators")
        if not isinstance(modulators, list):
            continue
        for mod in modulators:
            if not isinstance(mod, dict):
                continue
            lfo = mod.get("lfo")
            if not isinstance(lfo, dict):
                continue
            curve = lfo.get("curve")
            if isinstance(curve, dict):
                data = curve.get("data")
                if isinstance(data, dict) and isinstance(data.get("segments"), list) and data.get("segments"):
                    curve_pool.append(copy.deepcopy(data))
            table = lfo.get("table")
            if isinstance(table, dict):
                data = table.get("data")
                if isinstance(data, dict) and data:
                    table_pool.append(copy.deepcopy(data))
            if len(curve_pool) >= max_items and len(table_pool) >= max_items:
                return curve_pool[:max_items], table_pool[:max_items]
    return curve_pool[:max_items], table_pool[:max_items]


def diffuse_lane_fx_and_lfo(
    state: Dict[str, Any],
    rng: random.Random,
    amount: float,
    donor_states: Sequence[Dict[str, Any]],
    preserve_similarity: float = 0.75,
) -> int:
    changes = 0
    amt = max(0.0, min(1.0, amount))
    if amt <= 0.0:
        return 0
    preserve = max(0.0, min(1.0, preserve_similarity))

    snapin_pool = collect_snapin_templates(donor_states)
    if not snapin_pool:
        snapin_pool = collect_snapin_templates([state])
    curve_pool, table_pool = collect_lfo_templates(donor_states if donor_states else [state])
    enum_pool = build_enum_pool(([state] + list(donor_states)) if donor_states else [state])

    lanes = state.get("model", {}).get("lanes")
    if isinstance(lanes, list):
        for lane in lanes:
            if not isinstance(lane, dict):
                continue
            holder = lane.get("snapins")
            if not isinstance(holder, dict):
                continue
            snapins = holder.get("snapins")
            if not isinstance(snapins, list):
                continue

            # Keep lane structure mostly intact and diffuse effect parameter values in place.
            for snapin in snapins:
                if not isinstance(snapin, dict):
                    continue
                snapin_state = snapin.get("state")
                if not isinstance(snapin_state, dict):
                    continue
                for local_path, value in list(walk_leaves(snapin_state)):
                    if not isinstance(value, str):
                        continue
                    full_path = f"model.lanes[].snapins[].state.{local_path}"
                    if not is_mutation_path(full_path):
                        continue
                    path_chance = (0.07 + 0.28 * amt) * (1.0 - 0.45 * preserve)
                    if is_sync_path(full_path):
                        path_chance = max(path_chance, 0.28 + 0.45 * amt)
                    if rng.random() > min(1.0, path_chance):
                        continue
                    new_value = mutate_string_value(full_path, value, rng, max(0.10, amt), enum_pool)
                    if new_value != value and set_by_path(snapin_state, local_path, new_value):
                        changes += 1

            # Edit existing effect slots by borrowing same-type state from pool.
            for idx, snapin in enumerate(list(snapins)):
                if not isinstance(snapin, dict):
                    continue
                if amt < 0.45:
                    continue
                edit_chance = (0.03 + 0.26 * amt) * (1.0 - 0.60 * preserve)
                if rng.random() > min(1.0, edit_chance):
                    continue
                same_type = [
                    item for item in snapin_pool if item.get("type_id") == snapin.get("type_id") and item is not snapin
                ]
                if not same_type:
                    continue
                donor = copy.deepcopy(rng.choice(same_type))
                donor_state = donor.get("state")
                if not isinstance(donor_state, dict):
                    continue
                if snapin.get("state") != donor_state:
                    snapins[idx]["state"] = donor_state
                    changes += 1

            # Add an effect slot from pool at medium/high diffusion.
            add_chance = 0.0
            if amt >= 0.60:
                add_chance = (0.08 + 0.32 * ((amt - 0.60) / 0.40)) * (1.0 - 0.45 * preserve)
            if snapin_pool and len(snapins) < 8 and rng.random() < add_chance:
                candidate = copy.deepcopy(rng.choice(snapin_pool))
                candidate["instance_id"] = next_snapin_instance_id(state)
                candidate.setdefault("$type", "snapin_instance")
                snapins.append(candidate)
                changes += 1

    modulators = state.get("model", {}).get("modulators")
    if isinstance(modulators, list):
        for mod in modulators:
            if not isinstance(mod, dict):
                continue
            lfo = mod.get("lfo")
            if not isinstance(lfo, dict):
                continue

            lfo_change_chance = (0.14 + 0.42 * amt) * (1.0 - 0.30 * preserve)
            if table_pool and rng.random() < min(1.0, lfo_change_chance):
                table = lfo.get("table")
                if not isinstance(table, dict):
                    table = {"$type": "table_data"}
                    lfo["table"] = table
                replacement = copy.deepcopy(rng.choice(table_pool))
                if table.get("data") != replacement:
                    table["data"] = replacement
                    changes += 1

            if curve_pool and rng.random() < min(1.0, lfo_change_chance):
                curve = lfo.get("curve")
                if not isinstance(curve, dict):
                    curve = {"$type": "curve_data"}
                    lfo["curve"] = curve
                replacement = copy.deepcopy(rng.choice(curve_pool))
                if curve.get("data") != replacement:
                    curve["data"] = replacement
                    changes += 1

            frame = lfo.get("frame")
            if isinstance(frame, str) and NUMERIC_RE.fullmatch(frame) and rng.random() < (0.25 + 0.55 * amt):
                try:
                    current = float(frame)
                except ValueError:
                    current = 0.0
                if rng.random() < 0.55:
                    current = rng.uniform(0.0, 255.0)
                else:
                    current += (rng.random() * 2.0 - 1.0) * (8.0 + 80.0 * amt)
                current = max(0.0, min(255.0, current))
                replacement = _format_decimal(current)
                if replacement != frame:
                    lfo["frame"] = replacement
                    changes += 1

    return changes


def _module_type(module: Dict[str, Any]) -> str:
    value = module.get("type")
    return value if isinstance(value, str) else ""


def count_active_modules(state: Dict[str, Any]) -> int:
    modules = state.get("model", {}).get("voice", {}).get("modules")
    if not isinstance(modules, list):
        return 0
    return sum(1 for module in modules if isinstance(module, dict) and _module_type(module) != "none")


def count_generators(state: Dict[str, Any]) -> int:
    modules = state.get("model", {}).get("voice", {}).get("modules")
    if not isinstance(modules, list):
        return 0
    return sum(1 for module in modules if isinstance(module, dict) and _module_type(module) in SOUND_GENERATOR_TYPES)


def count_lane_snapins(state: Dict[str, Any]) -> int:
    total = 0
    for _lane, snapins in _iter_lane_snapins(state):
        total += len(snapins)
    return total


def _quantile(values: Sequence[int], q: float) -> int:
    if not values:
        return 0
    ordered = sorted(values)
    idx = int(round((len(ordered) - 1) * max(0.0, min(1.0, q))))
    return ordered[idx]


def _clamp_module_output(module: Dict[str, Any], lane_count: int) -> int:
    if lane_count <= 0:
        lane_count = 1
    repairs = 0
    output = module.get("output")
    if not isinstance(output, dict):
        output = {"$type": "output_module"}
        module["output"] = output
        repairs += 1
    target = output.get("target")
    lane_idx = parse_lane_index(target) if isinstance(target, str) else None
    if lane_idx is None or lane_idx < 0 or lane_idx >= lane_count:
        output["target"] = f"lane_{min(max(lane_idx or 0, 0), lane_count - 1)}"
        repairs += 1
    if output.get("send_enabled") != "true":
        output["send_enabled"] = "true"
        repairs += 1
    gain = parse_floatish(output.get("gain"), default=0.0)
    if gain <= 0.001:
        output["gain"] = "0.25"
        repairs += 1
    return repairs


def _normalize_module_for_slot(module: Dict[str, Any], slot_idx: int, lane_count: int) -> int:
    repairs = 0
    if isinstance(module.get("index"), int):
        if module["index"] != slot_idx:
            module["index"] = slot_idx
            repairs += 1
    else:
        index_str = str(slot_idx)
        if module.get("index") != index_str:
            module["index"] = index_str
            repairs += 1
    mod_type = _module_type(module)
    if mod_type != "none" and module.get("enabled") != "true":
        module["enabled"] = "true"
        repairs += 1
    if mod_type in SOUND_GENERATOR_TYPES:
        repairs += _clamp_module_output(module, lane_count)
    return repairs


def collect_module_templates(states: Sequence[Dict[str, Any]], max_items: int = 512) -> Dict[str, List[Dict[str, Any]]]:
    templates: Dict[str, List[Dict[str, Any]]] = {"all": [], "groups": [], "generators": [], "effects": []}
    for state in states:
        modules = state.get("model", {}).get("voice", {}).get("modules")
        if not isinstance(modules, list):
            continue
        for module in modules:
            if not isinstance(module, dict):
                continue
            mod_type = _module_type(module)
            if mod_type == "none":
                continue
            item = copy.deepcopy(module)
            templates["all"].append(item)
            if mod_type == "group":
                templates["groups"].append(item)
            elif mod_type in SOUND_GENERATOR_TYPES:
                templates["generators"].append(item)
            else:
                templates["effects"].append(item)
            if len(templates["all"]) >= max_items:
                return templates
    return templates


def catalyze_expand_structure(
    state: Dict[str, Any],
    donor_states: Sequence[Dict[str, Any]],
    rng: random.Random,
    complexity: float,
) -> int:
    changes = 0
    complexity = max(0.0, min(1.0, complexity))
    if complexity <= 0.01:
        return 0

    model = state.get("model")
    if not isinstance(model, dict):
        return 0
    voice = model.get("voice")
    if not isinstance(voice, dict):
        return 0
    modules = voice.get("modules")
    lanes = model.get("lanes")
    if not isinstance(modules, list) or not isinstance(lanes, list):
        return 0
    voice_lane_limit = _effective_voice_lane_limit(model, lanes) if lanes else 1

    donor_stats = {
        "active": [count_active_modules(s) for s in donor_states],
        "generators": [count_generators(s) for s in donor_states],
        "snapins": [count_lane_snapins(s) for s in donor_states],
    }
    active_now = sum(1 for module in modules if isinstance(module, dict) and _module_type(module) != "none")
    generators_now = sum(1 for module in modules if isinstance(module, dict) and _module_type(module) in SOUND_GENERATOR_TYPES)
    empty_slots = [idx for idx, module in enumerate(modules) if isinstance(module, dict) and _module_type(module) == "none"]

    templates = collect_module_templates(donor_states if donor_states else [state])
    all_templates = templates.get("all", [])
    group_templates = templates.get("groups", [])
    generator_templates = templates.get("generators", [])
    effect_templates = templates.get("effects", [])

    donor_active_goal = _quantile(donor_stats["active"], 0.85)
    donor_gen_goal = _quantile(donor_stats["generators"], 0.85)
    target_active = max(active_now, int(round((1.0 - complexity) * active_now + complexity * max(donor_active_goal + 2, 8))))
    target_active = min(LOGICAL_MAX_ACTIVE_MODULES, target_active, len(modules))
    target_generators = max(generators_now, int(round((1.0 - complexity) * generators_now + complexity * max(donor_gen_goal + 1, 2))))
    target_generators = min(LOGICAL_MAX_GENERATORS, target_generators)

    while empty_slots and active_now < target_active and all_templates:
        slot_idx = empty_slots.pop(0)
        pool = all_templates
        if generators_now < target_generators and generator_templates and rng.random() < 0.72:
            pool = generator_templates
        elif group_templates and rng.random() < (0.20 + 0.25 * complexity):
            pool = group_templates
        elif effect_templates and rng.random() < (0.45 + 0.20 * complexity):
            pool = effect_templates
        template = copy.deepcopy(rng.choice(pool))
        changes += _normalize_module_for_slot(template, slot_idx, voice_lane_limit)
        modules[slot_idx] = template
        active_now += 1
        if _module_type(template) in SOUND_GENERATOR_TYPES:
            generators_now += 1
        changes += 1

    snapin_pool = collect_snapin_templates(donor_states if donor_states else [state], max_items=768)
    if snapin_pool:
        target_snapins = max(
            count_lane_snapins(state),
            int(
                round(
                    (1.0 - complexity) * count_lane_snapins(state)
                    + complexity * max(_quantile(donor_stats["snapins"], 0.85) + 2, 6)
                )
            ),
        )
        target_snapins = min(LOGICAL_MAX_TOTAL_SNAPINS, target_snapins)
        lane_refs = list(_iter_lane_snapins(state))
        if lane_refs:
            current_snapins = count_lane_snapins(state)
            while current_snapins < target_snapins:
                eligible = [item for item in lane_refs if len(item[1]) < LOGICAL_MAX_SNAPINS_PER_LANE]
                if not eligible:
                    break
                lane, lane_snapins = rng.choice(eligible)
                holder = lane.get("snapins")
                if not isinstance(holder, dict):
                    break
                raw = holder.get("snapins")
                if not isinstance(raw, list):
                    break
                candidate = copy.deepcopy(rng.choice(snapin_pool))
                candidate["instance_id"] = next_snapin_instance_id(state)
                candidate.setdefault("$type", "snapin_instance")
                raw.append(candidate)
                lane_snapins.append(candidate)
                current_snapins += 1
                changes += 1

    return changes


def walk_leaves(value: Any, path: str = "") -> Iterable[Tuple[str, Any]]:
    if isinstance(value, dict):
        for key, child in value.items():
            child_path = f"{path}.{key}" if path else key
            yield from walk_leaves(child, child_path)
        return
    if isinstance(value, list):
        for idx, child in enumerate(value):
            child_path = f"{path}[{idx}]"
            yield from walk_leaves(child, child_path)
        return
    yield path, value


def set_by_path(root: Any, path: str, value: Any) -> bool:
    cur = root
    token = ""
    i = 0
    steps: List[Tuple[Any, Any]] = []

    while i < len(path):
        ch = path[i]
        if ch == ".":
            if token:
                if not isinstance(cur, dict) or token not in cur:
                    return False
                steps.append((cur, token))
                cur = cur[token]
                token = ""
            i += 1
            continue
        if ch == "[":
            if token:
                if not isinstance(cur, dict) or token not in cur:
                    return False
                steps.append((cur, token))
                cur = cur[token]
                token = ""
            end = path.find("]", i)
            if end == -1:
                return False
            idx_text = path[i + 1 : end]
            if not idx_text.isdigit() or not isinstance(cur, list):
                return False
            idx = int(idx_text)
            if idx >= len(cur):
                return False
            steps.append((cur, idx))
            cur = cur[idx]
            i = end + 1
            continue
        token += ch
        i += 1

    if token:
        if not isinstance(cur, dict) or token not in cur:
            return False
        parent = cur
        key = token
    else:
        if not steps:
            return False
        parent, key = steps[-1]

    parent[key] = value
    return True


def parse_boolish(value: Any, default: bool = False) -> bool:
    if isinstance(value, str):
        low = value.strip().lower()
        if low in ("true", "1", "yes", "on"):
            return True
        if low in ("false", "0", "no", "off"):
            return False
    if isinstance(value, bool):
        return value
    return default


def parse_floatish(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def capture_generator_ingredient_guard(state: Dict[str, Any]) -> Dict[int, Dict[str, Any]]:
    guard: Dict[int, Dict[str, Any]] = {}
    modules = state.get("model", {}).get("voice", {}).get("modules")
    if not isinstance(modules, list):
        return guard
    for idx, module in enumerate(modules):
        if not isinstance(module, dict):
            continue
        mod_type = module.get("type")
        if not isinstance(mod_type, str) or mod_type not in SOUND_GENERATOR_TYPES:
            continue
        output = module.get("output") if isinstance(module.get("output"), dict) else {}
        mix = module.get("mix") if isinstance(module.get("mix"), dict) else {}
        group = module.get("group") if isinstance(module.get("group"), dict) else None
        guard[idx] = {
            "type": mod_type,
            "enabled": str(module.get("enabled", "true")),
            "group": copy.deepcopy(group) if group is not None else None,
            "output_target": output.get("target"),
            "output_send_enabled": output.get("send_enabled"),
            "output_gain": parse_floatish(output.get("gain"), default=0.0),
            "mix_gain": parse_floatish(mix.get("gain"), default=-1.0),
        }
    return guard


def enforce_generator_ingredient_guard(
    state: Dict[str, Any],
    guard: Dict[int, Dict[str, Any]],
    gain_floor_ratio: float = 0.35,
) -> int:
    repaired = 0
    modules = state.get("model", {}).get("voice", {}).get("modules")
    if not isinstance(modules, list):
        return repaired
    for idx, data in guard.items():
        if idx < 0 or idx >= len(modules):
            continue
        module = modules[idx]
        if not isinstance(module, dict):
            continue

        expected_type = data.get("type")
        current_type = module.get("type")
        if isinstance(expected_type, str):
            if expected_type in SOUND_GENERATOR_TYPES:
                # Preserve generator slots as generators, while still allowing
                # diffusion to morph generator flavor (e.g. analog <-> wavetable).
                if not isinstance(current_type, str) or current_type not in SOUND_GENERATOR_TYPES:
                    module["type"] = expected_type
                    repaired += 1
            elif module.get("type") != expected_type:
                module["type"] = expected_type
                repaired += 1

        expected_enabled = data.get("enabled")
        if isinstance(expected_enabled, str) and module.get("enabled") != expected_enabled:
            module["enabled"] = expected_enabled
            repaired += 1

        expected_group = data.get("group")
        if isinstance(expected_group, dict):
            current_group = module.get("group")
            if not isinstance(current_group, dict) or current_group != expected_group:
                module["group"] = copy.deepcopy(expected_group)
                repaired += 1

        output = module.get("output")
        if not isinstance(output, dict):
            output = {"$type": "output_module"}
            module["output"] = output
            repaired += 1

        expected_target = data.get("output_target")
        if isinstance(expected_target, str) and output.get("target") != expected_target:
            output["target"] = expected_target
            repaired += 1

        expected_send = data.get("output_send_enabled")
        if isinstance(expected_send, str) and output.get("send_enabled") != expected_send:
            output["send_enabled"] = expected_send
            repaired += 1

        original_output_gain = parse_floatish(data.get("output_gain"), default=0.0)
        if original_output_gain > 0.001:
            floor = max(0.03, original_output_gain * gain_floor_ratio)
            current_gain = parse_floatish(output.get("gain"), default=original_output_gain)
            if current_gain < floor:
                output["gain"] = _format_decimal(floor)
                repaired += 1

        mix = module.get("mix")
        if isinstance(mix, dict):
            original_mix_gain = parse_floatish(data.get("mix_gain"), default=-1.0)
            if original_mix_gain > 0.001:
                floor = max(0.03, original_mix_gain * gain_floor_ratio)
                current_mix_gain = parse_floatish(mix.get("gain"), default=original_mix_gain)
                if current_mix_gain < floor:
                    mix["gain"] = _format_decimal(floor)
                    repaired += 1
    return repaired


def mutate_generator_types(
    state: Dict[str, Any],
    rng: random.Random,
    amount: float,
    donor_states: Sequence[Dict[str, Any]] | None = None,
) -> int:
    changes = 0
    amount = max(0.0, min(1.0, amount))
    modules = state.get("model", {}).get("voice", {}).get("modules")
    if not isinstance(modules, list):
        return 0

    donor_states = donor_states or []
    allowed_types = {"analog_generator", "wavetable_generator"}
    for donor_state in donor_states:
        donor_modules = donor_state.get("model", {}).get("voice", {}).get("modules")
        if not isinstance(donor_modules, list):
            continue
        for module in donor_modules:
            if not isinstance(module, dict):
                continue
            mod_type = module.get("type")
            if isinstance(mod_type, str) and mod_type in ("analog_generator", "wavetable_generator"):
                allowed_types.add(mod_type)
    type_pool = sorted(allowed_types) if allowed_types else ["analog_generator", "wavetable_generator"]

    wt_names = tuple(discover_wavetable_name_pool())
    analog_options = tuple(DEFAULT_ANALOG_WAVEFORMS)
    mutate_chance = 0.06 + 0.22 * amount

    for module in modules:
        if not isinstance(module, dict):
            continue
        current_type = module.get("type")
        if not isinstance(current_type, str) or current_type not in SOUND_GENERATOR_TYPES:
            continue
        if rng.random() > mutate_chance:
            continue

        candidates = [entry for entry in type_pool if entry != current_type]
        if not candidates:
            continue
        new_type = rng.choice(candidates)
        module["type"] = new_type
        changes += 1

        if module.get("enabled") != "true":
            module["enabled"] = "true"
            changes += 1

        output = module.get("output")
        if not isinstance(output, dict):
            output = {"$type": "output_module"}
            module["output"] = output
            changes += 1
        if output.get("send_enabled") != "true":
            output["send_enabled"] = "true"
            changes += 1
        if parse_floatish(output.get("gain"), default=0.0) < 0.05:
            output["gain"] = "0.25"
            changes += 1

        if new_type == "analog_generator":
            analog = module.get("analog")
            if not isinstance(analog, dict):
                analog = {"$type": "analog_generator"}
                module["analog"] = analog
                changes += 1
            if not isinstance(analog.get("waveform"), str) or analog.get("waveform") not in analog_options:
                analog["waveform"] = rng.choice(analog_options)
                changes += 1
        elif new_type == "wavetable_generator":
            wavetable = module.get("wavetable")
            if not isinstance(wavetable, dict):
                wavetable = {"$type": "wavetable_generator"}
                module["wavetable"] = wavetable
                changes += 1
            current_name = wavetable.get("wavetable_name")
            if not isinstance(current_name, str) or not current_name.strip():
                if wt_names:
                    wavetable["wavetable_name"] = rng.choice(wt_names)
                    changes += 1
            frame = parse_floatish(wavetable.get("frame"), default=0.0)
            if frame < 0.0 or frame > SAFE_WAVETABLE_FRAME_MAX:
                wavetable["frame"] = _format_decimal(max(0.0, min(SAFE_WAVETABLE_FRAME_MAX, frame)))
                changes += 1

    return changes


def mutate_active_generator_parameters(
    state: Dict[str, Any],
    rng: random.Random,
    amount: float,
    enum_pool: Dict[str, List[str]],
    preserve_pitch_low: bool = False,
    pitch_protect_threshold: float = 0.30,
) -> int:
    changes = 0
    amount = max(0.0, min(1.0, amount))
    modules = state.get("model", {}).get("voice", {}).get("modules")
    if not isinstance(modules, list):
        return 0

    branch_map = {
        "analog_generator": ("analog", "mix", "filter", "distortion", "unison", "output"),
        "wavetable_generator": ("wavetable", "mix", "filter", "distortion", "unison", "output"),
        "sampler_generator": ("sampler", "mix", "filter", "distortion", "output"),
        "granular_generator": ("granular", "mix", "filter", "distortion", "output"),
        "noise_generator": ("noise", "mix", "filter", "distortion", "output"),
        "oscillator": ("oscillator", "mix", "filter", "distortion", "output"),
    }

    for idx, module in enumerate(modules):
        if not isinstance(module, dict):
            continue
        module_type = module.get("type")
        if not isinstance(module_type, str) or module_type not in SOUND_GENERATOR_TYPES:
            continue
        if not parse_boolish(module.get("enabled"), default=True):
            continue

        branches = branch_map.get(module_type, ())
        module_paths: List[Tuple[str, str]] = []
        module_changed = 0
        for branch in branches:
            payload = module.get(branch)
            if not isinstance(payload, dict):
                continue
            for local_path, value in walk_leaves(payload, branch):
                if not isinstance(value, str):
                    continue
                full_path = f"model.voice.modules[{idx}].{local_path}"
                if not is_mutation_path(full_path):
                    continue
                if preserve_pitch_low and amount <= pitch_protect_threshold and is_pitch_path(full_path):
                    continue
                module_paths.append((full_path, value))
                path_chance = 0.20 + 0.60 * amount
                canonical = normalize_path(full_path).lower()
                if canonical.endswith(".analog.waveform") or canonical.endswith(".wavetable.wavetable_name"):
                    path_chance = max(path_chance, 0.65)
                if is_wavetable_frame_path(full_path):
                    path_chance = max(path_chance, 0.75)
                if is_sync_path(full_path):
                    path_chance = max(path_chance, 0.55)
                if rng.random() > min(1.0, path_chance):
                    continue
                new_value = mutate_string_value(full_path, value, rng, max(0.20, amount), enum_pool)
                if new_value == value and value_kind(value) == "num" and not INT_RE.fullmatch(value):
                    try:
                        n = float(value)
                    except ValueError:
                        n = 0.0
                    span = max(abs(n), 0.2) * (0.18 + 0.45 * amount)
                    n2 = n + (rng.random() * 2.0 - 1.0) * span
                    n2 = clamp_numeric(full_path, n2)
                    if 0.0 <= n <= 1.0:
                        n2 = max(0.0, min(1.0, n2))
                    if n >= 0:
                        n2 = max(0.0, n2)
                    if math.isfinite(n2):
                        new_value = _format_decimal(n2)
                if new_value != value and set_by_path(state, full_path, new_value):
                    changes += 1
                    module_changed += 1

        # Ensure at least one audible parameter tweak per active generator at medium+ amounts.
        if module_paths and module_changed == 0 and amount >= 0.30:
            full_path, value = rng.choice(module_paths)
            forced = mutate_string_value(full_path, value, rng, max(0.45, amount), enum_pool)
            if forced == value and value_kind(value) == "num" and not INT_RE.fullmatch(value):
                try:
                    n = float(value)
                except ValueError:
                    n = 0.0
                span = max(abs(n), 0.25) * (0.25 + 0.55 * amount)
                n2 = n + (rng.random() * 2.0 - 1.0) * span
                n2 = clamp_numeric(full_path, n2)
                if 0.0 <= n <= 1.0:
                    n2 = max(0.0, min(1.0, n2))
                if n >= 0:
                    n2 = max(0.0, n2)
                if math.isfinite(n2):
                    forced = _format_decimal(n2)
            if forced != value and set_by_path(state, full_path, forced):
                changes += 1

    return changes


def modulation_target_is_pitch(modulation: Dict[str, Any]) -> bool:
    target = modulation.get("target")
    if not isinstance(target, dict):
        return False
    path = target.get("path")
    if not isinstance(path, dict):
        return False
    entries = path.get("entries")
    if not isinstance(entries, list):
        return False
    joined = ".".join(str(entry).lower() for entry in entries)
    return "pitch" in joined or "transpose" in joined or "semitone" in joined or "detune" in joined or "octave" in joined


def mutate_global_modulation_amounts(
    state: Dict[str, Any],
    rng: random.Random,
    amount: float,
    preserve_pitch_low: bool = False,
    pitch_protect_threshold: float = 0.30,
) -> int:
    changes = 0
    amount = max(0.0, min(1.0, amount))
    modulations = state.get("model", {}).get("globals", {}).get("modulations")
    if not isinstance(modulations, list):
        return 0

    for modulation in modulations:
        if not isinstance(modulation, dict):
            continue
        if preserve_pitch_low and amount <= pitch_protect_threshold and modulation_target_is_pitch(modulation):
            continue

        amount_value = modulation.get("amount")
        if isinstance(amount_value, str) and NUMERIC_RE.fullmatch(amount_value):
            if rng.random() < (0.30 + 0.60 * amount):
                try:
                    n = float(amount_value)
                except ValueError:
                    n = 0.0
                span = max(abs(n), 0.2) * (0.20 + 0.55 * amount)
                n2 = n + (rng.random() * 2.0 - 1.0) * span
                n2 = max(-2.0, min(2.0, n2))
                updated = _format_decimal(n2)
                if updated != amount_value:
                    modulation["amount"] = updated
                    changes += 1

        curve_value = modulation.get("curve")
        if isinstance(curve_value, str) and NUMERIC_RE.fullmatch(curve_value):
            if rng.random() < (0.18 + 0.45 * amount):
                try:
                    c = float(curve_value)
                except ValueError:
                    c = 0.0
                c2 = c + (rng.random() * 2.0 - 1.0) * (0.08 + 0.35 * amount)
                c2 = max(-1.0, min(1.0, c2))
                updated = _format_decimal(c2)
                if updated != curve_value:
                    modulation["curve"] = updated
                    changes += 1

    return changes


def count_changed_leaf_values(base_state: Dict[str, Any], candidate_state: Dict[str, Any]) -> int:
    base_map = {path: value for path, value in walk_leaves(base_state)}
    changed = 0
    for path, value in walk_leaves(candidate_state):
        if base_map.get(path) != value:
            changed += 1
    return changed


def load_binary_preset_blob(path: Path) -> bytes:
    if not path.exists():
        fail(f"preset not found: {path}")
    return path.read_bytes()


def write_binary_preset_blob(path: Path, blob: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(blob)


def _iter_length_prefixed_strings(blob: bytes) -> Iterable[Tuple[int, int, str, bool]]:
    i = 0
    n = len(blob)
    while i + 8 <= n:
        if i % 4 != 0:
            i += 1
            continue
        length = struct.unpack_from("<I", blob, i)[0]
        if length == 0 or length > 512:
            i += 4
            continue
        end = i + 4 + length
        if end > n:
            i += 4
            continue
        payload = blob[i + 4 : end]
        has_null_terminator = payload.endswith(b"\x00")
        body = payload[:-1] if has_null_terminator else payload
        if not body:
            i = end
            continue
        if b"\x00" in body:
            i += 4
            continue
        printable = sum(32 <= b <= 126 or b in (9, 10, 13) for b in body)
        if printable / len(body) < 0.95:
            i += 4
            continue
        try:
            text = body.decode("utf-8")
        except UnicodeDecodeError:
            try:
                text = body.decode("latin1")
            except UnicodeDecodeError:
                i += 4
                continue
        yield i, length, text, has_null_terminator
        i = end


def _same_length_replacement(
    original: str,
    candidates: Sequence[str],
    rng: random.Random,
) -> str | None:
    valid = [cand for cand in candidates if cand != original and len(cand) == len(original)]
    if not valid:
        return None
    return rng.choice(valid)


def _same_length_numeric_digit_mutation(original: str, rng: random.Random, amount: float) -> str | None:
    # Conservative binary-safe numeric mutation:
    # mutate decimal strings by changing 1-2 digits while preserving exact length.
    if "." not in original:
        return None
    if not NUMERIC_RE.fullmatch(original):
        return None
    if "e" in original.lower():
        return None

    chars = list(original)
    digit_positions = [idx for idx, ch in enumerate(chars) if ch.isdigit()]
    if not digit_positions:
        return None
    if len(digit_positions) == 1:
        return None

    edit_count = 1 if amount < 0.60 else 2
    edit_count = min(edit_count, len(digit_positions))
    chosen = rng.sample(digit_positions, edit_count)
    changed = False
    for idx in chosen:
        current = chars[idx]
        replacements = [d for d in "0123456789" if d != current]
        # Avoid turning first non-sign integer digit into 0 where possible.
        if idx == 0 and chars[0] != "-":
            replacements = [d for d in replacements if d != "0"] or replacements
        if idx == 1 and chars[0] == "-" and len(chars) > 2 and chars[2] == ".":
            replacements = [d for d in replacements if d != "0"] or replacements
        if not replacements:
            continue
        chars[idx] = rng.choice(replacements)
        changed = True
    if not changed:
        return None
    mutated = "".join(chars)
    if mutated == original:
        return None
    if not NUMERIC_RE.fullmatch(mutated):
        return None
    return mutated


def _fit_ascii_text(raw: str, target_len: int) -> str:
    clean = "".join(ch if 32 <= ord(ch) <= 126 else " " for ch in raw)
    if target_len <= 0:
        return ""
    if len(clean) > target_len:
        return clean[:target_len]
    return clean + (" " * (target_len - len(clean)))


def stamp_state_metadata(state: Dict[str, Any], author: str, description: str, preset_name: str | None = None) -> None:
    meta = state.get("meta")
    if not isinstance(meta, dict):
        meta = {}
        state["meta"] = meta
    meta["author"] = author
    meta["description"] = description

    preset = state.get("preset")
    if not isinstance(preset, dict):
        preset = {}
        state["preset"] = preset
    if preset_name:
        preset["name"] = preset_name


def stamp_binary_metadata_blob(blob: bytes, author: str, description: str) -> bytes:
    data = bytearray(blob)
    modified = False

    raw = bytes(data)
    for match in reversed(list(META_AUTHOR_BYTES_RE.finditer(raw))):
        old_value = match.group(2)
        replacement = _fit_ascii_text(author, len(old_value)).encode("ascii", errors="ignore")
        if replacement != old_value:
            data[match.start(2) : match.end(2)] = replacement
            modified = True

    raw = bytes(data)
    for match in reversed(list(META_DESCRIPTION_BYTES_RE.finditer(raw))):
        old_value = match.group(2)
        replacement = _fit_ascii_text(description, len(old_value)).encode("ascii", errors="ignore")
        if replacement != old_value:
            data[match.start(2) : match.end(2)] = replacement
            modified = True

    return bytes(data) if modified else blob


def mutate_binary_preset_blob(
    blob: bytes,
    rng: random.Random,
    amount: float,
    donor_blobs: Sequence[bytes] | None = None,
    float_offsets: Sequence[int] | None = None,
) -> Tuple[bytes, int]:
    data = bytearray(blob)
    changed = 0

    wavetable_pool = discover_wavetable_name_pool()
    waveform_pool = DEFAULT_ANALOG_WAVEFORMS
    # Safe binary mutation mode:
    # Only rewrite known semantic strings with same-length replacements.
    # Avoids structural corruption from raw numeric byte edits.
    for start, length, text, has_null in list(_iter_length_prefixed_strings(bytes(data))):
        replacement: str | None = None
        low = text.lower()
        if low in waveform_pool and rng.random() < min(1.0, 0.35 + amount):
            replacement = _same_length_replacement(text, waveform_pool, rng)
        elif text in wavetable_pool and rng.random() < min(1.0, 0.30 + amount):
            replacement = _same_length_replacement(text, wavetable_pool, rng)
        elif rng.random() < min(1.0, 0.16 + 0.46 * amount):
            replacement = _same_length_numeric_digit_mutation(text, rng, amount)
        if replacement is None:
            continue
        encoded = replacement.encode("utf-8")
        payload = encoded + (b"\x00" if has_null else b"")
        if len(payload) != length:
            continue
        data[start + 4 : start + 4 + length] = payload
        changed += 1

    # IMPORTANT:
    # Binary float/word edits are limited to conservative same-length string swaps.
    _ = float_offsets

    return bytes(data), changed


def combine_binary_preset_blobs(
    base_blob: bytes,
    donor_blobs: Sequence[bytes],
    rng: random.Random,
    mix_rate: float,
    mutate_amount: float,
    float_offsets: Sequence[int] | None = None,
) -> Tuple[bytes, int, int]:
    if not donor_blobs:
        return base_blob, 0, 0
    data = bytearray(base_blob)
    cross_changed = 0
    donors = [blob for blob in donor_blobs if blob]
    if not donors:
        return base_blob, 0, 0

    wavetable_pool = set(discover_wavetable_name_pool())
    waveform_pool = set(DEFAULT_ANALOG_WAVEFORMS)
    base_slots = list(_iter_length_prefixed_strings(bytes(data)))

    donor_waveforms_by_len: Dict[int, List[str]] = {}
    donor_wavetables_by_len: Dict[int, List[str]] = {}
    for donor in donors:
        for _start, _length, text, _has_null in _iter_length_prefixed_strings(donor):
            low = text.lower()
            if low in waveform_pool:
                donor_waveforms_by_len.setdefault(len(text), []).append(text)
            if text in wavetable_pool:
                donor_wavetables_by_len.setdefault(len(text), []).append(text)

    for start, length, text, has_null in base_slots:
        if rng.random() > min(1.0, mix_rate):
            continue
        replacement: str | None = None
        low = text.lower()
        if low in waveform_pool:
            replacement = _same_length_replacement(text, donor_waveforms_by_len.get(len(text), []), rng)
        elif text in wavetable_pool:
            replacement = _same_length_replacement(text, donor_wavetables_by_len.get(len(text), []), rng)
        if replacement is None:
            continue
        encoded = replacement.encode("utf-8")
        payload = encoded + (b"\x00" if has_null else b"")
        if len(payload) != length:
            continue
        data[start + 4 : start + 4 + length] = payload
        cross_changed += 1

    mutated_blob, mutate_changed = mutate_binary_preset_blob(
        bytes(data),
        rng,
        mutate_amount,
        donor_blobs=donors,
        float_offsets=float_offsets,
    )
    return mutated_blob, cross_changed, mutate_changed


@lru_cache(maxsize=1)
def discover_wavetable_name_pool() -> Tuple[str, ...]:
    names: set[str] = set()
    search_roots = [
        Path("/Library/Application Support/Kilohearts/dependencies/factory_wavetables"),
        Path.home() / "Library/Audio/Presets/Kilohearts/User Wavetables",
    ]
    valid_suffixes = {".flac", ".wav", ".wt", ".wavetable"}
    for root in search_roots:
        if not root.is_dir():
            continue
        for file_path in root.rglob("*"):
            if not file_path.is_file():
                continue
            if file_path.suffix.lower() not in valid_suffixes:
                continue
            stem = file_path.stem.strip()
            if stem:
                names.add(stem)
    return tuple(sorted(names))


def is_zip_preset_file(path: Path) -> bool:
    try:
        with path.open("rb") as fh:
            return fh.read(4) == b"PK\x03\x04"
    except OSError:
        return False


@lru_cache(maxsize=1)
def discover_system_preset_hints() -> Dict[str, Tuple[str, ...]]:
    known_wavetables = discover_wavetable_name_pool()
    wavetable_lookup = {name.lower(): name for name in known_wavetables}
    wavetable_counts: Dict[str, int] = {}
    waveform_hits: set[str] = set()

    scanned = 0
    for root in SYSTEM_PRESET_ROOTS:
        if not root.is_dir():
            continue
        for preset_path in root.rglob("*.phaseplant"):
            if scanned >= SYSTEM_PRESET_SCAN_LIMIT:
                break
            scanned += 1
            if is_zip_preset_file(preset_path):
                continue
            try:
                raw = preset_path.read_bytes()
            except OSError:
                continue
            if len(raw) > SYSTEM_PRESET_MAX_BYTES:
                raw = raw[:SYSTEM_PRESET_MAX_BYTES]
            for token_bytes in ASCII_TOKEN_RE.findall(raw):
                try:
                    token = token_bytes.decode("latin1")
                except UnicodeDecodeError:
                    continue
                token = re.sub(r"\s+", " ", token).strip(" _-/")
                if len(token) < 3:
                    continue
                lowered = token.lower()
                if lowered in DEFAULT_ANALOG_WAVEFORMS:
                    waveform_hits.add(lowered)
                wavetable_name = wavetable_lookup.get(lowered)
                if wavetable_name:
                    wavetable_counts[wavetable_name] = wavetable_counts.get(wavetable_name, 0) + 1
        if scanned >= SYSTEM_PRESET_SCAN_LIMIT:
            break

    preferred_wavetables = tuple(
        name for name, _ in sorted(wavetable_counts.items(), key=lambda item: (-item[1], item[0].lower()))
    )
    return {
        "preferred_wavetables": preferred_wavetables,
        "analog_waveforms": tuple(sorted(waveform_hits)),
    }


def build_enum_pool(states: Sequence[Dict[str, Any]]) -> Dict[str, List[str]]:
    pool: Dict[str, set[str]] = {}
    for state in states:
        for path, value in walk_leaves(state):
            if not isinstance(value, str):
                continue
            if value_kind(value) != "str":
                continue
            if not is_enum_mutation_path(path):
                continue
            canonical = normalize_path(path).lower()
            text = value.strip()
            if text:
                pool.setdefault(canonical, set()).add(text)

    wavetable_pool = discover_wavetable_name_pool()
    system_hints = discover_system_preset_hints()
    hinted_waveforms = system_hints.get("analog_waveforms", ())

    keys = list(pool.keys())
    for key in keys:
        if key.endswith(".analog.waveform"):
            pool[key].update(DEFAULT_ANALOG_WAVEFORMS)
            pool[key].update(hinted_waveforms)
        if wavetable_pool and key.endswith(".wavetable.wavetable_name"):
            pool[key].update(wavetable_pool)

    # Fallback keys used when a specific canonical key is not present.
    pool.setdefault(".analog.waveform", set()).update(DEFAULT_ANALOG_WAVEFORMS)
    pool.setdefault(".analog.waveform", set()).update(hinted_waveforms)
    if wavetable_pool:
        pool.setdefault(".wavetable.wavetable_name", set()).update(wavetable_pool)

    return {path: sorted(values) for path, values in pool.items() if values}


def enum_options_for_path(path: str, enum_pool: Dict[str, List[str]]) -> List[str]:
    canonical = normalize_path(path).lower()
    options = list(enum_pool.get(canonical, []))
    if options:
        return options
    for suffix in ENUM_MUTATION_SUFFIXES:
        if canonical.endswith(suffix):
            return list(enum_pool.get(suffix, []))
    return []


def choose_enum_replacement(
    path: str,
    current_value: str,
    options: Sequence[str],
    rng: random.Random,
) -> str | None:
    candidates = [item for item in options if item != current_value]
    if not candidates:
        return None
    if is_wavetable_name_path(path):
        preferred = discover_system_preset_hints().get("preferred_wavetables", ())
        preferred_candidates = [name for name in preferred if name in candidates]
        if preferred_candidates and rng.random() < 0.75:
            return rng.choice(preferred_candidates)
    return rng.choice(candidates)


def build_reference_model(states: Sequence[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    numeric_values: Dict[str, List[float]] = {}
    bool_counts: Dict[str, Dict[str, int]] = {}
    for state in states:
        for path, value in walk_leaves(state):
            if not isinstance(value, str):
                continue
            if not is_mutation_path(path) and not is_enum_mutation_path(path):
                continue
            canonical = normalize_path(path).lower()
            kind = value_kind(value)
            if kind == "num" and (not INT_RE.fullmatch(value) or is_wavetable_frame_path(path)):
                try:
                    numeric = float(value)
                except ValueError:
                    continue
                if math.isfinite(numeric):
                    numeric_values.setdefault(canonical, []).append(numeric)
            elif kind == "bool":
                counts = bool_counts.setdefault(canonical, {"true": 0, "false": 0})
                counts[value] += 1

    bool_probability: Dict[str, float] = {}
    for canonical, counts in bool_counts.items():
        total = counts["true"] + counts["false"]
        if total > 0:
            bool_probability[canonical] = counts["true"] / total
    return {"numeric": numeric_values, "bool_probability": bool_probability}


def collect_snapin_instance_ids(state: Dict[str, Any]) -> List[str]:
    ids: List[str] = []
    model = state.get("model")
    if not isinstance(model, dict):
        return ids
    lanes = model.get("lanes")
    if not isinstance(lanes, list):
        return ids

    for lane in lanes:
        if not isinstance(lane, dict):
            continue
        snapins_holder = lane.get("snapins")
        if not isinstance(snapins_holder, dict):
            continue
        snapins = snapins_holder.get("snapins")
        if not isinstance(snapins, list):
            continue
        for snapin in snapins:
            if not isinstance(snapin, dict):
                continue
            instance_id = snapin.get("instance_id")
            if isinstance(instance_id, str) and instance_id.isdigit():
                ids.append(instance_id)
    return ids


def parse_lane_index(target: str) -> int | None:
    if not isinstance(target, str):
        return None
    if not target.startswith("lane_"):
        return None
    suffix = target.split("_", 1)[1]
    if not suffix.isdigit():
        return None
    return int(suffix)


def _effective_voice_lane_limit(model: Dict[str, Any], lanes: Sequence[Any]) -> int:
    lane_count = len(lanes)
    if lane_count <= 0:
        return 0
    raw = model.get("voice_lane_count")
    if isinstance(raw, str) and raw.isdigit():
        limit = int(raw)
    elif isinstance(raw, (int, float)):
        limit = int(raw)
    else:
        limit = lane_count
    if limit <= 0:
        return lane_count
    return max(1, min(lane_count, limit))


def module_is_audible(module: Dict[str, Any], lanes: Sequence[Any], valid_lane_count: int | None = None) -> bool:
    module_type = module.get("type")
    if not isinstance(module_type, str) or module_type not in SOUND_GENERATOR_TYPES:
        return False
    if not parse_boolish(module.get("enabled"), default=True):
        return False

    output = module.get("output")
    if not isinstance(output, dict):
        return False
    if not parse_boolish(output.get("send_enabled"), default=True):
        return False
    if parse_floatish(output.get("gain"), default=0.0) <= 0.001:
        return False

    target = output.get("target")
    if not isinstance(target, str):
        return False
    lane_idx = parse_lane_index(target)
    if lane_idx is None:
        # Non-lane targets are assumed valid.
        return True
    lane_limit = valid_lane_count if isinstance(valid_lane_count, int) and valid_lane_count > 0 else len(lanes)
    if lane_idx < 0 or lane_idx >= lane_limit:
        return False

    lane = lanes[lane_idx]
    if not isinstance(lane, dict):
        return False
    if not parse_boolish(lane.get("enabled"), default=True):
        return False
    if parse_boolish(lane.get("mute"), default=False):
        return False
    if parse_floatish(lane.get("gain"), default=1.0) <= 0.001:
        return False
    if parse_floatish(lane.get("mix"), default=1.0) <= 0.001:
        return False
    return True


def _lane_snapin_count(lane: Dict[str, Any]) -> int:
    snapins_holder = lane.get("snapins")
    if not isinstance(snapins_holder, dict):
        return 0
    snapins = snapins_holder.get("snapins")
    if not isinstance(snapins, list):
        return 0
    return sum(1 for item in snapins if isinstance(item, dict))


def _ensure_lane_audible_defaults(lane: Dict[str, Any], min_gain: float = 0.8, min_mix: float = 0.8) -> int:
    repairs = 0
    if lane.get("enabled") != "true":
        lane["enabled"] = "true"
        repairs += 1
    if lane.get("mute") != "false":
        lane["mute"] = "false"
        repairs += 1
    if lane.get("solo") != "false":
        lane["solo"] = "false"
        repairs += 1
    if parse_floatish(lane.get("gain"), default=0.0) < min_gain:
        lane["gain"] = _format_decimal(min_gain)
        repairs += 1
    if parse_floatish(lane.get("mix"), default=0.0) < min_mix:
        lane["mix"] = _format_decimal(min_mix)
        repairs += 1
    if not isinstance(lane.get("snapins"), dict):
        lane["snapins"] = {"$type": "snapin_list", "snapins": []}
        repairs += 1
    elif not isinstance(lane["snapins"].get("snapins"), list):
        lane["snapins"]["snapins"] = []
        repairs += 1
    return repairs


def _set_output_target(module: Dict[str, Any], lane_idx: int, gain_floor: float = 0.25) -> int:
    repairs = 0
    output = module.get("output")
    if not isinstance(output, dict):
        output = {"$type": "output_module"}
        module["output"] = output
        repairs += 1
    wanted_target = f"lane_{lane_idx}"
    if output.get("target") != wanted_target:
        output["target"] = wanted_target
        repairs += 1
    if output.get("send_enabled") != "true":
        output["send_enabled"] = "true"
        repairs += 1
    if parse_floatish(output.get("gain"), default=0.0) < gain_floor:
        output["gain"] = _format_decimal(gain_floor)
        repairs += 1
    return repairs


def ensure_generator_routing_integrity(state: Dict[str, Any], gain_floor: float = 0.20) -> int:
    model = state.get("model")
    if not isinstance(model, dict):
        return 0
    voice = model.get("voice")
    lanes = model.get("lanes")
    if not isinstance(voice, dict) or not isinstance(lanes, list):
        return 0
    modules = voice.get("modules")
    if not isinstance(modules, list):
        return 0

    repairs = 0
    if not lanes:
        lanes.append(
            {
                "$type": "lane",
                "enabled": "true",
                "gain": "1",
                "mix": "1",
                "mute": "false",
                "solo": "false",
                "snapins": {"$type": "snapin_list", "snapins": []},
            }
        )
        repairs += 1
    if isinstance(lanes[0], dict):
        repairs += _ensure_lane_audible_defaults(lanes[0], min_gain=0.8, min_mix=0.8)

    voice_lane_limit = _effective_voice_lane_limit(model, lanes)
    fallback_lane_idx = 0
    for idx, lane in enumerate(lanes[:voice_lane_limit]):
        if not isinstance(lane, dict):
            continue
        if not parse_boolish(lane.get("enabled"), default=True):
            continue
        if parse_boolish(lane.get("mute"), default=False):
            continue
        if parse_floatish(lane.get("gain"), default=1.0) <= 0.01:
            continue
        if parse_floatish(lane.get("mix"), default=1.0) <= 0.01:
            continue
        fallback_lane_idx = idx
        break

    for module in modules:
        if not isinstance(module, dict):
            continue
        if module.get("type") not in SOUND_GENERATOR_TYPES:
            continue
        if module.get("enabled") != "true":
            module["enabled"] = "true"
            repairs += 1

        output = module.get("output")
        if not isinstance(output, dict):
            repairs += _set_output_target(module, fallback_lane_idx, gain_floor=gain_floor)
        else:
            target = output.get("target")
            lane_idx = parse_lane_index(target) if isinstance(target, str) else None
            if lane_idx is None or lane_idx < 0 or lane_idx >= voice_lane_limit:
                repairs += _set_output_target(module, fallback_lane_idx, gain_floor=gain_floor)
            else:
                if output.get("send_enabled") != "true":
                    output["send_enabled"] = "true"
                    repairs += 1
                if parse_floatish(output.get("gain"), default=0.0) < gain_floor:
                    output["gain"] = _format_decimal(gain_floor)
                    repairs += 1

        mix = module.get("mix")
        if isinstance(mix, dict) and parse_floatish(mix.get("gain"), default=0.0) < 0.12:
            mix["gain"] = "0.25"
            repairs += 1
    return repairs


def _repair_filter_model_values(model_obj: Dict[str, Any]) -> int:
    repairs = 0
    model_type = str(model_obj.get("$type", "")).lower()
    filter_kind = str(model_obj.get("type", "")).lower()

    def set_clamped(key: str, low: float, high: float, preferred_if_oob: float | None = None) -> None:
        nonlocal repairs
        raw = model_obj.get(key)
        if not isinstance(raw, str) or not NUMERIC_RE.fullmatch(raw):
            return
        try:
            value = float(raw)
        except ValueError:
            return
        new_value = value
        if value < low or value > high:
            if preferred_if_oob is not None:
                new_value = preferred_if_oob
            else:
                new_value = max(low, min(high, value))
        if new_value != value:
            model_obj[key] = _format_decimal(new_value)
            repairs += 1

    if "cutoff" in model_obj:
        cutoff_preferred = None
        if "high" in filter_kind:
            cutoff_preferred = 1200.0
        elif "low" in filter_kind:
            cutoff_preferred = 800.0
        elif "band" in filter_kind:
            cutoff_preferred = 900.0
        set_clamped("cutoff", 35.0, 18000.0, preferred_if_oob=cutoff_preferred)

    if "q" in model_obj:
        set_clamped("q", 0.1, 12.0)
    if "resonance" in model_obj:
        set_clamped("resonance", 0.0, 0.97)
    if "gain" in model_obj and model_type in ("filter", "filter_fx", "ladder"):
        set_clamped("gain", 0.05, 8.0, preferred_if_oob=1.0)
    if "drive" in model_obj:
        set_clamped("drive", 0.0, 8.0, preferred_if_oob=1.0)
    if "mix" in model_obj:
        set_clamped("mix", 0.0, 1.0)
    return repairs


def ensure_filter_audibility_ranges(state: Dict[str, Any]) -> int:
    model = state.get("model")
    if not isinstance(model, dict):
        return 0
    repairs = 0

    modules = model.get("voice", {}).get("modules")
    if isinstance(modules, list):
        for module in modules:
            if not isinstance(module, dict):
                continue
            filt = module.get("filter")
            if isinstance(filt, dict):
                repairs += _repair_filter_model_values(filt)

    lanes = model.get("lanes")
    if isinstance(lanes, list):
        for lane in lanes:
            if not isinstance(lane, dict):
                continue
            holder = lane.get("snapins")
            if not isinstance(holder, dict):
                continue
            snapins = holder.get("snapins")
            if not isinstance(snapins, list):
                continue
            for snapin in snapins:
                if not isinstance(snapin, dict):
                    continue
                snapin_state = snapin.get("state")
                if not isinstance(snapin_state, dict):
                    continue
                snapin_model = snapin_state.get("model")
                if not isinstance(snapin_model, dict):
                    continue
                stype = str(snapin_model.get("$type", "")).lower()
                if stype in ("filter", "ladder", "ladder_filter"):
                    repairs += _repair_filter_model_values(snapin_model)
    return repairs


def ensure_random_audible_anchor(state: Dict[str, Any]) -> int:
    model = state.get("model")
    if not isinstance(model, dict):
        return 0
    voice = model.get("voice")
    lanes = model.get("lanes")
    if not isinstance(voice, dict) or not isinstance(lanes, list):
        return 0
    modules = voice.get("modules")
    if not isinstance(modules, list):
        return 0

    repairs = 0
    generators = [m for m in modules if isinstance(m, dict) and m.get("type") in SOUND_GENERATOR_TYPES]
    if not generators:
        return repairs
    voice_lane_limit = _effective_voice_lane_limit(model, lanes)
    if voice_lane_limit <= 0:
        return repairs

    # If at least one generator is already routed to a dry lane, keep routing as-is.
    for module in generators:
        output = module.get("output")
        if not isinstance(output, dict):
            continue
        target = output.get("target")
        lane_idx = parse_lane_index(target) if isinstance(target, str) else None
        if lane_idx is None or lane_idx < 0 or lane_idx >= voice_lane_limit:
            continue
        lane = lanes[lane_idx]
        if not isinstance(lane, dict):
            continue
        if _lane_snapin_count(lane) == 0 and module_is_audible(module, lanes, valid_lane_count=voice_lane_limit):
            return repairs

    dry_lane_idx: int | None = None
    for idx, lane in enumerate(lanes[:voice_lane_limit]):
        if not isinstance(lane, dict):
            continue
        if _lane_snapin_count(lane) == 0:
            dry_lane_idx = idx
            break

    if dry_lane_idx is None:
        # If voice lane count is explicitly limited, force anchor into lane_0.
        # Extra lanes beyond that limit are not guaranteed to receive voice module output.
        if voice_lane_limit < len(lanes):
            dry_lane_idx = 0
        else:
            lanes.append(
                {
                    "$type": "lane",
                    "enabled": "true",
                    "gain": "1",
                    "mix": "1",
                    "mute": "false",
                    "solo": "false",
                    "snapins": {"$type": "snapin_list", "snapins": []},
                }
            )
            dry_lane_idx = len(lanes) - 1
            repairs += 1

    dry_lane = lanes[dry_lane_idx]
    if isinstance(dry_lane, dict):
        repairs += _ensure_lane_audible_defaults(dry_lane, min_gain=0.9, min_mix=0.9)

    # Route one generator to the dry lane as a guaranteed audible anchor.
    anchor_module = generators[0]
    repairs += _set_output_target(anchor_module, dry_lane_idx, gain_floor=0.25)

    mix = anchor_module.get("mix")
    if isinstance(mix, dict) and parse_floatish(mix.get("gain"), default=0.0) < 0.2:
        mix["gain"] = "0.3"
        repairs += 1
    return repairs


def apply_random_audibility_safety(state: Dict[str, Any]) -> int:
    repairs = 0
    repairs += ensure_generator_routing_integrity(state, gain_floor=0.20)
    repairs += ensure_filter_audibility_ranges(state)
    repairs += ensure_random_audible_anchor(state)
    repairs += ensure_audible_signal_path(state)
    return repairs


def ensure_audible_signal_path(state: Dict[str, Any]) -> int:
    model = state.get("model")
    if not isinstance(model, dict):
        return 0
    voice = model.get("voice")
    if not isinstance(voice, dict):
        return 0
    modules = voice.get("modules")
    lanes = model.get("lanes")
    if not isinstance(modules, list) or not isinstance(lanes, list):
        return 0

    voice_lane_limit = _effective_voice_lane_limit(model, lanes)
    if any(module_is_audible(m, lanes, valid_lane_count=voice_lane_limit) for m in modules if isinstance(m, dict)):
        return 0

    repairs = 0
    lane0: Dict[str, Any] | None = None
    if lanes and isinstance(lanes[0], dict):
        lane0 = lanes[0]
    else:
        for lane in lanes:
            if isinstance(lane, dict):
                lane0 = lane
                break
    if lane0 is None:
        lane0 = {
            "$type": "lane",
            "enabled": "true",
            "gain": "1",
            "mix": "1",
            "mute": "false",
            "solo": "false",
            "snapins": {"$type": "snapin_list", "snapins": []},
        }
        lanes.insert(0, lane0)
        repairs += 1

    if lane0.get("enabled") != "true":
        lane0["enabled"] = "true"
        repairs += 1
    if lane0.get("mute") != "false":
        lane0["mute"] = "false"
        repairs += 1
    if lane0.get("solo") != "false":
        lane0["solo"] = "false"
        repairs += 1
    if parse_floatish(lane0.get("gain"), default=0.0) <= 0.001:
        lane0["gain"] = "1"
        repairs += 1
    if parse_floatish(lane0.get("mix"), default=0.0) <= 0.001:
        lane0["mix"] = "1"
        repairs += 1

    candidate_module: Dict[str, Any] | None = None
    for module in modules:
        if not isinstance(module, dict):
            continue
        module_type = module.get("type")
        if isinstance(module_type, str) and module_type in SOUND_GENERATOR_TYPES:
            candidate_module = module
            break
    if candidate_module is None:
        for module in modules:
            if not isinstance(module, dict):
                continue
            if module.get("type") == "none":
                candidate_module = module
                break
    if candidate_module is None:
        return repairs

    if candidate_module.get("type") == "none":
        candidate_module["type"] = "analog_generator"
        repairs += 1
    if candidate_module.get("enabled") != "true":
        candidate_module["enabled"] = "true"
        repairs += 1

    analog = candidate_module.get("analog")
    if not isinstance(analog, dict):
        analog = {"$type": "analog_generator"}
        candidate_module["analog"] = analog
        repairs += 1
    if analog.get("waveform") not in DEFAULT_ANALOG_WAVEFORMS:
        analog["waveform"] = "saw"
        repairs += 1

    output = candidate_module.get("output")
    if not isinstance(output, dict):
        output = {"$type": "output_module"}
        candidate_module["output"] = output
        repairs += 1
    if output.get("target") != "lane_0":
        output["target"] = "lane_0"
        repairs += 1
    if output.get("send_enabled") != "true":
        output["send_enabled"] = "true"
        repairs += 1
    if parse_floatish(output.get("gain"), default=0.0) <= 0.001:
        output["gain"] = "0.25"
        repairs += 1

    return repairs


def ensure_wavetable_generators_have_content(state: Dict[str, Any]) -> int:
    model = state.get("model")
    if not isinstance(model, dict):
        return 0
    voice = model.get("voice")
    if not isinstance(voice, dict):
        return 0
    modules = voice.get("modules")
    if not isinstance(modules, list):
        return 0

    wt_names = discover_wavetable_name_pool()
    preferred = discover_system_preset_hints().get("preferred_wavetables", ())
    default_wt = preferred[0] if preferred else (wt_names[0] if wt_names else "")
    repairs = 0

    for module in modules:
        if not isinstance(module, dict):
            continue
        if module.get("type") != "wavetable_generator":
            continue
        if not parse_boolish(module.get("enabled"), default=True):
            continue

        wavetable = module.get("wavetable")
        if not isinstance(wavetable, dict):
            continue
        if not isinstance(wavetable.get("wavetable_name"), str) or not wavetable.get("wavetable_name", "").strip():
            if default_wt:
                wavetable["wavetable_name"] = default_wt
                repairs += 1
        frame = parse_floatish(wavetable.get("frame"), default=-1.0)
        if frame < 0.0:
            wavetable["frame"] = "0"
            repairs += 1
        elif frame > SAFE_WAVETABLE_FRAME_MAX:
            wavetable["frame"] = _format_decimal(SAFE_WAVETABLE_FRAME_MAX)
            repairs += 1

    return repairs


def find_unresolved_snapin_targets(state: Dict[str, Any]) -> List[Tuple[int, str]]:
    model = state.get("model")
    if not isinstance(model, dict):
        return []
    globals_obj = model.get("globals")
    if not isinstance(globals_obj, dict):
        return []
    modulations = globals_obj.get("modulations")
    if not isinstance(modulations, list):
        return []

    valid_ids = set(collect_snapin_instance_ids(state))
    unresolved: List[Tuple[int, str]] = []
    for idx, modulation in enumerate(modulations):
        if not isinstance(modulation, dict):
            continue
        target = modulation.get("target")
        if not isinstance(target, dict):
            continue
        module = target.get("module")
        if isinstance(module, str) and module.isdigit() and module not in valid_ids:
            unresolved.append((idx, module))
    return unresolved


def _modulation_signature(modulation: Dict[str, Any]) -> str | None:
    target = modulation.get("target")
    if not isinstance(target, dict):
        return None
    path = target.get("path")
    if not isinstance(path, dict):
        return None
    entries = path.get("entries")
    if not isinstance(entries, list):
        return None
    module = str(target.get("module", ""))
    serialized_entries = "/".join(str(entry) for entry in entries)
    return f"{module}|{serialized_entries}"


def _is_master_pitch_modulation(modulation: Dict[str, Any]) -> bool:
    target = modulation.get("target")
    if not isinstance(target, dict):
        return False
    module = str(target.get("module", "")).lower()
    path = target.get("path")
    if not isinstance(path, dict):
        return False
    entries = path.get("entries")
    if not isinstance(entries, list):
        return False
    lower_entries = [str(entry).lower() for entry in entries]
    has_pitch = any("pitch" in entry for entry in lower_entries)
    is_master = module == "master" or any("master" in entry for entry in lower_entries)
    return has_pitch and is_master


def capture_master_pitch_modulation_signatures(state: Dict[str, Any]) -> Tuple[str, ...]:
    modulations = state.get("model", {}).get("globals", {}).get("modulations")
    if not isinstance(modulations, list):
        return ()
    signatures: List[str] = []
    for modulation in modulations:
        if not isinstance(modulation, dict):
            continue
        if not _is_master_pitch_modulation(modulation):
            continue
        signature = _modulation_signature(modulation)
        if signature:
            signatures.append(signature)
    return tuple(sorted(set(signatures)))


def remove_new_master_pitch_modulations(state: Dict[str, Any], allowed_signatures: Sequence[str]) -> int:
    model = state.get("model")
    if not isinstance(model, dict):
        return 0
    globals_obj = model.get("globals")
    if not isinstance(globals_obj, dict):
        return 0
    modulations = globals_obj.get("modulations")
    if not isinstance(modulations, list):
        return 0

    allowed = set(allowed_signatures)
    kept: List[Any] = []
    removed = 0
    for modulation in modulations:
        if not isinstance(modulation, dict):
            kept.append(modulation)
            continue
        if _is_master_pitch_modulation(modulation):
            signature = _modulation_signature(modulation)
            if signature not in allowed:
                removed += 1
                continue
        kept.append(modulation)

    if removed:
        globals_obj["modulations"] = kept
    return removed


def state_integrity_issues(state: Dict[str, Any]) -> List[str]:
    issues: List[str] = []
    ids = collect_snapin_instance_ids(state)
    if len(ids) != len(set(ids)):
        issues.append("duplicate snapin instance_id values detected")

    unresolved = find_unresolved_snapin_targets(state)
    if unresolved:
        samples = ", ".join(f"mod[{idx}]=>{module}" for idx, module in unresolved[:6])
        more = f" (+{len(unresolved) - 6} more)" if len(unresolved) > 6 else ""
        issues.append(f"unresolved snapin target module ids: {samples}{more}")
    return issues


def remove_malformed_modulations(state: Dict[str, Any]) -> int:
    model = state.get("model")
    if not isinstance(model, dict):
        return 0
    globals_obj = model.get("globals")
    if not isinstance(globals_obj, dict):
        return 0
    modulations = globals_obj.get("modulations")
    if not isinstance(modulations, list):
        return 0

    kept: List[Any] = []
    removed = 0
    for modulation in modulations:
        if not isinstance(modulation, dict):
            removed += 1
            continue

        target = modulation.get("target")
        source = modulation.get("source")
        if not isinstance(target, dict) or not isinstance(source, dict):
            removed += 1
            continue

        path = target.get("path")
        src_path = source.get("path")
        if not isinstance(path, dict) or not isinstance(src_path, dict):
            removed += 1
            continue

        entries = path.get("entries")
        src_entries = src_path.get("entries")
        if not isinstance(entries, list) or not entries:
            removed += 1
            continue
        if not isinstance(src_entries, list) or not src_entries:
            removed += 1
            continue

        kept.append(modulation)

    if removed:
        globals_obj["modulations"] = kept
    return removed


def apply_output_audibility_safety(state: Dict[str, Any]) -> int:
    repairs = 0
    repairs += remove_malformed_modulations(state)
    repairs += ensure_filter_audibility_ranges(state)
    repairs += ensure_generator_routing_integrity(state, gain_floor=0.20)
    repairs += ensure_random_audible_anchor(state)
    repairs += ensure_audible_signal_path(state)
    return repairs


def sanitize_state_for_output(state: Dict[str, Any]) -> int:
    model = state.get("model")
    if not isinstance(model, dict):
        return 0

    repaired = 0
    lanes = model.get("lanes")
    if isinstance(lanes, list):
        for lane in lanes:
            if not isinstance(lane, dict):
                continue
            for key in ("mute", "solo"):
                value = lane.get(key)
                if isinstance(value, str) and value != "false":
                    lane[key] = "false"
                    repaired += 1

    globals_obj = model.get("globals")
    if not isinstance(globals_obj, dict):
        return repaired
    modulations = globals_obj.get("modulations")
    if not isinstance(modulations, list):
        return repaired

    valid_ids = sorted(set(collect_snapin_instance_ids(state)), key=int)
    fallback_id = valid_ids[0] if valid_ids else None
    for modulation in modulations:
        if not isinstance(modulation, dict):
            continue
        target = modulation.get("target")
        if not isinstance(target, dict):
            continue
        module = target.get("module")
        if isinstance(module, str) and module.isdigit() and module not in valid_ids:
            # Keep modulation plugged by remapping invalid module refs to a valid snapin id.
            target["module"] = fallback_id if fallback_id is not None else "local"
            repaired += 1

    repaired += ensure_wavetable_generators_have_content(state)
    repaired += ensure_audible_signal_path(state)
    return repaired


def clamp_numeric(path: str, value: float) -> float:
    low = path.lower()
    if any(tag in low for tag in ("mix", "blend", "pan", "spread", "random_", "phase", "position", "gain")):
        return max(-1.0, min(1.0, value)) if "pan" in low else max(0.0, min(1.0, value))
    if any(tag in low for tag in ("enabled", "mute", "bypass")):
        return 1.0 if value >= 0.5 else 0.0
    if "voices" in low:
        return float(max(1, min(16, int(round(value)))))
    if "octave" in low:
        return float(max(-4, min(4, int(round(value)))))
    return value


def _format_decimal(value: float) -> str:
    text = f"{value:.10f}".rstrip("0").rstrip(".")
    if text in ("", "-0"):
        return "0"
    return text


def mutate_curve_segments(state: Dict[str, Any], rng: random.Random, amount: float) -> int:
    changes = 0
    modulations = state.get("model", {}).get("modulators")
    if not isinstance(modulations, list):
        return changes

    for mod in modulations:
        if not isinstance(mod, dict):
            continue
        lfo = mod.get("lfo")
        if not isinstance(lfo, dict):
            continue
        curve = lfo.get("curve")
        if not isinstance(curve, dict):
            continue
        data = curve.get("data")
        if not isinstance(data, dict):
            continue
        segments = data.get("segments")
        if not isinstance(segments, list):
            continue
        if len(segments) < 2:
            continue

        for idx, segment in enumerate(segments):
            if not isinstance(segment, dict):
                continue
            x_raw = segment.get("x")
            y_raw = segment.get("y")
            if not isinstance(x_raw, str) or not isinstance(y_raw, str):
                continue
            try:
                x = float(x_raw)
                y = float(y_raw)
            except ValueError:
                continue

            y_changed = False
            if rng.random() < (0.85 * amount):
                y += (rng.random() * 2.0 - 1.0) * (0.7 * amount)
                y = max(-1.0, min(1.0, y))
                segment["y"] = _format_decimal(y)
                y_changed = segment["y"] != y_raw
                if y_changed:
                    changes += 1

            # Keep endpoints fixed in time; jitter interior x positions.
            if 0 < idx < len(segments) - 1 and rng.random() < (0.5 * amount):
                x += (rng.random() * 2.0 - 1.0) * (0.15 * amount)
                x = max(0.0, min(1.0, x))
                prev_x = float(segments[idx - 1].get("x", "0"))
                next_x = float(segments[idx + 1].get("x", "1"))
                x = max(prev_x + 0.001, min(next_x - 0.001, x))
                new_x = _format_decimal(x)
                if new_x != x_raw:
                    segment["x"] = new_x
                    changes += 1

            if y_changed:
                # Keep envelope segment type values as-is; only coordinates are adjusted.
                pass
    return changes


def mutate_string_value(path: str, value: str, rng: random.Random, amount: float, enum_pool: Dict[str, List[str]]) -> str:
    kind = value_kind(value)
    if kind == "bool":
        if rng.random() < 0.35 * amount:
            return "false" if value == "true" else "true"
        return value

    if kind == "num":
        try:
            n = float(value)
        except ValueError:
            return value

        if is_wavetable_frame_path(path):
            chance = min(1.0, 0.40 + 0.70 * amount)
            if rng.random() > chance:
                return value
            if rng.random() < (0.45 + 0.35 * amount):
                mutated = rng.uniform(0.0, SAFE_WAVETABLE_FRAME_MAX)
            else:
                span = 12.0 + 180.0 * amount
                mutated = n + (rng.random() * 2.0 - 1.0) * span
            mutated = max(0.0, min(SAFE_WAVETABLE_FRAME_MAX, mutated))
            return _format_decimal(mutated)

        # Integer-coded fields are frequently enum indexes or mode values.
        # Keeping them unchanged is safer than mutating into invalid states.
        if INT_RE.fullmatch(value):
            if not is_sync_path(path):
                return value
            canonical = normalize_path(path).lower()
            current_int = int(round(n))
            if canonical.endswith(".analog.sync"):
                if current_int in (0, 1):
                    return "0" if current_int == 1 else "1"
                current_int = max(0, min(2, current_int + rng.choice((-1, 1))))
                return str(current_int)
            if canonical.endswith("rate_numerator"):
                choices = [1, 2, 3, 4, 6, 8, 12, 16]
                return str(rng.choice(choices))
            if canonical.endswith(".length"):
                span = 1 + int(round(3 * amount))
                current_int += rng.randint(-span, span)
                current_int = max(1, min(16, current_int))
                return str(current_int)
            if canonical.endswith(".rate"):
                span = 1 + int(round(4 * amount))
                current_int += rng.randint(-span, span)
                current_int = max(0, min(16, current_int))
                return str(current_int)
            span = 1 + int(round(2 * amount))
            current_int += rng.randint(-span, span)
            current_int = max(0, min(32, current_int))
            return str(current_int)

        magnitude = max(0.0001, abs(n), 0.25)
        delta = (rng.random() * 2.0 - 1.0) * magnitude * (0.35 * amount)
        mutated = clamp_numeric(path, n + delta)
        if 0.0 <= n <= 1.0:
            mutated = max(0.0, min(1.0, mutated))

        # Preserve non-negative domains for values that started non-negative.
        if n >= 0:
            mutated = max(0.0, mutated)
        if not math.isfinite(mutated):
            return value
        return _format_decimal(mutated)

    if kind == "str" and is_enum_mutation_path(path):
        options = enum_options_for_path(path, enum_pool)
        chance = 0.45 * amount
        canonical = normalize_path(path).lower()
        if canonical.endswith(".analog.waveform") or canonical.endswith(".wavetable.wavetable_name"):
            chance = max(chance, 0.35 + 0.35 * amount)
        if options and rng.random() < chance:
            replacement = choose_enum_replacement(path, value, options, rng)
            if replacement is not None:
                return replacement

    # Free-form strings are skipped unless explicitly whitelisted enum paths.
    return value


def mutate_state(
    state: Dict[str, Any],
    rng: random.Random,
    amount: float,
    enum_pool: Dict[str, List[str]],
    preserve_pitch_low: bool = False,
    pitch_protect_threshold: float = 0.30,
    preserve_similarity: float = 0.0,
) -> int:
    changed = 0
    preserve = max(0.0, min(1.0, preserve_similarity))
    mutation_chance = min(1.0, amount * 1.3) * (1.0 - 0.55 * preserve)
    for path, value in list(walk_leaves(state)):
        if not isinstance(value, str):
            continue
        if not is_mutation_path(path):
            continue
        if preserve_pitch_low and amount <= pitch_protect_threshold and is_pitch_path(path):
            continue
        path_chance = mutation_chance
        if is_wavetable_frame_path(path):
            path_chance = max(path_chance, min(1.0, 0.55 + amount))
        if is_sync_path(path):
            sync_floor = (0.22 + 0.35 * amount) * (1.0 - 0.35 * preserve)
            path_chance = max(path_chance, min(1.0, sync_floor))
        canonical = normalize_path(path).lower()
        if canonical.endswith(".analog.waveform") or canonical.endswith(".wavetable.wavetable_name"):
            path_chance = max(path_chance, 0.35 + 0.20 * (1.0 - 0.30 * preserve))
        if rng.random() > path_chance:
            continue
        new_value = mutate_string_value(path, value, rng, amount, enum_pool)
        if new_value != value and set_by_path(state, path, new_value):
            changed += 1
    curve_rounds = 1 + (1 if amount > 0.6 else 0)
    for _ in range(curve_rounds):
        changed += mutate_curve_segments(state, rng, amount)
    return changed


def crossover_states(
    base: Dict[str, Any],
    donors: Sequence[Dict[str, Any]],
    rng: random.Random,
    mix_rate: float,
    enum_pool: Dict[str, List[str]],
) -> int:
    changed = 0
    if not donors:
        return changed

    donor_paths: Dict[str, List[Any]] = {}
    for donor in donors:
        for path, value in walk_leaves(donor):
            if not isinstance(value, str):
                continue
            donor_paths.setdefault(path, []).append(value)

    for path, base_value in list(walk_leaves(base)):
        if not isinstance(base_value, str):
            continue
        if not is_mutation_path(path):
            continue
        base_kind = value_kind(base_value)
        if base_kind not in ("bool", "num", "str"):
            continue
        if rng.random() > mix_rate:
            continue
        values = donor_paths.get(path)
        if not values:
            continue

        chosen = rng.choice(values)
        if value_kind(chosen) != base_kind:
            continue
        # Avoid swapping integer-coded mode/index values.
        if base_kind == "num" and INT_RE.fullmatch(base_value) and not is_wavetable_frame_path(path):
            continue
        if base_kind == "str":
            if not is_enum_mutation_path(path):
                continue
            allowed = set(enum_options_for_path(path, enum_pool))
            if not allowed:
                continue
            if chosen not in allowed:
                continue
        if is_wavetable_frame_path(path) and chosen == base_value:
            chosen = mutate_string_value(path, base_value, rng, max(0.35, mix_rate), enum_pool)
        if chosen != base_value and set_by_path(base, path, chosen):
            changed += 1

    return changed


def synthesize_random_state(
    template_state: Dict[str, Any],
    reference_model: Dict[str, Dict[str, Any]],
    enum_pool: Dict[str, List[str]],
    rng: random.Random,
    complexity: float,
) -> Tuple[Dict[str, Any], int]:
    state = copy.deepcopy(template_state)
    changed = 0
    complexity = max(0.0, min(1.0, complexity))
    coverage = 0.15 + (0.85 * complexity)
    numeric_model = reference_model.get("numeric", {})
    bool_model = reference_model.get("bool_probability", {})

    for path, value in list(walk_leaves(state)):
        if not isinstance(value, str):
            continue
        if not is_mutation_path(path):
            continue
        local_coverage = coverage
        if is_wavetable_frame_path(path):
            local_coverage = max(local_coverage, 0.70 + 0.25 * complexity)
        if rng.random() > local_coverage:
            continue

        canonical = normalize_path(path).lower()
        kind = value_kind(value)

        if kind == "num" and (not INT_RE.fullmatch(value) or is_wavetable_frame_path(path)):
            try:
                original = float(value)
            except ValueError:
                continue
            source_values = numeric_model.get(canonical, [])
            if source_values:
                picked = rng.choice(source_values)
                lo = min(source_values)
                hi = max(source_values)
                span = max(hi - lo, abs(picked) * 0.2, 0.01)
                if is_wavetable_frame_path(path):
                    span = max(span, 24.0 + 128.0 * complexity)
                picked += (rng.random() * 2.0 - 1.0) * span * (0.15 + 0.35 * complexity)
                if is_wavetable_frame_path(path):
                    picked = max(0.0, min(SAFE_WAVETABLE_FRAME_MAX, picked))
                picked = clamp_numeric(path, picked)
                if 0.0 <= original <= 1.0:
                    picked = max(0.0, min(1.0, picked))
                if original >= 0:
                    picked = max(0.0, picked)
                if math.isfinite(picked):
                    replacement = _format_decimal(picked)
                    if replacement != value and set_by_path(state, path, replacement):
                        changed += 1
                    continue

        if kind == "bool":
            p_true = bool_model.get(canonical, 0.5)
            if rng.random() < (0.35 + 0.55 * complexity):
                replacement = "true" if rng.random() < p_true else "false"
                if replacement != value and set_by_path(state, path, replacement):
                    changed += 1
            continue

        if kind == "str" and is_enum_mutation_path(path):
            options = enum_options_for_path(path, enum_pool)
            if options and rng.random() < (0.30 + 0.60 * complexity):
                replacement = choose_enum_replacement(path, value, options, rng)
                if replacement is None:
                    continue
                if set_by_path(state, path, replacement):
                    changed += 1
            continue

        # Fallback for paths not covered by the model pools.
        replacement = mutate_string_value(path, value, rng, 0.25 + 0.75 * complexity, enum_pool)
        if replacement != value and set_by_path(state, path, replacement):
            changed += 1

    for _ in range(1 + int(complexity * 3)):
        changed += mutate_curve_segments(state, rng, 0.25 + 0.75 * complexity)
    return state, changed


def merge_assets(presets: Sequence[Preset]) -> Tuple[Dict[str, bytes], List[str]]:
    merged: Dict[str, bytes] = {}
    warnings: List[str] = []

    for preset in presets:
        for name, blob in preset.assets.items():
            if name not in merged:
                merged[name] = blob
                continue
            if merged[name] == blob:
                continue
            existing_hash = hashlib.sha1(merged[name]).hexdigest()[:8]
            incoming_hash = hashlib.sha1(blob).hexdigest()[:8]
            warnings.append(
                f"asset collision on '{name}' (kept first: {existing_hash}, skipped {incoming_hash} from {preset.source.name})"
            )
    return merged, warnings


def find_presets(patterns: Sequence[str]) -> List[Path]:
    if not patterns:
        return sorted(Path(".").glob("*.phaseplant"))

    results: List[Path] = []
    for pattern in patterns:
        p = Path(pattern)
        if p.exists() and p.is_file():
            results.append(p)
            continue

        matches = [Path(m) for m in fnmatch.filter([str(x) for x in Path(".").glob("*.phaseplant")], pattern)]
        if matches:
            results.extend(matches)
            continue

        # Last fallback: recursive glob pattern.
        recursive = [x for x in Path(".").glob(pattern) if x.is_file() and x.suffix == ".phaseplant"]
        results.extend(recursive)

    # Preserve order and dedupe.
    seen = set()
    unique: List[Path] = []
    for path in results:
        key = str(path.resolve())
        if key in seen:
            continue
        seen.add(key)
        unique.append(path)
    return unique


def cmd_mutate(args: argparse.Namespace) -> None:
    rng = random.Random(args.seed)
    preset = load_preset(Path(args.input))
    out_state = copy.deepcopy(preset.state)
    enum_pool = build_enum_pool([preset.state])
    generator_guard = capture_generator_ingredient_guard(preset.state)
    changes = mutate_state(out_state, rng, amount=args.amount, enum_pool=enum_pool)
    repaired_guard = enforce_generator_ingredient_guard(out_state, generator_guard)
    repaired = sanitize_state_for_output(out_state)
    repaired += apply_output_audibility_safety(out_state)
    write_preset(Path(args.output), out_state, preset.assets)
    print(f"mutated {changes} values -> {args.output}")
    if repaired_guard:
        print(f"generator ingredient fixes applied: {repaired_guard}")
    if repaired:
        print(f"integrity fixes applied: {repaired}")


def cmd_combine(args: argparse.Namespace) -> None:
    rng = random.Random(args.seed)
    input_paths = [Path(p) for p in args.inputs]
    if len(input_paths) < 2:
        fail("combine requires at least 2 input presets")

    presets = [load_preset(p) for p in input_paths]
    base = copy.deepcopy(presets[0].state)
    donors = [p.state for p in presets[1:]]
    enum_pool = build_enum_pool([p.state for p in presets])
    generator_guard = capture_generator_ingredient_guard(presets[0].state)

    crossover_changes = crossover_states(base, donors, rng, mix_rate=args.mix_rate, enum_pool=enum_pool)
    mutate_changes = 0
    if args.mutate > 0:
        mutate_changes = mutate_state(base, rng, amount=args.mutate, enum_pool=enum_pool)
    repaired_guard = enforce_generator_ingredient_guard(base, generator_guard)
    repaired = sanitize_state_for_output(base)
    repaired += apply_output_audibility_safety(base)

    assets, warnings = merge_assets(presets)
    write_preset(Path(args.output), base, assets)

    print(f"combined {len(input_paths)} presets -> {args.output}")
    print(f"crossover changes: {crossover_changes}, mutation changes: {mutate_changes}")
    if repaired_guard:
        print(f"generator ingredient fixes applied: {repaired_guard}")
    if repaired:
        print(f"integrity fixes applied: {repaired}")
    if warnings:
        print("asset warnings:")
        for warning in warnings:
            print(f"- {warning}")


def cmd_random(args: argparse.Namespace) -> None:
    rng = random.Random(args.seed)
    pool_paths = find_presets(args.sources)
    output_path = Path(args.output).resolve()
    pool_paths = [path for path in pool_paths if path.resolve() != output_path]
    if len(pool_paths) < 2:
        fail("need at least 2 presets in the source pool")

    loaded = [load_preset(path) for path in pool_paths]
    presets: List[Preset] = []
    skipped: List[str] = []
    for preset in loaded:
        unresolved = find_unresolved_snapin_targets(preset.state)
        if unresolved:
            skipped.append(f"{preset.source.name} ({len(unresolved)} unresolved snapin targets)")
            continue
        presets.append(preset)
    if len(presets) < 2:
        fail("need at least 2 valid presets in the source pool after filtering invalid snapin references")

    base_preset = rng.choice(presets)
    donor_count = max(1, min(args.donors, len(presets) - 1))
    donor_presets = rng.sample([p for p in presets if p.source != base_preset.source], donor_count)

    working = copy.deepcopy(base_preset.state)
    working_presets = [base_preset] + donor_presets
    enum_pool = build_enum_pool([preset.state for preset in working_presets])

    crossover_changes = crossover_states(
        working,
        [p.state for p in donor_presets],
        rng,
        mix_rate=args.mix_rate,
        enum_pool=enum_pool,
    )
    mutate_changes = mutate_state(working, rng, amount=args.mutate, enum_pool=enum_pool)
    structure_changes = catalyze_expand_structure(
        working,
        [preset.state for preset in working_presets],
        rng,
        complexity=args.mutate,
    )
    mutate_changes += structure_changes
    repaired = sanitize_state_for_output(working)
    repaired += apply_random_audibility_safety(working)

    assets, warnings = merge_assets(working_presets)
    write_preset(Path(args.output), working, assets)

    print(f"random preset -> {args.output}")
    print(f"base: {base_preset.source.name}")
    print("donors: " + ", ".join(p.source.name for p in donor_presets))
    print(f"crossover changes: {crossover_changes}, mutation/structure changes: {mutate_changes}")
    if repaired:
        print(f"integrity fixes applied: {repaired}")
    if skipped:
        print("skipped invalid sources:")
        for item in skipped:
            print(f"- {item}")
    if warnings:
        print("asset warnings:")
        for warning in warnings:
            print(f"- {warning}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Combine, mutate, and randomize Kilohearts Phase Plant .phaseplant presets"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_mutate = sub.add_parser("mutate", help="Mutate a single preset")
    p_mutate.add_argument("input", help="Input .phaseplant file")
    p_mutate.add_argument("output", help="Output .phaseplant file")
    p_mutate.add_argument("--amount", type=float, default=0.2, help="Mutation intensity/probability (0..1)")
    p_mutate.add_argument("--seed", type=int, default=None, help="Random seed for reproducible output")
    p_mutate.set_defaults(func=cmd_mutate)

    p_combine = sub.add_parser("combine", help="Combine multiple presets")
    p_combine.add_argument("inputs", nargs="+", help="Input .phaseplant files (first is base)")
    p_combine.add_argument("-o", "--output", required=True, help="Output .phaseplant file")
    p_combine.add_argument("--mix-rate", type=float, default=0.35, help="Crossover probability per parameter (0..1)")
    p_combine.add_argument("--mutate", type=float, default=0.1, help="Optional post-crossover mutation amount (0..1)")
    p_combine.add_argument("--seed", type=int, default=None, help="Random seed for reproducible output")
    p_combine.set_defaults(func=cmd_combine)

    p_random = sub.add_parser("random", help="Generate a random preset from a preset pool")
    p_random.add_argument("-o", "--output", required=True, help="Output .phaseplant file")
    p_random.add_argument(
        "--sources",
        nargs="*",
        default=["*.phaseplant"],
        help="Source files or glob patterns (default: *.phaseplant)",
    )
    p_random.add_argument("--donors", type=int, default=3, help="Number of donor presets")
    p_random.add_argument("--mix-rate", type=float, default=0.45, help="Crossover probability per parameter (0..1)")
    p_random.add_argument("--mutate", type=float, default=0.25, help="Mutation amount after crossover (0..1)")
    p_random.add_argument("--seed", type=int, default=None, help="Random seed for reproducible output")
    p_random.set_defaults(func=cmd_random)

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    for key in ("amount", "mix_rate", "mutate"):
        if hasattr(args, key):
            value = getattr(args, key)
            if not 0 <= value <= 1:
                fail(f"--{key.replace('_', '-')} must be between 0 and 1")

    args.func(args)


if __name__ == "__main__":
    main()
