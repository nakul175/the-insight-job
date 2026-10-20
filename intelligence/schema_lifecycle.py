"""Immutable labelled samples and sample-gated schema publication."""

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.shortcuts import get_object_or_404

from workspaces.models import Workspace

from .content import extract_fields, schemas_for, validate_fields
from .models import (
    ContentVersion,
    EvaluationSample,
    EvaluationSet,
    EvaluationVersion,
    ExtractionJob,
    SchemaCheck,
    SystemConfiguration,
)
from .permissions import member, records_for, system_can_read, systems_for
from .services import audit


def writable_schema(actor, workspace_id, schema_id):
    Workspace.objects.select_for_update(no_key=True).get(pk=workspace_id)
    membership = member(actor, workspace_id, write=True)
    schema = get_object_or_404(
        schemas_for(actor, workspace_id).select_for_update(), pk=schema_id
    )
    if membership.role != "administrator" and schema.owner_id != actor.pk:
        raise PermissionDenied("Schema owner required.")
    return schema


@transaction.atomic
def save_samples(
    actor,
    workspace_id,
    schema_id,
    name,
    samples,
    sample_set_id=None,
    expected_version=0,
):
    schema = writable_schema(actor, workspace_id, schema_id)
    if (
        not isinstance(name, str)
        or not name.strip()
        or len(name) > 120
        or not isinstance(samples, list)
        or not 1 <= len(samples) <= 100
    ):
        raise ValidationError("Provide a sample set name and 1–100 labelled samples.")
    sample_set = (
        get_object_or_404(
            EvaluationSet.objects.select_for_update(),
            pk=sample_set_id,
            workspace_id=workspace_id,
            schema=schema,
        )
        if sample_set_id
        else EvaluationSet.objects.create(
            workspace_id=workspace_id, schema=schema, owner=actor, name=name
        )
    )
    if sample_set.version != expected_version:
        raise ValidationError("Sample labels changed; reload before saving.")
    version = EvaluationVersion.objects.create(
        sample_set=sample_set, number=sample_set.version + 1, actor=actor
    )
    visible = records_for(actor, workspace_id)
    seen = set()
    for item in samples:
        if not isinstance(item, dict) or set(item) != {
            "content_version",
            "fields",
            "routing",
            "qualification",
        }:
            raise ValidationError(
                "Each sample requires content_version, fields, routing and qualification."
            )
        try:
            revision = get_object_or_404(
                ContentVersion.objects.filter(record__in=visible),
                pk=item["content_version"],
            )
        except (ValueError, ValidationError) as error:
            raise ValidationError("Invalid content version.") from error
        if revision.pk in seen:
            raise ValidationError("Duplicate sample content version.")
        seen.add(revision.pk)
        if schema.system_id and not system_can_read(
            schema.system, revision.record.source
        ):
            raise PermissionDenied("System source access is required for evaluation.")
        if (
            not isinstance(item["fields"], dict)
            or len(item["fields"]) > 50
            or len(str(item["fields"])) > 20000
        ):
            raise ValidationError("Expected fields must be a bounded object.")
        if item["routing"] not in {"include", "exclude", "review"} or item[
            "qualification"
        ] not in {"pass", "fail", "review", "enrich"}:
            raise ValidationError(
                "Explicit expected routing and qualification labels are required."
            )
        analysis = revision.analyses.order_by("-number").first()
        EvaluationSample.objects.create(
            version=version,
            content_version=revision,
            analysis=analysis,
            expected_fields=item["fields"],
            expected_routing=item["routing"],
            expected_qualification=item["qualification"],
        )
    sample_set.version = version.number
    sample_set.name = name
    sample_set.save(update_fields=["version", "name"])
    audit(
        actor,
        workspace_id,
        "samples.version_saved",
        sample_set.pk,
        {"version": version.number, "samples": len(samples)},
    )
    return version


def permitted_samples(actor, version):
    allowed = set(
        records_for(actor, version.sample_set.workspace_id).values_list("pk", flat=True)
    )
    samples = list(
        version.samples.select_related("content_version__record__source", "analysis")
    )
    if not samples or any(
        sample.content_version.record_id not in allowed for sample in samples
    ):
        raise PermissionDenied(
            "All sample content must remain permitted; results are unavailable."
        )
    schema = version.sample_set.schema
    if schema.system_id and any(
        not system_can_read(schema.system, s.content_version.record.source)
        for s in samples
    ):
        raise PermissionDenied("System sample source access was revoked.")
    return samples


@transaction.atomic
def check_schema(actor, workspace_id, schema_id, draft_number, evaluation_version_id):
    schema = writable_schema(actor, workspace_id, schema_id)
    draft = get_object_or_404(schema.versions, number=draft_number)
    validate_fields(draft.fields)
    version = get_object_or_404(
        EvaluationVersion,
        pk=evaluation_version_id,
        sample_set__schema=schema,
        sample_set__workspace_id=workspace_id,
    )
    samples = permitted_samples(actor, version)
    results = []
    for sample in samples:
        actual = extract_fields(sample.analysis or sample.content_version, draft.fields)
        expected = sample.expected_fields
        # A complete expected field contract prevents vacuous empty-label passes.
        passed = set(expected) == set(actual) and all(
            actual[key]["value"] == value
            and actual[key]["status"] == ("unknown" if value is None else "known")
            for key, value in expected.items()
        )
        for field in draft.fields:
            value = expected.get(field["key"])
            if value is None:
                continue
            kind = field["type"]
            valid_label = (
                (type(value) is bool)
                if kind == "boolean"
                else (type(value) is int)
                if kind == "integer"
                else (type(value) in {int, float})
                if kind == "number"
                else isinstance(value, str)
            )
            passed = passed and valid_label
        passed = passed and all(
            not field["required"] or actual[field["key"]]["status"] == "known"
            for field in draft.fields
        )
        results.append(
            {
                "sample": sample.pk,
                "passed": passed,
                "actual": actual,
                "routing_qualification": "stored labels; not evaluated by a schema field check",
            }
        )
    check = SchemaCheck.objects.create(
        workspace_id=workspace_id,
        schema_version=draft,
        evaluation_version=version,
        actor=actor,
        passed=all(x["passed"] for x in results),
        results=results,
    )
    audit(
        actor,
        workspace_id,
        "schema.samples_checked",
        schema.pk,
        {"check": str(check.pk), "passed": check.passed},
    )
    return check


def dependencies(schema, actor):
    visible = set(systems_for(actor, schema.workspace_id).values_list("pk", flat=True))
    dependencies = []
    if schema.system_id and schema.system.state != "retired":
        dependencies.append(
            {
                "kind": "system schema",
                "id": str(schema.system_id),
                "name": schema.system.name
                if schema.system_id in visible
                else "Restricted dependency",
            }
        )
    for system in schema.workspace.consumersystem_set.exclude(state="retired"):
        latest = SystemConfiguration.objects.filter(
            system=system, number=system.config_version
        ).first()
        configurations = [latest.config] if latest else []
        if system.active_bundle_id:
            configurations.append(system.active_bundle.configuration.config)
        if any(
            config.get("schema_id") == str(schema.pk)
            or schema.key in config.get("type_keys", [])
            for config in configurations
        ):
            dependencies.append(
                {
                    "kind": "system draft",
                    "id": str(system.pk) if system.pk in visible else None,
                    "name": system.name
                    if system.pk in visible
                    else "Restricted dependency",
                }
            )
    if ExtractionJob.objects.filter(
        schema_version__schema=schema, state__in=["queued", "processing"]
    ).exists():
        dependencies.append(
            {"kind": "pending extraction", "name": "Pending extraction jobs"}
        )
    return dependencies


def breaking_changes(old, new):
    old_fields = {f["key"]: f for f in old}
    new_fields = {f["key"]: f for f in new}
    changes = []
    for key, field in old_fields.items():
        if key not in new_fields or any(
            field.get(k) != new_fields[key].get(k)
            for k in ["type", "unit", "allowed_values", "required", "evidence_required"]
        ):
            changes.append(key)
    changes.extend(
        key
        for key in new_fields.keys() - old_fields.keys()
        if new_fields[key]["required"]
    )
    return changes


@transaction.atomic
def publish_schema(actor, workspace_id, schema_id, draft_number, check_id):
    schema = writable_schema(actor, workspace_id, schema_id)
    latest = schema.versions.order_by("-number").first()
    if not latest or latest.number != draft_number or schema.state == "retired":
        raise ValidationError("Publish the current non-retired draft only.")
    check = get_object_or_404(
        SchemaCheck,
        pk=check_id,
        workspace_id=workspace_id,
        schema_version=latest,
        passed=True,
    )
    permitted_samples(actor, check.evaluation_version)
    sample_set = check.evaluation_version.sample_set
    sample_set.refresh_from_db()
    if sample_set.version != check.evaluation_version.number:
        raise ValidationError("Sample labels changed; rerun checks.")
    if schema.published_version:
        old = schema.versions.get(number=schema.published_version)
        changes = breaking_changes(old.fields, latest.fields)
        refs = dependencies(schema, actor)
        if changes and refs:
            raise ValidationError(
                "Breaking fields "
                + ", ".join(changes)
                + "; address dependencies: "
                + "; ".join(x["name"] for x in refs)
            )
    latest.state = "published"
    latest.save(update_fields=["state"])
    schema.state = "published"
    schema.published_version = latest.number
    schema.save(update_fields=["state", "published_version"])
    audit(
        actor,
        workspace_id,
        "schema.published",
        schema.pk,
        {"version": latest.number, "check": str(check.pk)},
    )
    return schema


@transaction.atomic
def retire_schema(actor, workspace_id, schema_id):
    schema = writable_schema(actor, workspace_id, schema_id)
    refs = dependencies(schema, actor)
    if refs:
        raise ValidationError(
            "Retirement blocked: " + "; ".join(x["name"] for x in refs)
        )
    schema.state = "retired"
    schema.save(update_fields=["state"])
    audit(actor, workspace_id, "schema.retired", schema.pk, {})
    return schema
