#!/usr/bin/env python3
"""APLYD engineering configuration audit and explicit command runner.

Python >=3.11; standard library only. No network or application execution in audit.
The runner executes reviewed arbitrary code: it is NOT a sandbox or proof of
application compliance. See ../README.md for the exact enforcement boundary.
"""
from __future__ import annotations

import argparse
import copy
import csv
import xml.etree.ElementTree as ET
from urllib.parse import urlsplit
import datetime as dt
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import signal
import subprocess
import sys
import time
import uuid
from typing import Any

sys.dont_write_bytecode = True
if sys.version_info < (3, 11):
    raise SystemExit("BLOCKED: Python 3.11 or later is required")

from integration import check_integration, csv_rows
VERSION = "2.1.0"
PACKAGE = Path(__file__).resolve().parents[2]
CHECK_IDS = ("format", "lint", "types", "build", "dependencies", "secrets", "tests")
MAX_JSON_BYTES = 8 * 1024 * 1024
MAX_LOG_BYTES = 5 * 1024 * 1024


class ConfigurationError(ValueError):
    """A controlled input or configuration error, not a successful check."""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def safe_path(root: Path, value: str) -> Path:
    """Repository-relative path; reject traversal, links and .git internals."""
    if not isinstance(value, str) or not value or "\\" in value or "\x00" in value:
        raise ConfigurationError("A path must be a nonempty POSIX-style relative path")
    relative = PurePosixPath(value)
    if relative.is_absolute() or ".." in relative.parts or ".git" in relative.parts:
        raise ConfigurationError(f"Unsafe repository-relative path: {value!r}")
    candidate = root
    for part in relative.parts:
        candidate = candidate / part
        if candidate.is_symlink():
            raise ConfigurationError(f"Symlink path is not permitted: {value!r}")
    if not candidate.resolve().is_relative_to(root.resolve()):
        raise ConfigurationError(f"Path leaves the selected root: {value!r}")
    return candidate


def read_json(path: Path) -> Any:
    if not path.is_file() or path.is_symlink():
        raise ConfigurationError(f"Missing regular JSON file: {path.name}")
    if path.stat().st_size > MAX_JSON_BYTES:
        raise ConfigurationError(f"JSON input exceeds {MAX_JSON_BYTES} bytes: {path.name}")
    def unique_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        value: dict[str, Any] = {}
        for key, item in pairs:
            if key in value:
                raise ConfigurationError(f"Duplicate JSON key in {path.name}: {key}")
            value[key] = item
        return value
    def reject_constant(value: str) -> None:
        raise ConfigurationError(f"Non-finite JSON constant in {path.name}: {value}")
    try:
        return json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=unique_pairs,
                          parse_constant=reject_constant)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ConfigurationError(f"Cannot read valid JSON from {path.name}: {exc}") from exc


def schema_errors(value: Any, schema: dict[str, Any], where: str = "$", depth: int = 0) -> list[str]:
    """Validate the explicitly supported subset used by the shipped schemas.

    Unknown schema keywords fail instead of silently pretending full JSON Schema
    support. This utility is not a general Draft 2020-12 implementation.
    """
    allowed = {"$schema", "title", "description", "type", "properties", "required",
               "additionalProperties", "items", "enum", "minLength", "pattern",
               "minItems", "minimum", "maximum"}
    if depth > 50:
        return [f"{where}: maximum schema/data depth exceeded"]
    unknown = set(schema) - allowed
    if unknown:
        return [f"{where}: unsupported schema keywords: {sorted(unknown)}"]
    expected = schema.get("type")
    kinds = expected if isinstance(expected, list) else [expected] if expected else []
    predicates = {
        "object": lambda x: isinstance(x, dict),
        "array": lambda x: isinstance(x, list),
        "string": lambda x: isinstance(x, str),
        "integer": lambda x: isinstance(x, int) and not isinstance(x, bool),
        "number": lambda x: isinstance(x, (int, float)) and not isinstance(x, bool),
        "boolean": lambda x: isinstance(x, bool),
        "null": lambda x: x is None,
    }
    if any(kind not in predicates for kind in kinds):
        return [f"{where}: unsupported schema type"]
    if kinds and not any(predicates[kind](value) for kind in kinds):
        return [f"{where}: expected {' or '.join(kinds)}"]
    issues: list[str] = []
    if "enum" in schema and not any(type(value) is type(v) and value == v for v in schema["enum"]):
        issues.append(f"{where}: value is outside the permitted enumeration")
    if isinstance(value, dict):
        props = schema.get("properties", {})
        for key in schema.get("required", []):
            if key not in value:
                issues.append(f"{where}.{key}: required field missing")
        if schema.get("additionalProperties") is False:
            for key in sorted(set(value) - set(props)):
                issues.append(f"{where}.{key}: unknown field")
        for key in value.keys() & props.keys():
            issues.extend(schema_errors(value[key], props[key], f"{where}.{key}", depth + 1))
    elif isinstance(value, list):
        if len(value) < schema.get("minItems", 0):
            issues.append(f"{where}: too few items")
        if "items" in schema:
            for index, item in enumerate(value):
                issues.extend(schema_errors(item, schema["items"], f"{where}[{index}]", depth + 1))
    elif isinstance(value, str):
        if len(value) < schema.get("minLength", 0):
            issues.append(f"{where}: string is too short")
        if schema.get("minLength", 0) >= 1 and not value.strip():
            issues.append(f"{where}: required text must not be blank")
        if "pattern" in schema and re.search(schema["pattern"], value) is None:
            issues.append(f"{where}: string does not match the required pattern")
    elif isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            issues.append(f"{where}: below minimum")
        if "maximum" in schema and value > schema["maximum"]:
            issues.append(f"{where}: above maximum")
    return issues


def all_strings(value: Any, where: str = "$") -> list[tuple[str, str]]:
    if isinstance(value, str):
        return [(where, value)]
    if isinstance(value, dict):
        return [pair for key, item in value.items() for pair in all_strings(item, f"{where}.{key}")]
    if isinstance(value, list):
        return [pair for index, item in enumerate(value) for pair in all_strings(item, f"{where}[{index}]")]
    return []


def placeholder_errors(value: Any, name: str) -> list[str]:
    pattern = re.compile(r"\b(?:UNCONFIGURED|REPLACE_ME|TBD|TODO)\b|<(?:replace|owner|issue|repo|next-number)[^>]*>", re.I)
    return [f"{name}{where[1:]}: unresolved placeholder" for where, text in all_strings(value) if pattern.search(text)]


def is_runtime_cache(relative: str) -> bool:
    """Ignore generated cache data, never changed manifest entries or symlinks.

    Unexpected Python source in a cache directory is still an unlisted payload.
    The manifest's own files are always hashed, before this enumeration filter.
    """
    parts = PurePosixPath(relative).parts
    if "__pycache__" in parts and relative.endswith(".pyc"):
        return True
    if ".pytest_cache" in parts:
        tail = "/".join(parts[parts.index(".pytest_cache") + 1:])
        return tail in {".gitignore", "CACHEDIR.TAG", "README.md",
                        "v/cache/nodeids", "v/cache/lastfailed", "v/cache/stepwise"}
    return False


def verify_package(root: Path) -> dict[str, Any]:
    """Integrity checks detect corruption, not publisher authenticity."""
    root = root.resolve()
    failures: list[str] = []
    manifest = read_json(safe_path(root, "PACKAGE_MANIFEST.json"))
    if not isinstance(manifest, dict) or manifest.get("version") != VERSION or not isinstance(manifest.get("files"), list):
        raise ConfigurationError("Invalid package manifest structure/version")
    expected: set[str] = set()
    for item in manifest["files"]:
        if not isinstance(item, dict) or not {"path", "sha256", "bytes"} <= item.keys():
            raise ConfigurationError("Malformed package manifest entry")
        name = item["path"]
        if (not isinstance(item["sha256"], str)
                or re.fullmatch(r"[a-f0-9]{64}", item["sha256"]) is None
                or type(item["bytes"]) is not int or item["bytes"] < 0):
            raise ConfigurationError("Invalid digest or size in package manifest")
        path = safe_path(root, name)
        if name in expected:
            failures.append(f"Duplicate manifest path: {name}")
        expected.add(name)
        if not path.is_file():
            failures.append(f"Missing payload: {name}")
        elif path.stat().st_size != item["bytes"] or sha256_file(path) != item["sha256"]:
            failures.append(f"Payload digest/size mismatch: {name}")
    actual = set()
    for path in root.rglob("*"):
        relative = path.relative_to(root).as_posix()
        if relative == ".git" or relative.startswith(".git/"):
            continue  # Source-control metadata is not distributed payload.
        if path.is_symlink():
            failures.append(f"Unexpected symlink: {path.relative_to(root).as_posix()}")
            continue
        if is_runtime_cache(relative):
            continue
        if path.is_file() and relative not in {"PACKAGE_MANIFEST.json", "SHA256SUMS"}:
            actual.add(path.relative_to(root).as_posix())
    for name in sorted(actual - expected):
        failures.append(f"Unlisted payload: {name}")
    sums_path = safe_path(root, "SHA256SUMS")
    if not sums_path.is_file():
        failures.append("Missing SHA256SUMS")
    else:
        listed_sums = {}
        for line in sums_path.read_text(encoding="utf-8").splitlines():
            match = re.fullmatch(r"([a-f0-9]{64})  (.+)", line)
            if not match or match[2] in listed_sums:
                failures.append("Malformed or duplicate SHA256SUMS entry")
                continue
            listed_sums[match[2]] = match[1]
        if set(listed_sums) != expected | {"PACKAGE_MANIFEST.json"}:
            failures.append("SHA256SUMS file set differs from manifest payload")
        for name, digest in listed_sums.items():
            path = safe_path(root, name)
            if not path.is_file() or sha256_file(path) != digest:
                failures.append(f"SHA256SUMS mismatch: {name}")
    catalogue = read_json(safe_path(root, "06-enforcement/controls.json"))
    controls = catalogue.get("controls", []) if isinstance(catalogue, dict) else []
    ids: set[str] = set()
    for control in controls:
        if not isinstance(control, dict):
            raise ConfigurationError("Malformed control catalogue entry")
        control_id = control.get("id", "")
        if control_id in ids:
            failures.append(f"Duplicate control ID: {control_id}")
        ids.add(control_id)
        path = safe_path(root, control.get("document", ""))
        if not path.is_file() or f"### {control_id} — " not in path.read_text(encoding="utf-8"):
            failures.append(f"Control missing from normative document: {control_id}")
    return {"kind": "package-integrity", "package_version": VERSION,
            "state": "INTEGRITY_PASS" if not failures else "INTEGRITY_FAIL",
            "payload_files": len(expected), "controls": len(ids), "errors": failures,
            "limits": "Checksums detect corruption; they do not authenticate the publisher or prove application compliance."}


UNRESOLVED = {"n/a", "na", "none", "pending", "unknown", "null", "unassigned", "not assigned", "-", "tbd", "todo", "unconfigured", "replace_me"}


def unresolved(value: Any) -> bool:
    return not isinstance(value, str) or not value.strip() or value.strip().casefold() in UNRESOLVED


def reference_errors(root: Path, ref: str | None, name: str) -> list[str]:
    if unresolved(ref):
        return [f"{name}: an actual reference is required, not a placeholder"]
    if ref.startswith("https://"):
        parsed = urlsplit(ref)
        if not parsed.hostname or parsed.username or parsed.password:
            return [f"{name}: invalid HTTPS reference"]
        return []  # A reference only; no network call or authority verification.
    try:
        path = safe_path(root, ref.split("#", 1)[0])
        return [] if path.is_file() else [f"{name}: referenced local file does not exist"]
    except ConfigurationError as exc:
        return [f"{name}: {exc}"]


def load_exceptions(root: Path, profile: dict[str, Any]) -> dict[str, Any]:
    """Read the one selected source. CSV mapping is explicit, never guessed.

    No normalized copy is written. Native status/control semantics outside
    engineering remain the responsibility of the native product/QA checker.
    """
    config = profile["exception_register"]
    path = safe_path(root, config["path"])
    if not path.is_file():
        raise ConfigurationError("Missing selected shared exception register")
    if config["format"] == "json":
        if config["field_map"] or config["status_map"] or config.get("defaults"):
            raise ConfigurationError("Canonical JSON register needs no CSV mapping")
        return read_json(path)
    schema = read_json(PACKAGE / "06-enforcement/schemas/exceptions.schema.json")
    fields = schema["properties"]["exceptions"]["items"]["required"]
    defaults = config.get("defaults", {})
    if not isinstance(defaults, dict) or set(defaults) - {"domains", "remediation_ref"}:
        raise ConfigurationError("CSV defaults may supply only domains/remediation_ref, never approval, status or risk")
    if set(defaults) & set(config["field_map"]) or set(defaults) | set(config["field_map"]) != set(fields):
        raise ConfigurationError("Shared CSV exception register requires one explicit mapping/default for every canonical field")
    if defaults:
        approval_errors = reference_errors(root, config.get("mapping_approval_ref"), "exception_register.mapping_approval_ref")
        if approval_errors:
            raise ConfigurationError("; ".join(approval_errors))
        if "domains" in defaults and defaults["domains"] != ["qa"]:
            raise ConfigurationError("Legacy QA defaults may not manufacture engineering/cross-domain waivers; add explicit domains or migrate")
        if "remediation_ref" in defaults:
            issues = reference_errors(root, defaults["remediation_ref"], "exception_register.defaults.remediation_ref")
            if issues:
                raise ConfigurationError("; ".join(issues))
    if not all(isinstance(x, str) and x for x in config["field_map"].values()):
        raise ConfigurationError("CSV exception field mappings must be nonblank header names")
    values = []
    for row in csv_rows(path, MAX_JSON_BYTES):
        try:
            item = {key: row[column] for key, column in config["field_map"].items()}
            item.update(copy.deepcopy(defaults))
            if isinstance(item["domains"], str):
                item["domains"] = [x.strip() for x in item["domains"].split(config["list_separator"]) if x.strip()]
            item["control_ids"] = [x.strip() for x in item["control_ids"].split(config["list_separator"]) if x.strip()]
            item["status"] = config["status_map"][item["status"]]
            for key in ("approver", "approval_ref", "expires_on"):
                item[key] = item[key] or None
            values.append(item)
        except KeyError as exc:
            raise ConfigurationError("CSV exception header/status has no reviewed mapping") from exc
    return {"schema_version": 2, "exceptions": values}


def exception_expired(value: str | None, now: dt.datetime) -> bool:
    """Date-only = valid through that UTC date. Timestamp = exact aware instant.

    Never truncate a QA ISO timestamp to a date, which could extend a waiver.
    """
    if not isinstance(value, str):
        raise ValueError("Expiry is missing")
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        return dt.date.fromisoformat(value) < now.astimezone(dt.timezone.utc).date()
    parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("Expiry timestamp must have a timezone")
    return parsed <= now


def legacy_exception_errors(root: Path, profile: dict, selected: dict) -> list[str]:
    """A surviving v1 source cannot silently become an orphan register."""
    try:
        path = safe_path(root, ".engineering/exceptions.json")
        if not path.exists() or profile["exception_register"]["path"] == ".engineering/exceptions.json":
            return []
        old = read_json(path)
        if not isinstance(old, dict) or not isinstance(old.get("exceptions"), list):
            return ["legacy exceptions: malformed legacy register; reconcile it explicitly"]
        if not old["exceptions"]:
            return []
        receipt_path = safe_path(root, ".aplyd/exception-migration.json")
        if not receipt_path.is_file():
            return ["legacy exceptions: nonempty .engineering/exceptions.json is unmigrated; run migrate_exceptions.py and approve the receipt"]
        receipt = read_json(receipt_path)
        if (receipt.get("state") != "approved" or receipt.get("source_path") != ".engineering/exceptions.json"
                or receipt.get("source_sha256") != sha256_file(path)
                or receipt.get("target_path") != profile["exception_register"]["path"]):
            return ["legacy exceptions: migration receipt is unapproved or refers to different source/target bytes or paths"]
        errors = reference_errors(root, receipt.get("approval_ref"), "exception_migration.approval_ref")
        ids = [entry["id"] for entry in old["exceptions"]]
        mapping = receipt.get("id_map", {})
        current = {entry["id"] for entry in selected["exceptions"]}
        if (not isinstance(mapping, dict) or set(mapping) != set(ids) or len(ids) != len(set(ids))
                or not all(isinstance(x, str) for x in mapping.values())
                or len(set(mapping.values())) != len(mapping) or not set(mapping.values()) <= current):
            errors.append("legacy exceptions: receipt must account for every old ID in the selected register")
        return errors
    except (OSError, ValueError, KeyError, TypeError) as exc:
        return [f"legacy exceptions: {exc}"]


def audit_repository(root: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    errors: list[str] = []
    warnings: list[str] = []
    loaded: dict[str, Any] = {}
    for name, schema_name in (("repository", "repository-profile"), ("commands", "commands"),
                              ("traceability", "traceability")):
        try:
            data = read_json(safe_path(root, f".engineering/{name}.json"))
            schema = read_json(PACKAGE / "06-enforcement" / "schemas" / f"{schema_name}.schema.json")
            found = schema_errors(data, schema, name)
            errors.extend(found)
            if not found:
                loaded[name] = data
        except (ConfigurationError, OSError) as exc:
            errors.append(f"{name}: {exc}")
    # No further dereference of structurally invalid documents.
    if len(loaded) != 3:
        return audit_result(errors, warnings), loaded
    profile, commands = loaded["repository"], loaded["commands"]
    try:
        loaded["exceptions"] = load_exceptions(root, profile)
        errors.extend(schema_errors(loaded["exceptions"], read_json(PACKAGE / "06-enforcement/schemas/exceptions.schema.json"), "exceptions"))
        if errors:
            return audit_result(errors, warnings), loaded
    except (ConfigurationError, OSError, ValueError, TypeError, csv.Error) as exc:
        errors.append(f"exceptions: {exc}")
        return audit_result(errors, warnings), loaded
    errors.extend(legacy_exception_errors(root, profile, loaded["exceptions"]))
    errors.extend(reference_errors(root, profile["approval_ref"], "repository.approval_ref"))
    errors.extend(reference_errors(root, profile["team_dod_ref"], "repository.team_dod_ref"))
    errors.extend(reference_errors(root, profile["tracker"]["reference"], "repository.tracker.reference"))
    try:
        evidence = safe_path(root, profile["evidence_root"])
        if evidence == root or len(evidence.relative_to(root).parts) < 2:
            errors.append("repository.evidence_root: use a dedicated nested evidence directory")
    except ConfigurationError as exc:
        errors.append(str(exc))
    if profile["integration_mode"] == "component":
        errors.extend(reference_errors(root, profile["component_mode_approval_ref"], "repository.component_mode_approval_ref"))
        warnings.append("COMPONENT ONLY: PM/QA integration is not verified; no combined release gate can pass")

    errors.extend(placeholder_errors(profile, "repository"))
    if profile["adoption_state"] != "approved":
        errors.append("repository.adoption_state: draft is not approved configuration")
    for role, owner in profile["owners"].items():
        if unresolved(owner):
            errors.append(f"repository.owners.{role}: actual accountable owner is required")
    for key in ("approval_ref", "design_depth"):
        if unresolved(profile[key]):
            errors.append(f"repository.{key}: actual value is required")
    errors.extend(reference_errors(root, profile["product_docs"]["index"], "product_docs.index"))
    if not profile["product_docs"]["baseline"]:
        errors.append("product_docs.baseline: version/commit/decision baseline is required")
    if not profile["product_docs"]["bindings"]:
        errors.append("product_docs.bindings: at least one canonical design/document binding is required")
    for index, binding in enumerate(profile["product_docs"]["bindings"]):
        errors.extend(reference_errors(root, binding["reference"], f"product_docs.bindings[{index}]"))
    if not profile["toolchain"]:
        errors.append("repository.toolchain: exact tool/runtime/provisioning records are required")
    if not profile["branch_policy_evidence_ref"]:
        if profile["maturity"] in {"pilot", "production"}:
            errors.append("repository.branch_policy_evidence_ref: server control evidence reference is required")
        else:
            warnings.append("No server control evidence is recorded; prototype configuration is not server-enforced approval")
    warnings.append("Approver identity, reference truth and actual GitHub/Linear state are not verified by this local audit")
    for name in sorted(set(profile["required_paths"]) | {"README.md", "AGENTS.md", "docs/engineering/README.md"}):
        try:
            if not safe_path(root, name).exists():
                errors.append(f"required_paths: missing {name}")
        except ConfigurationError as exc:
            errors.append(str(exc))
    try:
        package_root = safe_path(root, profile["package_path"])
        if profile["package_path"] != f".aplyd/engineering/{VERSION}":
            errors.append("repository.package_path: must match the installed versioned package path")
        integrity = verify_package(package_root)
        errors.extend(integrity["errors"])
    except (ConfigurationError, OSError) as exc:
        errors.append(f"package integrity: {exc}")
    errors.extend(placeholder_errors(commands, "commands"))
    errors.extend(reference_errors(root, commands["review_ref"], "commands.review_ref"))
    check_ids = [entry["id"] for entry in commands["checks"]]
    if sorted(check_ids) != sorted(CHECK_IDS):
        errors.append("commands.checks: exactly one record for each of the seven required categories is required")
    for item in commands["checks"]:
        label = "commands." + item["id"]
        if item["state"] == "unconfigured":
            errors.append(f"{label}: unconfigured is not a passing check")
        elif item["state"] == "configured" and not item["commands"]:
            errors.append(f"{label}: configured check must include at least one real command")
        elif item["state"] == "not_applicable":
            if item["commands"] or unresolved(item["rationale"]):
                errors.append(f"{label}: N/A requires no commands and a scoped reason")
            errors.extend(reference_errors(root, item["approval_ref"], label + ".approval_ref"))
            if item["id"] == "tests" and (profile["executable_code"] or any(r["kind"] == "implementation" for r in loaded["traceability"]["records"])):
                errors.append(f"{label}: executable/implementation scope requires an actual nonempty test run")
        if item["id"] == "tests" and item["state"] == "configured":
            report = item.get("test_report")
            if not report or not report["path"].startswith("{evidence_dir}/"):
                errors.append(f"{label}: a fresh per-run test_report under {{evidence_dir}}/ is required")
    for command in commands["setup_commands"] + [c for item in commands["checks"] for c in item["commands"]]:
        try:
            if not safe_path(root, command["cwd"]).is_dir():
                errors.append("commands: working directory does not exist")
        except ConfigurationError as exc:
            errors.append(f"commands: {exc}")
    records = loaded["traceability"]["records"]
    if not records:
        errors.append("traceability: at least the real authorised adoption/engineering work item must be recorded")
    seen: set[str] = set()
    for record in records:
        label = record["task_id"]
        if label in seen:
            errors.append(f"traceability: duplicate {label}")
        seen.add(label)
        errors.extend(placeholder_errors(record, label))
        if record["kind"] == "implementation" and (not record["story_ids"] or not record["acceptance_criterion_ids"] or not record["qa_case_ids"]):
            errors.append(f"{label}: implementation requires typed story, acceptance-criterion and QA-case IDs")
        if profile["tracker"]["system"] != "local":
            errors.extend(reference_errors(root, record["tracker_ref"], label + ".tracker_ref"))
        errors.extend(reference_errors(root, record["authorization_ref"], label + ".authorization_ref"))
        for field in ("legacy_ids", "story_ids", "acceptance_criterion_ids", "qa_case_ids", "qa_run_ids"):
            if len(record[field]) != len(set(record[field])):
                errors.append(f"{label}: duplicate identifiers in {field}")
        if record["kind"] == "discovery" and record["status"] in {"reviewed", "merged", "deployed"}:
            warnings.append(f"{label}: discovery output is not approval of product behaviour")
        if record["status"] in {"implemented", "reviewed", "merged", "deployed"}:
            if not record["paths"] or not record["branch"]:
                errors.append(f"{label}: implemented or later status requires paths and actual branch")
        if record["status"] in {"reviewed", "merged", "deployed"}:
            if not record["pr"] or not record["review_ref"]:
                errors.append(f"{label}: reviewed or later status requires actual PR and review references")
            if not record["commit"] or re.fullmatch(r"(?:[a-fA-F0-9]{40}|[a-fA-F0-9]{64})", record["commit"]) is None:
                errors.append(f"{label}: reviewed or later status requires a full source revision")
        if record["status"] == "deployed" and (not record["release"] or not record["deployment_evidence"]):
            errors.append(f"{label}: deployment requires release and observed deployment evidence")
        for value in record["paths"]:
            try:
                safe_path(root, value)  # Historical paths may since have been moved/deleted.
            except ConfigurationError as exc:
                errors.append(f"{label}: {exc}")
    catalogue = read_json(PACKAGE / "06-enforcement/controls.json")
    controls = {item["id"]: item for item in catalogue["controls"]}
    seen = set()
    now = dt.datetime.now(dt.timezone.utc)
    for exception in loaded["exceptions"]["exceptions"]:
        label = exception["id"]
        if label in seen:
            errors.append(f"exceptions: duplicate {label}")
        seen.add(label)
        for control_id in exception["control_ids"]:
            if control_id not in controls:
                if "engineering" in exception["domains"] and control_id.startswith("ENG-"):
                    errors.append(f"{label}: unknown engineering control ID {control_id}")
            elif exception["status"] == "approved" and controls[control_id]["non_waivable"]:
                errors.append(f"{label}: cannot approve deviation to non-waivable {control_id}")
        if exception["status"] == "approved":
            errors.extend(placeholder_errors(exception, label))
            if unresolved(exception["approver"]) or unresolved(exception["owner"]):
                errors.append(f"{label}: actual owner and approver required")
            errors.extend(reference_errors(root, exception["approval_ref"], label + ".approval_ref"))
            if exception["approver"] and exception["owner"].strip().casefold() == exception["approver"].strip().casefold():
                errors.append(f"{label}: requester/owner cannot self-approve")
            try:
                if exception_expired(exception["expires_on"], now):
                    errors.append(f"{label}: approved exception has expired")
            except (ValueError, TypeError):
                errors.append(f"{label}: expiry requires YYYY-MM-DD or an ISO timestamp with an explicit timezone")
            errors.extend(reference_errors(root, exception["remediation_ref"], label + ".remediation_ref"))
    if profile["integration_mode"] == "combined":
        bridge_errors, bridge_data = check_integration(root, profile, records, sys.modules[__name__])
        errors.extend(bridge_errors)
        loaded["integration"] = bridge_data
    return audit_result(errors, warnings), loaded


def audit_result(errors: list[str], warnings: list[str]) -> dict[str, Any]:
    return {"kind": "structural-engineering-audit", "package_version": VERSION,
            "state": "STRUCTURAL_PASS_HUMAN_REVIEW_REQUIRED" if not errors else "BLOCKED",
            "errors": errors, "warnings": warnings,
            "limits": "No application behaviour, live settings, approval authenticity or whole-standard compliance is certified."}


def git_snapshot(root: Path) -> dict[str, Any]:
    env = {**os.environ, "GIT_OPTIONAL_LOCKS": "0"}
    result: dict[str, Any] = {}
    for name, args in (("head", ["rev-parse", "HEAD"]), ("branch", ["branch", "--show-current"]),
                       ("working_tree", ["status", "--porcelain=v1", "--untracked-files=normal"])):
        try:
            run = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True,
                                 timeout=10, env=env, check=False)
            result[name] = run.stdout.strip() if run.returncode == 0 else None
        except (OSError, subprocess.TimeoutExpired):
            result[name] = None
    return result


def terminate_process_group(process: subprocess.Popen[Any]) -> None:
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    process.wait(timeout=10)


def execute_one(root: Path, command: dict[str, Any], log: Path) -> dict[str, Any]:
    start = dt.datetime.now(dt.timezone.utc).isoformat()
    clock = time.monotonic()
    result: dict[str, Any] = {"argv": command["argv"], "cwd": command["cwd"],
                              "started_at": start, "timeout_seconds": command["timeout_seconds"],
                              "log": log.name, "exit_code": None}
    fd = os.open(log, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    state = "BLOCKED"
    process: subprocess.Popen[Any] | None = None
    try:
        with os.fdopen(fd, "wb") as stream:
            process = subprocess.Popen(command["argv"], cwd=safe_path(root, command["cwd"]),
                                       stdout=stream, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                       shell=False, start_new_session=True)
            while process.poll() is None:
                if time.monotonic() - clock > command["timeout_seconds"]:
                    terminate_process_group(process)
                    state = "TIMEOUT"
                    break
                if log.stat().st_size > MAX_LOG_BYTES:
                    terminate_process_group(process)
                    state = "OUTPUT_LIMIT"
                    break
                time.sleep(0.1)
            else:
                state = "PASS" if process.returncode == 0 else "FAIL"
            result["exit_code"] = process.returncode
        if log.stat().st_size > MAX_LOG_BYTES:
            with log.open("r+b") as stream:
                stream.truncate(MAX_LOG_BYTES)
            state = "OUTPUT_LIMIT"
    except KeyboardInterrupt:
        if process is not None and process.poll() is None:
            terminate_process_group(process)
        raise
    except (OSError, subprocess.SubprocessError, ConfigurationError) as exc:
        if process is not None and process.poll() is None:
            terminate_process_group(process)
        result["error"] = f"{type(exc).__name__}: command could not complete; inspect local configuration"
        state = "BLOCKED"
    result.update(state=state, duration_seconds=round(time.monotonic() - clock, 3),
                  log_sha256=sha256_file(log), log_bytes=log.stat().st_size)
    return result


def expand_command(command: dict, evidence_dir: str, run_id: str) -> dict:
    command = copy.deepcopy(command)
    command["argv"] = [x.replace("{evidence_dir}", evidence_dir).replace("{run_id}", run_id) for x in command["argv"]]
    return command


def test_report_result(root: Path, spec: dict, evidence_dir: str) -> dict:
    """Require a new report in this invocation's exclusive evidence directory.

    Nonzero executed cases and successful commands are necessary, not sufficient
    evidence of meaningful tests; QA owns completeness and independent review.
    """
    try:
        path = safe_path(root, spec["path"].replace("{evidence_dir}", evidence_dir))
        directory = safe_path(root, evidence_dir)
        if not path.is_relative_to(directory) or not path.is_file() or path.stat().st_size > MAX_JSON_BYTES:
            raise ConfigurationError("Missing, unsafe or oversized fresh test report")
        if spec["format"] == "counts-json":
            data = read_json(path)
            keys = {"executed", "failures", "errors", "skipped"}
            if not isinstance(data, dict) or set(data) != keys or any(type(data[k]) is not int or data[k] < 0 for k in keys):
                raise ConfigurationError("counts-json requires exactly four nonnegative integer counts")
            if data["failures"] + data["errors"] > data["executed"]:
                raise ConfigurationError("Invalid test count relationships")
        else:
            raw = path.read_bytes()
            if b"<!DOCTYPE" in raw.upper() or b"<!ENTITY" in raw.upper():
                raise ConfigurationError("DTD/entity declarations are not permitted in test reports")
            tree = ET.fromstring(raw)
            if tree.tag not in {"testsuite", "testsuites"}:
                raise ConfigurationError("Expected a JUnit testsuite/testsuites root")
            cases = list(tree.iter("testcase"))
            data = {"executed": sum(c.find("skipped") is None for c in cases),
                    "skipped": sum(c.find("skipped") is not None for c in cases),
                    "failures": sum(c.find("failure") is not None for c in cases),
                    "errors": sum(c.find("error") is not None for c in cases)}
        passed = data["executed"] >= spec["minimum_executed"] and data["failures"] == data["errors"] == 0
        return {"state": "PASS" if passed else "FAIL", **data,
                "report_sha256": sha256_file(path), "path": path.relative_to(root).as_posix()}
    except (ConfigurationError, OSError, ET.ParseError, ValueError) as exc:
        return {"state": "BLOCKED", "error": str(exc)}


def run_commands(root: Path, execute: bool, group: str = "all", phase: str = "change") -> tuple[dict[str, Any], int]:
    if not execute:
        return {"state": "BLOCKED", "error": "Explicit --execute-reviewed-commands is required; configured commands execute arbitrary code."}, 2
    if os.name != "posix":
        return {"state": "BLOCKED", "error": "Command execution supports Linux/macOS process-group control; audit/install do not require it."}, 2
    audit, data = audit_repository(root)
    if audit["errors"]:
        return audit, 1
    profile = data["repository"]
    combined = profile["integration_mode"] == "combined"
    if not combined and (group.startswith("qa_") or phase == "release"):
        return {"state": "BLOCKED", "error": "A component-only configuration cannot pass a combined QA/release gate"}, 1
    commands = data["commands"]
    checks = list(commands["checks"]) if group in {"all", "engineering"} else []
    gate_ids = []
    if combined:
        if group == "all":
            gate_ids = ["qa_lint"] + (["qa_release"] if phase == "release" else [])
        elif group in {"qa_lint", "qa_release"}:
            gate_ids = [group]
    for gid in gate_ids:
        gate = data["integration"]["gates"][gid]
        checks.append({"id": gid, "state": "configured", "commands": [gate["command"]]})
    evidence_root = safe_path(root, profile["evidence_root"])
    evidence_root.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(evidence_root, 0o700)
    run_id = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex
    directory = evidence_root / run_id
    directory.mkdir(mode=0o700)
    evidence_dir = directory.relative_to(root).as_posix()
    report: dict[str, Any] = {"kind": "engineering-command-execution", "package_version": VERSION,
         "run_id": run_id, "group": group, "phase": phase, "integration_mode": profile["integration_mode"],
         "task_ids": [record["task_id"] for record in data["traceability"]["records"]],
         "commands_config_sha256": sha256_file(root / ".engineering/commands.json"),
         "repository_config_sha256": sha256_file(root / ".engineering/repository.json"),
         "integration_manifest_sha256": data.get("integration", {}).get("manifest_sha256"),
         "before": git_snapshot(root), "setup": [], "checks": [],
         "limits": "Command/report outcomes only; no product acceptance, approval authenticity, test adequacy or server policy is certified."}
    serial = 0
    setup_ok = True
    for command in commands["setup_commands"]:
        serial += 1
        outcome = execute_one(root, expand_command(command, evidence_dir, run_id), directory / f"{serial:03}-setup.log")
        report["setup"].append(outcome)
        if outcome["state"] != "PASS":
            setup_ok = False
            break
    for check in checks:
        result: dict[str, Any] = {"id": check["id"], "commands": []}
        if not setup_ok:
            result["state"] = "NOT_RUN_SETUP_FAILED"
        elif check["state"] == "not_applicable":
            result.update(state="NOT_APPLICABLE", rationale=check["rationale"], approval_ref=check["approval_ref"])
        else:
            for command in check["commands"]:
                serial += 1
                actual = expand_command(command, evidence_dir, run_id)
                result["commands"].append(execute_one(root, actual, directory / f"{serial:03}-{check['id']}.log"))
            result["state"] = "PASS" if result["commands"] and all(c["state"] == "PASS" for c in result["commands"]) else "FAIL_OR_BLOCKED"
            if check["id"] == "tests":
                result["test_report"] = test_report_result(root, check["test_report"], evidence_dir)
                if result["test_report"]["state"] != "PASS":
                    result["state"] = "FAIL_OR_BLOCKED"
        report["checks"].append(result)
    report["after"] = git_snapshot(root)
    passed = setup_ok and bool(report["checks"]) and all(c["state"] in {"PASS", "NOT_APPLICABLE"} for c in report["checks"])
    success = "CONFIGURED_GATE_GROUP_PASS" if combined else "COMPONENT_COMMANDS_PASS_NOT_PRODUCT_DONE"
    report["state"] = success if passed else "FAIL_OR_BLOCKED"
    report["report_path"] = (directory / "report.json").relative_to(root).as_posix()
    with os.fdopen(os.open(directory / "report.json", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w", encoding="utf-8") as stream:
        json.dump(report, stream, indent=2)
        stream.write("\n")
    return report, 0 if passed else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    audit = sub.add_parser("audit", help="Validate structure/applicability; do not execute application code")
    audit.add_argument("--repo", type=Path, default=Path.cwd())
    audit.add_argument("--advisory", action="store_true", help="Nonblocking inventory, never a compliance pass")
    run = sub.add_parser("run", help="Execute explicitly reviewed command definitions and write protected evidence")
    run.add_argument("--repo", type=Path, default=Path.cwd())
    run.add_argument("--execute-reviewed-commands", action="store_true")
    run.add_argument("--group", choices=["all", "engineering", "qa_lint", "qa_release"], default="all")
    run.add_argument("--phase", choices=["change", "release"], default="change")
    identity = sub.add_parser("new-id", help="Generate a collision-resistant local ID; does not create a work item")
    identity.add_argument("--prefix", default="TASK", choices=["TASK", "EXC", "ADR", "PLAN"])
    verify = sub.add_parser("verify-package", help="Verify local distribution checksums, not publisher identity")
    verify.add_argument("--package", type=Path, default=PACKAGE)
    args = parser.parse_args()
    try:
        if args.action == "new-id":
            print(f"{args.prefix}-{uuid.uuid4()}")
            return 0
        if args.action == "verify-package":
            report = verify_package(args.package)
            code = 1 if report["errors"] else 0
        else:
            root = args.repo.expanduser().resolve()
            if not root.is_dir():
                raise ConfigurationError("Selected repository directory does not exist")
            if args.action == "audit":
                report, _ = audit_repository(root)
                code = 1 if report["errors"] else 0
                if args.advisory:
                    report.update(state="ADVISORY_ONLY_NOT_ENFORCED", blocking_findings=len(report["errors"]))
                    code = 0
            else:
                report, code = run_commands(root, args.execute_reviewed_commands, args.group, args.phase)
        print(json.dumps(report, indent=2))
        return code
    except KeyboardInterrupt:
        print(json.dumps({"state": "CANCELLED", "error": "Execution interrupted; no completed run report is asserted."}, indent=2))
        return 130
    except (ConfigurationError, OSError, ValueError, RecursionError) as exc:
        print(json.dumps({"state": "BLOCKED", "error": str(exc)}, indent=2))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
