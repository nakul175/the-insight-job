"""Scoped system drafts. Publication and execution never follow a draft save."""

import re

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.shortcuts import get_object_or_404

from workspaces.models import Workspace

from .models import (
    ConsumerSystem,
    ContentSchema,
    SystemConfiguration,
    SystemGrant,
    SystemSourceGrant,
)
from .permissions import member, sources_for, systems_for
from .services import audit


def owned_system(actor, workspace_id, system_id):
    Workspace.objects.select_for_update().get(pk=workspace_id)
    membership = member(actor, workspace_id, write=True)
    system = get_object_or_404(
        systems_for(actor, workspace_id).select_for_update(), pk=system_id
    )
    if membership.role != "administrator" and system.owner_id != actor.pk:
        raise PermissionDenied("Only the scoped owner can configure this system.")
    return system


def current_config(system):
    row = system.configurations.filter(number=system.config_version).first()
    return row.config if row else {}


def validate_relevance(value):
    if not isinstance(value, dict) or set(value) != {
        "all",
        "none",
        "examples",
        "unknown",
    }:
        raise ValidationError("Relevance requires all, none, examples and unknown.")
    for key in ("all", "none"):
        terms = value[key]
        if (
            not isinstance(terms, list)
            or len(terms) > 30
            or any(
                not isinstance(t, str) or not t.strip() or len(t) > 100 for t in terms
            )
        ):
            raise ValidationError(f"Relevance {key}: use up to 30 nonempty terms.")
    required = {t.strip().casefold() for t in value["all"]}
    excluded = {t.strip().casefold() for t in value["none"]}
    if required & excluded:
        raise ValidationError("Relevance criteria require and exclude the same term.")
    if value["unknown"] not in {"review", "exclude"}:
        raise ValidationError("Unknown relevance must explicitly review or exclude.")
    examples = value["examples"]
    if not isinstance(examples, list) or not examples or len(examples) > 30:
        raise ValidationError("Provide 1–30 relevance examples.")
    for example in examples:
        if (
            not isinstance(example, dict)
            or set(example) != {"text", "expected"}
            or not isinstance(example["text"], str)
            or len(example["text"]) > 10000
            or example["expected"] not in {"include", "exclude", "review"}
        ):
            raise ValidationError(
                "Each relevance example requires text and expected include/exclude/review."
            )
        if (
            relevance_decision(example["text"], value)["decision"]
            != example["expected"]
        ):
            raise ValidationError(
                "Relevance example contradicts the configured criteria."
            )
    return value


def relevance_decision(text, config):
    if not text.strip():
        return {
            "decision": config["unknown"],
            "method": "literal-terms-v1",
            "evidence": [],
        }
    normalized = text.casefold()
    hits = [term for term in config["all"] if term.casefold().strip() in normalized]
    vetoes = [term for term in config["none"] if term.casefold().strip() in normalized]
    return {
        "decision": "include"
        if len(hits) == len(config["all"]) and not vetoes
        else "exclude",
        "method": "literal-terms-v1",
        "evidence": hits + vetoes,
    }


def validate_config(actor, workspace_id, config):
    if not isinstance(config, dict) or set(config) - {
        "source_ids",
        "type_keys",
        "unknown_type",
        "relevance",
        "schema_id",
        "qualification",
        "destination_id",
    }:
        raise ValidationError("Unsupported system configuration fields.")
    ids = config.get("source_ids", [])
    if (
        not isinstance(ids, list)
        or len(ids) > 100
        or any(not isinstance(x, str) for x in ids)
    ):
        raise ValidationError("Select at most 100 permitted sources.")
    permitted = {
        str(x)
        for x in sources_for(actor, workspace_id)
        .filter(state__in=["draft", "active", "paused"])
        .values_list("pk", flat=True)
    }
    if not set(ids) <= permitted:
        raise ValidationError(
            "A selected source is unavailable or no longer permitted."
        )
    keys = config.get("type_keys", [])
    if (
        not isinstance(keys, list)
        or len(keys) > 50
        or any(
            not isinstance(x, str) or not re.fullmatch(r"[a-z][a-z0-9_-]{0,59}", x)
            for x in keys
        )
    ):
        raise ValidationError("Select valid published type keys.")
    available = set(
        ContentSchema.objects.filter(
            workspace_id=workspace_id,
            scope="type",
            state="published",
            published_version__isnull=False,
        ).values_list("key", flat=True)
    )
    if not set(keys) <= available:
        raise ValidationError("A selected type is unpublished or retired.")
    if config.get("unknown_type") not in {"review", "include", "exclude"}:
        raise ValidationError(
            "Choose review, include or exclude for Unknown content types."
        )
    if "relevance" in config:
        validate_relevance(config["relevance"])
    # Later qualification/destination stories own these contracts; don't accept unchecked draft IDs.
    if any(config.get(key) for key in ("schema_id", "qualification", "destination_id")):
        raise ValidationError(
            "Use validated schema, qualification and destination setup before attaching these dependencies."
        )
    return config


@transaction.atomic
def create_system(actor, workspace_id, name, purpose):
    membership = member(actor, workspace_id, write=True)
    if (
        not isinstance(name, str)
        or not name.strip()
        or len(name) > 120
        or not isinstance(purpose, str)
        or not purpose.strip()
        or len(purpose) > 500
    ):
        raise ValidationError(
            "Provide a name (120 characters) and purpose (500 characters)."
        )
    system = ConsumerSystem.objects.create(
        workspace_id=workspace_id,
        owner=actor,
        name=name.strip(),
        purpose=purpose.strip(),
    )
    SystemGrant.objects.create(membership=membership, system=system)
    audit(actor, workspace_id, "system.created", system.pk, {})
    return system


@transaction.atomic
def save_configuration(actor, workspace_id, system_id, expected_version, config):
    system = owned_system(actor, workspace_id, system_id)
    if expected_version != system.config_version:
        raise ValidationError("System draft changed; reload before saving.")
    if system.state == "retired":
        raise ValidationError("Retired systems cannot accept drafts.")
    validate_config(actor, workspace_id, config)
    from .models import WorkItem

    selected = config.get("source_ids", [])
    removed = system.systemsourcegrant_set.filter(active=True).exclude(
        source_id__in=selected
    )
    removed_ids = list(removed.values_list("source_id", flat=True))
    removed.update(active=False)
    WorkItem.objects.filter(
        system=system,
        source_id__in=removed_ids,
        state__in=["queued", "running", "retry"],
    ).update(state="blocked", reason="Source removed from system scope")
    for source_id in selected:
        grant, created = SystemSourceGrant.objects.get_or_create(
            system=system, source_id=source_id
        )
        if not created and not grant.active:
            raise ValidationError(
                "Source system access was revoked; an administrator must restore the grant."
            )
    system.config_version += 1
    SystemConfiguration.objects.create(
        system=system, number=system.config_version, config=config, actor=actor
    )
    system.save(update_fields=["config_version"])
    audit(
        actor,
        workspace_id,
        "system.draft_saved",
        system.pk,
        {"version": system.config_version},
    )
    return system


def activation_gaps(system):
    config = current_config(system)
    return [
        label
        for key, label in [
            ("schema_id", "Published system schema"),
            ("relevance", "Validated relevance logic"),
            ("qualification", "Published qualification rules"),
            ("destination_id", "Tested sandbox destination"),
        ]
        if not config.get(key)
    ]


@transaction.atomic
def request_activation(actor, workspace_id, system_id):
    system = owned_system(actor, workspace_id, system_id)
    gaps = activation_gaps(system)
    if gaps:
        raise ValidationError("Activation blocked: " + "; ".join(gaps))
    raise ValidationError(
        "Activation requires published, validated configuration; a draft cannot activate."
    )


@transaction.atomic
def duplicate_system(actor, workspace_id, system_id):
    original = get_object_or_404(systems_for(actor, workspace_id), pk=system_id)
    duplicate = create_system(
        actor,
        workspace_id,
        original.name[:110] + " (copy)",
        original.purpose or "Review copied configuration",
    )
    config = current_config(original)
    # Copy only non-sensitive logic, never source/type grants, dependency IDs or secrets.
    copied = {
        "source_ids": [],
        "type_keys": [],
        "unknown_type": config.get("unknown_type", "review"),
    }
    if "relevance" in config:
        copied["relevance"] = config["relevance"]
    duplicate = save_configuration(actor, workspace_id, duplicate.pk, 0, copied)
    audit(
        actor,
        workspace_id,
        "system.duplicated",
        duplicate.pk,
        {"original": str(original.pk)},
    )
    return duplicate
