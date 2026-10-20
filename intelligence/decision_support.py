"""Permission-aware next steps and typed engineering comparison summaries."""

from django.core.exceptions import PermissionDenied, ValidationError
from django.urls import reverse

from .delivery import validate_mapping
from .models import ConfigurationComparison, ContentSchema, DestinationMapping
from .permissions import sources_for, system_can_read
from .publication import validate_comparison
from .qualification import validate_rules
from .systems import current_config


def setup_progress(actor, workspace_id, system):
    config = current_config(system)
    release = reverse("system-release", args=[workspace_id, system.pk])
    steps = []
    allowed = {
        str(source.pk)
        for source in sources_for(actor, workspace_id)
        if system_can_read(system, source)
    }
    sources_valid = (
        bool(config.get("source_ids")) and set(config.get("source_ids", [])) <= allowed
    )
    steps.append(
        {
            "key": "sources",
            "name": "Sources and relevance",
            "complete": sources_valid and bool(config.get("relevance")),
            "url": reverse("system-configure", args=[workspace_id, system.pk]),
            "reason": "Select permitted sources, restore active system source grants, and validate relevance.",
        }
    )
    schema = (
        ContentSchema.objects.filter(
            system=system,
            workspace_id=workspace_id,
            pk=config.get("schema_id"),
            state="published",
        ).first()
        if config.get("schema_id")
        else None
    )
    definition = (
        schema.versions.filter(number=config.get("schema_version")).first()
        if schema
        else None
    )
    steps.append(
        {
            "key": "schema",
            "name": "Published field schema",
            "complete": definition is not None,
            "url": reverse("schemas", args=[workspace_id]),
            "reason": "Choose a published system schema version.",
        }
    )
    rules_valid, reason = False, "Configure qualification rules."
    if definition and config.get("qualification"):
        try:
            validate_rules(config["qualification"], definition.fields)
            rules_valid = True
        except ValidationError as error:
            reason = "; ".join(error.messages)
    steps.append(
        {
            "key": "rules",
            "name": "Qualification rules",
            "complete": rules_valid,
            "url": reverse("qualification", args=[workspace_id, system.pk]),
            "reason": reason,
        }
    )
    destination = (
        system.destinations.filter(
            pk=config.get("destination_id"), state="test_passed"
        ).first()
        if config.get("destination_id")
        else None
    )
    steps.append(
        {
            "key": "destination",
            "name": "Tested local destination",
            "complete": destination is not None,
            "url": reverse("destinations", args=[workspace_id, system.pk]),
            "reason": "Configure and test a local sandbox destination.",
        }
    )
    mapping = (
        DestinationMapping.objects.filter(
            system=system, schema_version=definition, contract__destination=destination
        )
        .order_by("-created_at", "-pk")
        .first()
        if definition and destination
        else None
    )
    mapping_valid, reason = False, "Complete required destination mappings."
    if mapping:
        try:
            validate_mapping(definition, mapping.contract, mapping.entries)
            if (
                destination.contracts.order_by("-number").first().pk
                != mapping.contract_id
            ):
                raise ValidationError(
                    "Destination contract changed; repair the mapping."
                )
            mapping_valid = True
        except ValidationError as error:
            reason = "; ".join(error.messages)
    steps.append(
        {
            "key": "mapping",
            "name": "Destination mapping",
            "complete": mapping_valid,
            "url": release + "#mappings",
            "reason": reason,
        }
    )
    comparison_valid = False
    for comparison in ConfigurationComparison.objects.filter(
        system=system, right__number=system.config_version, passed=True
    ).order_by("-created_at"):
        try:
            validate_comparison(actor, comparison)
        except (PermissionDenied, ValidationError):
            continue
        comparison_valid = True
        break
    steps.append(
        {
            "key": "samples",
            "name": "Labelled comparison gates",
            "complete": comparison_valid,
            "url": release + "#comparisons",
            "reason": "Run a nonempty passing comparison against current permitted labels.",
        }
    )
    published = bool(
        system.active_bundle_id
        and system.active_bundle.configuration.number == system.config_version
        and mapping
        and system.active_bundle.mapping_id == mapping.pk
    )
    steps.append(
        {
            "key": "publish",
            "name": "Publish tested bundle",
            "complete": published,
            "url": release,
            "reason": "Resolve the linked gates, then publish this system's tested bundle.",
        }
    )
    return steps


def comparison_summary(comparison):
    changed = unchanged = unresolved = 0
    errors = []
    for row in comparison.results:
        left, right = row.get("left", {}), row.get("right", {})
        differs = bool(
            row.get("decision_changed") or left.get("fields") != right.get("fields")
        )
        changed += differs
        unchanged += not differs
        unresolved += bool(
            row.get("field_errors")
            or right.get("routing") == "review"
            or right.get("qualification", {}).get("decision") in {"review", "enrich"}
            or not row.get("passed")
        )
        for field in row.get("field_errors", []):
            errors.append(
                {
                    "sample": row["sample"],
                    "field": field,
                    "anchor": f"schema-{right.get('schema_version')}-field-{field}",
                }
            )
        for rule in right.get("qualification", {}).get("rules", []):
            if rule.get("result", rule.get("status", rule.get("outcome"))) in {
                "fail",
                "review",
                "enrich",
                "unknown",
            }:
                errors.append(
                    {
                        "sample": row["sample"],
                        "field": rule.get("field", "rule"),
                        "anchor": "configuration-" + str(comparison.right.number),
                    }
                )
    expected = comparison.evaluation_version.samples.count()
    unresolved += max(0, expected - len(comparison.results))
    return {
        "changed": changed,
        "unchanged": unchanged,
        "unresolved": unresolved,
        "errors": errors,
    }
