"""Paired immutable sample checks and version-pinned local-sandbox bundles."""

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.http import Http404
from django.shortcuts import get_object_or_404

from .content import extract_fields
from .delivery import mapped_payload, validate_mapping
from .models import (
    ConfigurationComparison,
    DestinationMapping,
    EvaluationVersion,
    PublishedBundle,
    SchemaVersion,
)
from .permissions import sources_for, system_can_read
from .qualification import evaluate_rules, valid_value
from .schema_lifecycle import permitted_samples
from .services import audit
from .systems import owned_system, relevance_decision


def evaluate_sample(configuration, sample, types):
    config = configuration.config
    definition = get_object_or_404(
        SchemaVersion,
        schema_id=config.get("schema_id"),
        number=config.get("schema_version"),
        schema__system=configuration.system,
        schema__workspace_id=configuration.system.workspace_id,
    )
    parsed = sample.analysis or sample.content_version
    if parsed.status not in {"parsed", "partial"}:
        raise ValidationError("Sample parsing/security validation unavailable.")
    fields = extract_fields(parsed, definition.fields)
    relevance = relevance_decision(parsed.body, config["relevance"])
    if types and not set(types).intersection(config.get("type_keys", [])):
        relevance["decision"] = "exclude"
    elif not types and config.get("unknown_type", "review") != "include":
        relevance["decision"] = config.get("unknown_type", "review")
    qualification = evaluate_rules(
        fields, definition.fields, config["qualification"], configuration.number
    )
    missing = [
        f["key"]
        for f in definition.fields
        if f["required"] and fields[f["key"]]["status"] != "known"
    ]
    if missing and qualification["decision"] != "fail":
        qualification.update(
            decision=config["qualification"]["unknown"],
            reason="Required fields incomplete",
        )
    return {
        "fields": fields,
        "routing": relevance["decision"],
        "qualification": qualification,
        "schema_version": definition.pk,
        "configuration": configuration.number,
    }


@transaction.atomic
def compare_configurations(
    actor, workspace_id, system_id, left_number, right_number, evaluation_version_id
):
    system = owned_system(actor, workspace_id, system_id)
    left = get_object_or_404(system.configurations, number=left_number)
    right = get_object_or_404(system.configurations, number=right_number)
    version = get_object_or_404(
        EvaluationVersion,
        pk=evaluation_version_id,
        sample_set__workspace_id=workspace_id,
        sample_set__schema__system=system,
    )
    samples = permitted_samples(actor, version)
    if not samples:
        raise ValidationError("Empty evaluation cannot pass publication.")
    comparison = ConfigurationComparison.objects.create(
        workspace_id=workspace_id,
        system=system,
        left=left,
        right=right,
        evaluation_version=version,
        actor=actor,
    )
    rows = []
    try:
        for sample in samples:
            record = sample.content_version.record
            if not sources_for(actor, workspace_id).filter(
                pk=record.source_id
            ).exists() or not system_can_read(system, record.source):
                raise PermissionDenied("Sample source access is unavailable.")
            types = list(record.type_assignments.values_list("key", flat=True))
            a, b = (
                evaluate_sample(left, sample, types),
                evaluate_sample(right, sample, types),
            )
            definition = SchemaVersion.objects.get(pk=b["schema_version"])
            expected = sample.expected_fields
            definitions = {f["key"]: f for f in definition.fields}
            field_errors = [
                key
                for key in set(expected) | set(b["fields"])
                if key not in definitions
                or key not in expected
                or key not in b["fields"]
                or (
                    expected[key] is not None
                    and not valid_value(expected[key], definitions[key])
                )
                or b["fields"][key]["value"] != expected[key]
                or b["fields"][key]["status"]
                != ("unknown" if expected[key] is None else "known")
            ]
            passed = (
                not field_errors
                and b["routing"] == sample.expected_routing
                and b["qualification"]["decision"] == sample.expected_qualification
            )
            rows.append(
                {
                    "sample": sample.pk,
                    "content_version": str(sample.content_version_id),
                    "analysis": str(sample.analysis_id) if sample.analysis_id else None,
                    "classification_version": record.classification_version,
                    "left": a,
                    "right": b,
                    "field_errors": sorted(field_errors),
                    "false_exclusion": b["routing"] == "exclude"
                    and sample.expected_routing == "include",
                    "decision_changed": (a["routing"], a["qualification"]["decision"])
                    != (b["routing"], b["qualification"]["decision"]),
                    "passed": passed,
                }
            )
        comparison.state = "completed"
        comparison.passed = bool(rows) and all(row["passed"] for row in rows)
    except (ValidationError, KeyError, ValueError, Http404):
        comparison.state, comparison.passed = "failed", False
    comparison.results = rows
    comparison.save(update_fields=["state", "passed", "results"])
    audit(
        actor,
        workspace_id,
        "configuration.samples_compared",
        comparison.pk,
        {
            "state": comparison.state,
            "passed": comparison.passed,
            "left": left.number,
            "right": right.number,
            "delivery": False,
        },
    )
    return comparison


def validate_comparison(actor, comparison):
    if (
        comparison.state != "completed"
        or not comparison.passed
        or not comparison.results
    ):
        raise ValidationError(
            "A completed, passing nonempty sample comparison is required."
        )
    version = comparison.evaluation_version
    sample_set = version.sample_set
    sample_set.refresh_from_db()
    if sample_set.version != version.number:
        raise ValidationError("Sample labels changed; rerun comparison.")
    samples = permitted_samples(actor, version)
    pins = {sample.pk: sample for sample in samples}
    if set(pins) != {row["sample"] for row in comparison.results}:
        raise ValidationError("Sample comparison is incomplete.")
    for row in comparison.results:
        if (
            pins[row["sample"]].content_version.record.classification_version
            != row["classification_version"]
        ):
            raise ValidationError("Sample classification changed; rerun comparison.")


@transaction.atomic
def publish_bundle(
    actor, workspace_id, system_id, expected_configuration, mapping_id, comparison_id
):
    system = owned_system(actor, workspace_id, system_id)
    if system.state == "retired" or system.config_version != expected_configuration:
        raise ValidationError("Publish the current non-retired configuration only.")
    config = get_object_or_404(system.configurations, number=expected_configuration)
    comparison = get_object_or_404(
        ConfigurationComparison,
        pk=comparison_id,
        workspace_id=workspace_id,
        system=system,
        right=config,
    )
    validate_comparison(actor, comparison)
    mapping = get_object_or_404(
        DestinationMapping, pk=mapping_id, workspace_id=workspace_id, system=system
    )
    if (
        str(mapping.schema_version.schema_id) != config.config.get("schema_id")
        or mapping.schema_version.number != config.config.get("schema_version")
        or str(mapping.contract.destination_id) != config.config.get("destination_id")
    ):
        raise ValidationError(
            "Schema/mapping/destination pins do not match the configuration."
        )
    if (
        mapping.schema_version.schema.state != "published"
        or mapping.contract.destination.state != "test_passed"
        or mapping.contract.mode != "local_sandbox"
    ):
        raise ValidationError(
            "Published schema and tested local sandbox contract are required."
        )
    if (
        mapping.contract.destination.contracts.order_by("-number").first().pk
        != mapping.contract_id
    ):
        raise ValidationError("Destination schema changed; update mapping and samples.")
    validate_mapping(mapping.schema_version, mapping.contract, mapping.entries)
    permitted = {
        str(pk) for pk in sources_for(actor, workspace_id).values_list("pk", flat=True)
    }
    if not set(config.config.get("source_ids", [])) <= permitted:
        raise PermissionDenied("Configured source access is no longer permitted.")
    for sample in permitted_samples(actor, comparison.evaluation_version):
        from types import SimpleNamespace

        evaluated = evaluate_sample(
            config,
            sample,
            list(
                sample.content_version.record.type_assignments.values_list(
                    "key", flat=True
                )
            ),
        )
        if (
            evaluated["routing"] != "include"
            or evaluated["qualification"]["decision"] != "pass"
        ):
            continue
        mapped_payload(
            SimpleNamespace(
                system_id=system.pk,
                workspace_id=workspace_id,
                schema_version_id=mapping.schema_version_id,
                fields=evaluated["fields"],
            ),
            mapping,
        )
    latest = system.bundles.order_by("number").last()
    bundle = PublishedBundle.objects.create(
        workspace_id=workspace_id,
        system=system,
        number=(latest.number + 1 if latest else 1),
        configuration=config,
        mapping=mapping,
        comparison=comparison,
        actor=actor,
    )
    system.active_bundle, system.state = bundle, "active"
    system.save(update_fields=["active_bundle", "state"])
    audit(
        actor,
        workspace_id,
        "configuration.local_bundle_published",
        system.pk,
        {
            "bundle": str(bundle.pk),
            "version": bundle.number,
            "external_delivery": False,
        },
    )
    return bundle


@transaction.atomic
def rollback_bundle(actor, workspace_id, system_id, bundle_id, reason):
    system = owned_system(actor, workspace_id, system_id)
    bundle = get_object_or_404(
        PublishedBundle, pk=bundle_id, system=system, workspace_id=workspace_id
    )
    if (
        system.state == "retired"
        or not isinstance(reason, str)
        or not reason.strip()
        or len(reason) > 500
    ):
        raise ValidationError("Rollback requires a non-retired system and reason.")
    validate_comparison(actor, bundle.comparison)
    if (
        bundle.mapping.contract.destination.contracts.order_by("-number").first().pk
        != bundle.mapping.contract_id
        or bundle.mapping.contract.destination.state != "test_passed"
        or bundle.mapping.schema_version.schema.state != "published"
    ):
        raise ValidationError(
            "Historical mapping/schema/destination no longer validates; repair before rollback."
        )
    system.active_bundle, system.state = bundle, "active"
    system.save(update_fields=["active_bundle", "state"])
    audit(
        actor,
        workspace_id,
        "configuration.bundle_rolled_back",
        system.pk,
        {"bundle": str(bundle.pk), "reason": reason},
    )
    return bundle
