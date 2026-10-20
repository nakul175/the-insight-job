"""Typed deterministic rules. No timezone/currency/semantic guessing."""

import math
import operator as comparisons
from datetime import datetime

from django.core.exceptions import ValidationError


def valid_value(value, field):
    kind = field["type"]
    if value is None:
        return not field["required"]
    if kind == "boolean":
        return type(value) is bool
    if kind == "integer":
        return type(value) is int and abs(value) <= 10**18
    if kind == "number":
        return (type(value) is int and abs(value) <= 10**100) or (
            type(value) is float and math.isfinite(value)
        )
    if not isinstance(value, str) or len(value) > 2000:
        return False
    if kind == "enum":
        return value in field["allowed_values"]
    if kind == "date":
        try:
            datetime.fromisoformat(value)
        except ValueError:
            return False
    return True


def validate_rules(config, fields):
    if (
        not isinstance(config, dict)
        or set(config) != {"group", "unknown", "rules"}
        or config["group"] not in ("all", "any")
        or config["unknown"] not in ("review", "enrich")
    ):
        raise ValidationError(
            "Rules require all/any group, review/enrich unknown policy and rules."
        )
    rules = config["rules"]
    if not isinstance(rules, list) or not 1 <= len(rules) <= 50:
        raise ValidationError("Provide 1–50 deterministic rules.")
    definitions = {field["key"]: field for field in fields}
    for index, rule in enumerate(rules):
        if (
            not isinstance(rule, dict)
            or set(rule) != {"field", "operator", "value", "unit", "hard_fail"}
            or not isinstance(rule["field"], str)
            or rule["field"] not in definitions
            or type(rule["hard_fail"]) is not bool
        ):
            raise ValidationError(
                f"Rule {index}: field/operator/value/unit/hard_fail required."
            )
        field = definitions[rule["field"]]
        operations = {"eq", "ne"}
        if field["type"] in {"integer", "number"}:
            operations |= {"gt", "gte", "lt", "lte"}
        if field["type"] in {"text", "date"}:
            operations |= {"before", "on_or_before", "after", "on_or_after"}
        if field["type"] == "text":
            operations |= {"contains"}
        if (
            not isinstance(rule["operator"], str)
            or rule["operator"] not in operations
            or rule["value"] is None
            or not valid_value(rule["value"], field)
            or not isinstance(rule["unit"], str)
            or len(rule["unit"]) > 30
        ):
            raise ValidationError(
                f"Rule {index}: operator/value is incompatible with field type."
            )
    return config


def evaluate_rules(fields, definitions, config, configuration_number):
    validate_rules(config, definitions)
    results = []
    definitions_by_key = {field["key"]: field for field in definitions}
    for rule in config["rules"]:
        fact = fields.get(rule["field"], {})
        value, comparison = fact.get("value"), rule["value"]
        status = "review"
        reason = "Missing or conflicting evidence"
        source_timezone = None
        if (
            fact.get("status") == "known"
            and value is not None
            and valid_value(value, definitions_by_key[rule["field"]])
        ):
            if fact.get("unit", "") != rule["unit"]:
                reason = "Units/currency differ; no permitted conversion is configured"
            else:
                operator = rule["operator"]
                try:
                    if definitions_by_key[rule["field"]][
                        "type"
                    ] == "date" or operator in {
                        "before",
                        "on_or_before",
                        "after",
                        "on_or_after",
                    }:
                        left, right = (
                            datetime.fromisoformat(value),
                            datetime.fromisoformat(comparison),
                        )
                        if (
                            left.tzinfo is None
                            or right.tzinfo is None
                            or left.utcoffset() is None
                            or right.utcoffset() is None
                        ):
                            raise ValueError(
                                "Date timezone unavailable; Review required"
                            )
                        source_timezone = str(left.tzinfo)
                        passed = {
                            "eq": comparisons.eq,
                            "ne": comparisons.ne,
                            "before": comparisons.lt,
                            "on_or_before": comparisons.le,
                            "after": comparisons.gt,
                            "on_or_after": comparisons.ge,
                        }[operator](left, right)
                    else:
                        passed = {
                            "eq": comparisons.eq,
                            "ne": comparisons.ne,
                            "gt": comparisons.gt,
                            "gte": comparisons.ge,
                            "lt": comparisons.lt,
                            "lte": comparisons.le,
                            "contains": comparisons.contains,
                        }[operator](value, comparison)
                    status = "pass" if passed else "fail"
                    reason = "Deterministic comparison"
                except (ValueError, TypeError, KeyError):
                    reason = (
                        "Invalid comparison or timezone unavailable; Review required"
                    )
        results.append(
            {
                **rule,
                "input": value,
                "result": status,
                "reason": reason,
                "configuration_version": configuration_number,
                "evidence": fact.get("candidates", []),
                "source_timezone": source_timezone,
            }
        )
        if status == "fail" and rule["hard_fail"]:
            return {
                "decision": "fail",
                "reason": "Hard-fail rule stopped this system",
                "rules": results,
                "group": config["group"],
            }
    statuses = [row["result"] for row in results]
    # Unknown facts remain explicit even when another rule in an any-group passes.
    if "review" in statuses:
        decision = config["unknown"]
    elif config["group"] == "all":
        decision = "pass" if all(s == "pass" for s in statuses) else "fail"
    else:
        decision = "pass" if "pass" in statuses else "fail"
    return {
        "decision": decision,
        "reason": "Grouped deterministic evaluation",
        "rules": results,
        "group": config["group"],
    }
