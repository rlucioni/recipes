#!/usr/bin/env python3
"""Deterministic helpers for maintaining catalog.tsv.

The script deliberately does not classify recipes. It discovers work, creates
batch manifests for Cursor agents, validates their candidate rows, applies the
result atomically, and records content hashes for incremental runs.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import sys
import tempfile
from collections import Counter
from pathlib import Path
from typing import Any, Iterable


SCHEMA_VERSION = 2
HEADER = ["name", "type", "prep_time"]
METADATA_FIELDS = HEADER[1:]
TYPE_VALUES = {"meal", "baked_or_dessert", "drink", "component"}
PREP_TIME_VALUES = {"short", "medium", "long"}
NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")

SCRIPT_PATH = Path(__file__).resolve()
DEFAULT_ROOT = SCRIPT_PATH.parents[4]


class CatalogError(Exception):
    """An expected validation or workflow error."""


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(value, indent=2, sort_keys=True) + "\n"
    atomic_write(path, text)


def read_json(path: Path, default: Any = None) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CatalogError(f"cannot read JSON {path}: {exc}") from exc


def atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(
        dir=path.parent, prefix=f".{path.name}.", suffix=".tmp", text=True
    )
    temporary_path = Path(temporary)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        temporary_path.replace(path)
    except Exception:
        temporary_path.unlink(missing_ok=True)
        raise


def recipe_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def discover(root: Path) -> dict[str, dict[str, str]]:
    recipes_dir = root / "recipes"
    if not recipes_dir.is_dir():
        raise CatalogError(f"recipes directory does not exist: {recipes_dir}")
    recipes: dict[str, dict[str, str]] = {}
    for path in sorted(recipes_dir.glob("*.md"), key=lambda item: item.stem):
        name = path.stem
        if not NAME_RE.fullmatch(name):
            raise CatalogError(f"recipe filename is not kebab-case: {path.name}")
        recipes[name] = {
            "name": name,
            "path": path.relative_to(root).as_posix(),
            "sha256": recipe_hash(path),
        }
    return recipes


def read_catalog(path: Path) -> tuple[list[str], dict[str, dict[str, str]]]:
    if not path.exists():
        raise CatalogError(f"catalog does not exist: {path}")
    try:
        with path.open(encoding="utf-8", newline="") as handle:
            reader = csv.reader(handle, delimiter="\t")
            raw_rows = list(reader)
    except (OSError, csv.Error) as exc:
        raise CatalogError(f"cannot read catalog {path}: {exc}") from exc

    if not raw_rows:
        raise CatalogError("catalog is empty; it must contain a header")
    header = raw_rows[0]
    if "name" not in header:
        raise CatalogError("catalog header must contain a name column")
    if len(set(header)) != len(header):
        raise CatalogError("catalog header contains duplicate columns")

    name_index = header.index("name")
    rows: dict[str, dict[str, str]] = {}
    for line_number, values in enumerate(raw_rows[1:], start=2):
        if len(values) != len(header):
            raise CatalogError(
                f"line {line_number}: expected {len(header)} columns, got {len(values)}"
            )
        name = values[name_index]
        if not name:
            raise CatalogError(f"line {line_number}: name is required")
        if name in rows:
            raise CatalogError(f"line {line_number}: duplicate name {name}")
        source = dict(zip(header, values))
        rows[name] = {field: source.get(field, "") for field in HEADER}
    return header, rows


def validate_row(row: dict[str, str]) -> list[str]:
    errors: list[str] = []
    name = row.get("name", "")
    if not NAME_RE.fullmatch(name):
        errors.append("name must be a lowercase kebab-case recipe stem")

    recipe_type = row.get("type", "")
    if recipe_type and recipe_type not in TYPE_VALUES:
        errors.append(f"type is not allowed: {recipe_type}")

    prep_time = row.get("prep_time", "")
    if prep_time and prep_time not in PREP_TIME_VALUES:
        errors.append(f"prep_time is not allowed: {prep_time}")
    return errors


def validate_catalog(root: Path) -> dict[str, Any]:
    catalog_path = root / "catalog.tsv"
    errors: list[str] = []
    try:
        header, rows = read_catalog(catalog_path)
    except CatalogError as exc:
        return {"ok": False, "errors": [str(exc)], "rows": 0}

    if header != HEADER:
        errors.append(
            f"header must be {'<TAB>'.join(HEADER)}; got {'<TAB>'.join(header)}"
        )

    names = list(rows)
    if names != sorted(names):
        errors.append("catalog rows are not sorted by name")

    for name, row in rows.items():
        for message in validate_row(row):
            errors.append(f"{name}: {message}")

    recipes = discover(root)
    missing = sorted(set(recipes) - set(rows))
    extra = sorted(set(rows) - set(recipes))
    if missing:
        errors.append(f"missing recipe rows: {', '.join(missing)}")
    if extra:
        errors.append(f"rows without live recipes: {', '.join(extra)}")

    return {
        "ok": not errors,
        "errors": errors,
        "rows": len(rows),
        "recipes": len(recipes),
    }


def load_state(path: Path) -> dict[str, Any]:
    state = read_json(path, {"schema_version": None, "recipe_hashes": {}})
    if not isinstance(state, dict) or not isinstance(
        state.get("recipe_hashes", {}), dict
    ):
        raise CatalogError(f"invalid state file: {path}")
    return state


def invalid_fields(row: dict[str, str]) -> list[str]:
    fields: list[str] = []
    if row.get("type", "") and row["type"] not in TYPE_VALUES:
        fields.append("type")
    if row.get("prep_time", "") and row["prep_time"] not in PREP_TIME_VALUES:
        fields.append("prep_time")
    return fields


def clean_work_files(work_dir: Path) -> None:
    work_dir.mkdir(parents=True, exist_ok=True)
    (work_dir / "candidates").mkdir(exist_ok=True)
    for pattern in ("batch-*.json",):
        for path in work_dir.glob(pattern):
            path.unlink()
    for path in (work_dir / "candidates").glob("batch-*.tsv"):
        path.unlink()


def create_plan(
    root: Path, mode: str, batch_size: int, work_dir: Path
) -> dict[str, Any]:
    if batch_size < 1:
        raise CatalogError("batch size must be at least 1")
    header, rows = read_catalog(root / "catalog.tsv")
    recipes = discover(root)
    state = load_state(root / "catalog-state.json")
    state_hashes = state.get("recipe_hashes", {})

    additions = sorted(set(recipes) - set(rows))
    deletions = sorted(set(rows) - set(recipes)) if mode == "update-table" else []
    schema_changed = state.get("schema_version") != SCHEMA_VERSION
    header_changed = header != HEADER

    work: dict[str, dict[str, Any]] = {}
    for name in additions:
        work[name] = {
            "name": name,
            "action": "add",
            "reasons": ["no_catalog_row"],
            "fields_to_infer": METADATA_FIELDS,
        }

    if mode == "update-table":
        for name in sorted(set(recipes) & set(rows)):
            reasons: list[str] = []
            fields: set[str] = set()
            if schema_changed:
                reasons.append("schema_version_changed")
                fields.update(METADATA_FIELDS)
            if state_hashes.get(name) != recipes[name]["sha256"]:
                reasons.append("recipe_content_changed")
                fields.update(METADATA_FIELDS)
            invalid = invalid_fields(rows[name])
            if invalid:
                reasons.append("invalid_catalog_values")
                fields.update(invalid)
            if reasons:
                work[name] = {
                    "name": name,
                    "action": "reconcile",
                    "reasons": reasons,
                    "fields_to_infer": [
                        field for field in METADATA_FIELDS if field in fields
                    ],
                }

    clean_work_files(work_dir)
    items: list[dict[str, Any]] = []
    for name in sorted(work):
        item = {
            **work[name],
            "path": recipes[name]["path"],
            "sha256": recipes[name]["sha256"],
            "current": rows.get(name),
        }
        items.append(item)

    batches: list[dict[str, Any]] = []
    for start in range(0, len(items), batch_size):
        number = len(batches) + 1
        batch_items = items[start : start + batch_size]
        filename = f"batch-{number:03d}.json"
        candidate = f"candidates/batch-{number:03d}.tsv"
        manifest = {
            "schema_version": SCHEMA_VERSION,
            "mode": mode,
            "candidate_path": candidate,
            "items": batch_items,
        }
        write_json(work_dir / filename, manifest)
        batches.append(
            {
                "manifest": filename,
                "candidate": candidate,
                "names": [item["name"] for item in batch_items],
            }
        )

    plan = {
        "schema_version": SCHEMA_VERSION,
        "mode": mode,
        "catalog": "catalog.tsv",
        "state": "catalog-state.json",
        "header_changed": header_changed,
        "state_schema_changed": schema_changed,
        "additions": additions,
        "deletions": deletions,
        "items": items,
        "batches": batches,
        "live_hashes": {name: recipes[name]["sha256"] for name in sorted(recipes)},
    }
    write_json(work_dir / "plan.json", plan)
    return plan


def parse_candidate(path: Path) -> list[dict[str, str]]:
    try:
        with path.open(encoding="utf-8", newline="") as handle:
            values = list(csv.reader(handle, delimiter="\t"))
    except (OSError, csv.Error) as exc:
        raise CatalogError(f"cannot read candidate {path}: {exc}") from exc
    rows: list[dict[str, str]] = []
    for line_number, fields in enumerate(values, start=1):
        if len(fields) != len(HEADER):
            raise CatalogError(
                f"{path}:{line_number}: expected {len(HEADER)} columns, "
                f"got {len(fields)}"
            )
        row = dict(zip(HEADER, fields))
        errors = validate_row(row)
        if errors:
            raise CatalogError(f"{path}:{line_number}: {'; '.join(errors)}")
        rows.append(row)
    return rows


def load_candidates(work_dir: Path, plan: dict[str, Any]) -> dict[str, dict[str, str]]:
    expected = [item["name"] for item in plan["items"]]
    candidates: dict[str, dict[str, str]] = {}
    for batch in plan["batches"]:
        path = work_dir / batch["candidate"]
        if not path.exists():
            raise CatalogError(f"candidate file is missing: {path}")
        batch_rows = parse_candidate(path)
        actual_names = [row["name"] for row in batch_rows]
        if actual_names != batch["names"]:
            raise CatalogError(
                f"{path}: names/order do not match manifest; "
                f"expected {batch['names']}, got {actual_names}"
            )
        for row in batch_rows:
            if row["name"] in candidates:
                raise CatalogError(f"duplicate candidate name: {row['name']}")
            candidates[row["name"]] = row
    if list(candidates) != expected:
        raise CatalogError("candidate rows do not exactly match planned items")
    return candidates


def catalog_text(rows: dict[str, dict[str, str]]) -> str:
    output = ["\t".join(HEADER)]
    for name in sorted(rows):
        row = rows[name]
        output.append("\t".join(row.get(field, "") for field in HEADER))
    return "\n".join(output) + "\n"


def field_changes(
    before: dict[str, str], after: dict[str, str]
) -> dict[str, dict[str, str]]:
    return {
        field: {"old": before.get(field, ""), "new": after.get(field, "")}
        for field in METADATA_FIELDS
        if before.get(field, "") != after.get(field, "")
    }


def build_summary_markdown(diff: dict[str, Any]) -> str:
    lines = [
        f"# Catalog update ({diff['mode']})",
        "",
        f"- Added: {len(diff['added'])}",
        f"- Deleted: {len(diff['deleted'])}",
        f"- Changed: {len(diff['changed'])}",
    ]
    if diff["dry_run"]:
        lines.append("- Dry run: yes (catalog and state were not written)")
    for heading, key in (
        ("Added", "added"),
        ("Deleted", "deleted"),
        ("Changed", "changed"),
    ):
        values = diff[key]
        if not values:
            continue
        lines.extend(["", f"## {heading}"])
        for value in values:
            if key == "added":
                row = value["row"]
                filled = [
                    f"{field}={row[field]}"
                    for field in METADATA_FIELDS
                    if row[field]
                ]
                blank = [field for field in METADATA_FIELDS if not row[field]]
                lines.append(
                    f"- `{value['name']}`: filled "
                    f"{', '.join(filled) if filled else 'none'}; "
                    f"blank {', '.join(blank) if blank else 'none'}"
                )
            elif key == "deleted":
                lines.append(f"- `{value['name']}`: no live recipe file")
            else:
                changes = ", ".join(
                    f"{field}: `{values['old']}` → `{values['new']}`"
                    for field, values in value["fields"].items()
                )
                lines.append(f"- `{value['name']}`: {changes or 'schema normalization'}")
    lines.append("")
    return "\n".join(lines)


def apply_plan(root: Path, work_dir: Path, dry_run: bool) -> dict[str, Any]:
    plan = read_json(work_dir / "plan.json")
    if not plan:
        raise CatalogError(f"plan does not exist: {work_dir / 'plan.json'}")
    if plan.get("schema_version") != SCHEMA_VERSION:
        raise CatalogError("plan schema version does not match the script")

    _, original = read_catalog(root / "catalog.tsv")
    rows = {name: dict(row) for name, row in original.items()}
    candidates = load_candidates(work_dir, plan) if plan["items"] else {}

    if plan["mode"] == "update-table":
        for name in plan["deletions"]:
            rows.pop(name, None)
    for name, row in candidates.items():
        rows[name] = row

    recipes = discover(root)
    if set(rows) != set(recipes):
        missing = sorted(set(recipes) - set(rows))
        extra = sorted(set(rows) - set(recipes))
        raise CatalogError(f"merge is incomplete; missing={missing}, extra={extra}")
    for name, row in rows.items():
        errors = validate_row(row)
        if errors:
            raise CatalogError(f"{name}: {'; '.join(errors)}")

    added = [
        {"name": name, "row": rows[name]}
        for name in sorted(set(rows) - set(original))
    ]
    deleted = [{"name": name} for name in sorted(set(original) - set(rows))]
    changed = []
    for name in sorted(set(original) & set(rows)):
        changes = field_changes(original[name], rows[name])
        if changes:
            changed.append({"name": name, "fields": changes})
    diff = {
        "mode": plan["mode"],
        "dry_run": dry_run,
        "added": added,
        "deleted": deleted,
        "changed": changed,
    }

    if not dry_run:
        atomic_write(root / "catalog.tsv", catalog_text(rows))
        if plan["mode"] == "update-table":
            next_state = {
                "schema_version": SCHEMA_VERSION,
                "recipe_hashes": plan["live_hashes"],
            }
        else:
            current_state = load_state(root / "catalog-state.json")
            next_hashes = dict(current_state.get("recipe_hashes", {}))
            for name in plan["additions"]:
                next_hashes[name] = plan["live_hashes"][name]
            next_state = {
                "schema_version": current_state.get("schema_version"),
                "recipe_hashes": dict(sorted(next_hashes.items())),
            }
        write_json(
            root / "catalog-state.json",
            next_state,
        )
    write_json(work_dir / "diff.json", diff)
    atomic_write(work_dir / "summary.md", build_summary_markdown(diff))
    return diff


def bootstrap_state(root: Path, dry_run: bool) -> dict[str, Any]:
    result = validate_catalog(root)
    if not result["ok"]:
        raise CatalogError(
            "cannot bootstrap state for an invalid catalog: "
            + "; ".join(result["errors"])
        )
    recipes = discover(root)
    state = {
        "schema_version": SCHEMA_VERSION,
        "recipe_hashes": {name: recipes[name]["sha256"] for name in sorted(recipes)},
    }
    if not dry_run:
        write_json(root / "catalog-state.json", state)
    return {"ok": True, "recipes": len(recipes), "dry_run": dry_run}


def finish_plan(root: Path, work_dir: Path, dry_run: bool) -> dict[str, Any]:
    diff = apply_plan(root, work_dir, dry_run)
    validation = (
        {"ok": True, "skipped": "dry-run validates the merged result in memory"}
        if dry_run
        else validate_catalog(root)
    )
    if not validation["ok"]:
        raise CatalogError(
            "catalog failed validation after apply: "
            + "; ".join(validation.get("errors", []))
        )
    return {"ok": True, "diff": diff, "validation": validation}


def stats(plan: dict[str, Any]) -> dict[str, Any]:
    reasons = Counter(
        reason for item in plan["items"] for reason in item.get("reasons", [])
    )
    return {
        "mode": plan["mode"],
        "items": len(plan["items"]),
        "batches": len(plan["batches"]),
        "additions": len(plan["additions"]),
        "deletions": len(plan["deletions"]),
        "header_changed": plan["header_changed"],
        "state_schema_changed": plan["state_schema_changed"],
        "reasons": dict(sorted(reasons.items())),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root", type=Path, default=DEFAULT_ROOT, help="repository root"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    plan_parser = subparsers.add_parser("plan", help="discover and batch catalog work")
    plan_parser.add_argument("--mode", choices=("add-new", "update-table"), required=True)
    plan_parser.add_argument("--batch-size", type=int, default=24)
    plan_parser.add_argument("--work-dir", type=Path, default=Path(".catalog-work"))

    candidates_parser = subparsers.add_parser(
        "validate-candidates", help="validate all planned candidate batches"
    )
    candidates_parser.add_argument("--work-dir", type=Path, default=Path(".catalog-work"))

    apply_parser = subparsers.add_parser("apply", help="apply validated candidates")
    apply_parser.add_argument("--work-dir", type=Path, default=Path(".catalog-work"))
    apply_parser.add_argument("--dry-run", action="store_true")

    finish_parser = subparsers.add_parser(
        "finish", help="validate candidates, apply, and validate the catalog"
    )
    finish_parser.add_argument("--work-dir", type=Path, default=Path(".catalog-work"))
    finish_parser.add_argument("--dry-run", action="store_true")

    subparsers.add_parser("validate", help="validate catalog.tsv against live recipes")

    state_parser = subparsers.add_parser(
        "bootstrap-state", help="record hashes for an already valid catalog"
    )
    state_parser.add_argument("--dry-run", action="store_true")
    return parser


def resolve_work_dir(root: Path, work_dir: Path) -> Path:
    return work_dir if work_dir.is_absolute() else root / work_dir


def main(argv: Iterable[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    root = args.root.resolve()
    try:
        if args.command == "plan":
            work_dir = resolve_work_dir(root, args.work_dir)
            result = stats(create_plan(root, args.mode, args.batch_size, work_dir))
        elif args.command == "validate-candidates":
            work_dir = resolve_work_dir(root, args.work_dir)
            plan = read_json(work_dir / "plan.json")
            if not plan:
                raise CatalogError(f"plan does not exist: {work_dir / 'plan.json'}")
            candidates = load_candidates(work_dir, plan)
            result = {"ok": True, "rows": len(candidates)}
        elif args.command == "apply":
            result = apply_plan(
                root, resolve_work_dir(root, args.work_dir), args.dry_run
            )
        elif args.command == "finish":
            result = finish_plan(
                root, resolve_work_dir(root, args.work_dir), args.dry_run
            )
        elif args.command == "validate":
            result = validate_catalog(root)
            if not result["ok"]:
                print(json.dumps(result, indent=2, sort_keys=True))
                return 1
        elif args.command == "bootstrap-state":
            result = bootstrap_state(root, args.dry_run)
        else:  # pragma: no cover
            parser.error(f"unknown command: {args.command}")
            return 2
    except CatalogError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, indent=2), file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
