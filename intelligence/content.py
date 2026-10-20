"""Versioned content, evidence and explicit schema previews; no model/provider calls."""

import difflib
import hashlib
import math
import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.db.models import F, Q
from django.shortcuts import get_object_or_404

from .models import (
    ClassificationChange,
    ContentAnalysis,
    ContentIdentity,
    ContentRecord,
    ContentSchema,
    ContentType,
    ContentVersion,
    Derivative,
    DuplicateDecision,
    ExtractionJob,
    SchemaVersion,
)
from .permissions import member, records_for, system_can_read, systems_for
from .services import audit


def editable_record(actor, workspace_id, record_id):
    membership = member(actor, workspace_id)
    if membership.role not in {"administrator", "operator", "reviewer"}:
        raise PermissionDenied("Your role cannot correct content.")
    return get_object_or_404(records_for(actor, workspace_id), pk=record_id)


def schemas_for(actor, workspace_id):
    member(actor, workspace_id)
    return ContentSchema.objects.filter(workspace_id=workspace_id).filter(
        Q(system__isnull=True) | Q(system__in=systems_for(actor, workspace_id))
    )


def summary_for(parsed):
    return (
        {
            "text": parsed["segments"][0]["text"][:300],
            "segment_ids": [0],
            "method": "extractive",
        }
        if parsed["segments"]
        else {}
    )


def analysis_quality(status):
    return {"parsed": 3, "partial": 2, "empty": 1}.get(status, 0)


@transaction.atomic
def materialize(capture, parsed):
    """Source versions track bytes; immutable analyses track parser recovery."""
    if (
        capture.workspace_id != capture.source.workspace_id
        or capture.run.source_id != capture.source_id
        or capture.run.workspace_id != capture.workspace_id
    ):
        raise ValidationError("Capture source/workspace does not match its run.")
    text = "\n\n".join(s["text"] for s in parsed["segments"])
    lookup = {"source": capture.source, "canonical_url": capture.url}
    try:
        with transaction.atomic():
            record, _ = ContentRecord.objects.get_or_create(
                **lookup,
                defaults={
                    "workspace": capture.workspace,
                    "title": (text.splitlines()[0] if text else capture.url)[:300],
                },
            )
    except IntegrityError:
        record = ContentRecord.objects.get(**lookup)
    record = ContentRecord.objects.select_for_update().get(pk=record.pk)
    previous = record.revisions.order_by("-number").first()
    same = bool(previous and previous.digest == capture.digest)
    completeness = (
        "complete"
        if parsed["status"] == "parsed" and not capture.missing
        else "partial"
    )
    summary = summary_for(parsed)
    if same:
        revision = previous
    else:
        number = previous.number + 1 if previous else 1
        revision = ContentVersion.objects.create(
            workspace=capture.workspace,
            record=record,
            number=number,
            digest=capture.digest,
            media_type=capture.media_type,
            completeness=completeness,
            metadata={"author": None, "date": None, "geography": None},
            summary=summary,
            body=text,
            segments=parsed["segments"],
            changes={
                "text_diff": "\n".join(
                    difflib.unified_diff(
                        record.body.splitlines() if previous else [],
                        text.splitlines(),
                        fromfile=f"v{number - 1}",
                        tofile=f"v{number}",
                        lineterm="",
                    )
                )[:100000],
                "field_diff": "Previous extraction is stale; explicit reprocessing is required"
                if previous
                else "No earlier fields",
            },
        )
    current = revision.analyses.first()
    improve = (
        not current
        or analysis_quality(parsed["status"]) > analysis_quality(current.status)
        or (
            analysis_quality(parsed["status"]) == analysis_quality(current.status)
            and parsed["status"] in {"parsed", "partial"}
            and (
                current.segments != parsed["segments"]
                or current.completeness != completeness
            )
        )
    )
    if improve:
        analysis = ContentAnalysis.objects.create(
            workspace=capture.workspace,
            content_version=revision,
            capture=capture,
            number=current.number + 1 if current else 1,
            status=parsed["status"],
            reason=parsed["reason"],
            body=text,
            segments=parsed["segments"],
            summary=summary,
            completeness=completeness,
        )
        if previous:
            original = list(record.type_assignments.values_list("key", flat=True))
            record.type_assignments.all().delete()
            record.classification_version += 1
            ClassificationChange.objects.create(
                workspace=record.workspace,
                record=record,
                actor=capture.run.actor,
                version=record.classification_version,
                original=original,
                selected=[],
                reason="Content or processing evidence changed; previous types require reclassification.",
            )
        record.current_analysis = analysis
        record.body = text
        record.title = (text.splitlines()[0] if text else capture.url)[:300]
        record.status = "captured" if completeness == "complete" else "review"
        Derivative.objects.filter(record=record).update(stale=True)
    record.version = revision.number
    if not same:
        identity, _ = ContentIdentity.objects.get_or_create(
            workspace=capture.workspace, fingerprint=capture.digest
        )
        record.identity = identity
    record.save(
        update_fields=[
            "version",
            "identity",
            "current_analysis",
            "classification_version",
            "body",
            "title",
            "status",
        ]
    )
    capture.record = record
    capture.content_version = revision
    capture.save(update_fields=["record", "content_version"])
    return record, revision, not same


def validate_fields(fields):
    if not isinstance(fields, list) or not 1 <= len(fields) <= 50:
        raise ValidationError("fields: supply between 1 and 50 field definitions.")
    errors, keys = {}, set()
    for i, field in enumerate(fields):
        prefix = f"fields[{i}]"
        if not isinstance(field, dict):
            errors[prefix] = "Must be an object."
            continue
        key = field.get("key", "")
        if not isinstance(key, str) or not re.fullmatch(r"[a-z][a-z0-9_]{0,49}", key):
            errors[prefix + ".key"] = (
                "Use a lowercase key of up to 50 letters, digits or underscores."
            )
        elif key in keys:
            errors[prefix + ".key"] = "Duplicate key."
        keys.add(key if isinstance(key, str) else prefix)
        for name, limit in [("label", 100), ("instruction", 200)]:
            value = field.get(name)
            if (
                not isinstance(value, str)
                or not value.strip()
                or len(value) > limit
                or "\n" in value
                or ":" in value
            ):
                errors[prefix + "." + name] = (
                    f"Supply a single label without a colon, up to {limit} characters."
                )
        kind = field.get("type")
        if not isinstance(kind, str) or kind not in {
            "text",
            "integer",
            "number",
            "boolean",
            "date",
            "enum",
        }:
            errors[prefix + ".type"] = (
                "Choose text, integer, number, boolean, date or enum."
            )
        for flag in ["required", "evidence_required"]:
            if not isinstance(field.get(flag), bool):
                errors[prefix + "." + flag] = "Must be true or false."
        allowed = field.get("allowed_values", [])
        if (
            not isinstance(allowed, list)
            or len(allowed) > 100
            or any(not isinstance(v, str) or not v or len(v) > 200 for v in allowed)
        ):
            errors[prefix + ".allowed_values"] = "Supply up to 100 nonempty strings."
        elif (kind == "enum" and not allowed) or (kind != "enum" and allowed):
            errors[prefix + ".allowed_values"] = (
                "Allowed values are required only for enum fields."
            )
        unit = field.get("unit", "")
        if (
            not isinstance(unit, str)
            or len(unit) > 30
            or (unit and kind not in {"number", "integer"})
        ):
            errors[prefix + ".unit"] = (
                "Units apply only to numeric fields (up to 30 characters)."
            )
    if errors:
        raise ValidationError(errors)
    return fields


@transaction.atomic
def save_schema(
    actor,
    workspace_id,
    name,
    key,
    scope,
    fields,
    schema_id=None,
    expected_version=0,
    system_id=None,
):
    member(actor, workspace_id, write=True)
    fields = validate_fields(fields)
    if not isinstance(name, str) or not name.strip() or len(name) > 120:
        raise ValidationError("name: supply a name of up to 120 characters.")
    if not isinstance(key, str) or not re.fullmatch(r"[a-z][a-z0-9_-]{0,59}", key):
        raise ValidationError("key: use a lowercase key of up to 60 characters.")
    if scope not in {"common", "type", "system"}:
        raise ValidationError("scope: choose common, type or system.")
    system = (
        get_object_or_404(systems_for(actor, workspace_id), pk=system_id)
        if system_id
        else None
    )
    if (scope == "system") != bool(system):
        raise ValidationError("system: a system is required only for system scope.")
    if schema_id:
        schema = get_object_or_404(
            schemas_for(actor, workspace_id).select_for_update(), pk=schema_id
        )
        if schema.state == "retired":
            raise ValidationError("Retired schemas cannot accept new drafts.")
        if (schema.key, schema.scope, schema.system_id) != (
            key,
            scope,
            system.pk if system else None,
        ):
            raise ValidationError(
                "Schema key and scope cannot be changed in a field draft."
            )
    else:
        if ContentSchema.objects.filter(workspace_id=workspace_id, key=key).exists():
            raise ValidationError("key: already exists in this workspace.")
        schema = ContentSchema.objects.create(
            workspace_id=workspace_id,
            owner=actor,
            name=name,
            key=key,
            scope=scope,
            system=system,
        )
    latest = schema.versions.order_by("-number").first()
    current = latest.number if latest else 0
    if current != expected_version:
        raise ValidationError(
            "The draft changed; reload the current version before saving."
        )
    version = SchemaVersion.objects.create(
        schema=schema, number=current + 1, fields=fields
    )
    schema.name = name
    schema.save(update_fields=["name"])
    audit(
        actor,
        workspace_id,
        "schema.draft_saved",
        schema.pk,
        {"version": version.number},
    )
    return schema, version


@transaction.atomic
def correct_types(
    actor,
    record,
    selected,
    reason,
    expected_version,
    expected_content_version=None,
    expected_analysis_id=None,
):
    record = editable_record(actor, record.workspace_id, record.pk)
    record = ContentRecord.objects.select_for_update().get(pk=record.pk)
    if (
        expected_content_version is not None
        and expected_content_version != record.version
    ) or (
        expected_analysis_id is not None
        and expected_analysis_id != record.current_analysis_id
    ):
        raise ValidationError(
            "Content changed during classification; reload before correcting types."
        )
    if expected_version != record.classification_version:
        raise ValidationError("Classification changed; reload before correcting it.")
    if (
        not isinstance(selected, list)
        or len(selected) > 20
        or any(not isinstance(k, str) for k in selected)
    ):
        raise ValidationError("Choose up to 20 registered content types.")
    selected = sorted(set(selected))
    available = set(
        schemas_for(actor, record.workspace_id)
        .filter(scope="type")
        .exclude(state="retired")
        .values_list("key", flat=True)
    )
    if any(k not in available for k in selected):
        raise ValidationError("A selected type is unavailable.")
    if not isinstance(reason, str) or not reason.strip() or len(reason) > 2000:
        raise ValidationError("Supply a correction reason of up to 2000 characters.")
    original = list(record.type_assignments.values_list("key", flat=True))
    record.classification_version += 1
    record.save(update_fields=["classification_version"])
    record.type_assignments.all().delete()
    ContentType.objects.bulk_create(
        [ContentType(record=record, key=k) for k in selected]
    )
    change = ClassificationChange.objects.create(
        workspace_id=record.workspace_id,
        record=record,
        actor=actor,
        version=record.classification_version,
        original=original,
        selected=selected,
        reason=reason,
    )
    Derivative.objects.filter(record=record).update(stale=True)
    revision = record.revisions.order_by("-number").first()
    for key in selected or [None]:
        schema = (
            ContentSchema.objects.filter(
                workspace_id=record.workspace_id,
                key=key,
                state="published",
                scope="type",
            ).first()
            if key
            else None
        )
        schema_version = (
            schema.versions.filter(number=schema.published_version).first()
            if schema
            else None
        )
        ExtractionJob.objects.create(
            workspace_id=record.workspace_id,
            record=record,
            actor=actor,
            classification_version=record.classification_version,
            content_version=revision,
            analysis=record.current_analysis,
            schema_version=schema_version,
            preview=False,
            state="queued" if revision and schema_version else "action_required",
            reason=""
            if revision and schema_version
            else "Select/publish an available type schema before extraction.",
        )
    audit(
        actor,
        record.workspace_id,
        "content.type_corrected",
        record.pk,
        {
            "change": str(change.pk),
            "classification_version": record.classification_version,
        },
    )
    return change


def typed_value(raw, field):
    raw = raw.strip()
    kind, unit = field["type"], field.get("unit", "")
    if unit:
        suffix = " " + unit
        if not raw.endswith(suffix):
            raise ValueError("Required unit is absent or inconsistent.")
        raw = raw[: -len(suffix)].strip()
    if kind == "text":
        if not raw:
            raise ValueError("Empty value.")
        return raw
    if kind == "enum":
        if raw not in field["allowed_values"]:
            raise ValueError("Value is outside allowed enumeration.")
        return raw
    if kind == "boolean":
        if raw.lower() not in {"true", "false"}:
            raise ValueError("Use true or false.")
        return raw.lower() == "true"
    if kind == "date":
        if "T" in raw or " " in raw:
            return datetime.fromisoformat(raw).isoformat()
        return date.fromisoformat(raw).isoformat()
    number = Decimal(raw)
    if not number.is_finite():
        raise ValueError("Non-finite number.")
    if kind == "integer":
        if number != number.to_integral_value() or abs(number) > 10**18:
            raise ValueError("Expected a bounded integer.")
        return int(number)
    value = float(number)
    if not math.isfinite(value):
        raise ValueError("Number is out of range.")
    return value


def extract_fields(revision, fields):
    """Bounded exact-label connector; unsupported facts remain unknown."""
    validate_fields(fields)
    result = {}
    for field in fields:
        candidates = []
        marker = field["instruction"].strip().casefold()
        for index, segment in enumerate(revision.segments):
            for line in segment["text"].splitlines():
                label, separator, raw = line.strip().partition(":")
                if not separator or label.strip().casefold() != marker:
                    continue
                raw = raw.strip()
                try:
                    if len(raw) > 2000:
                        raise ValueError("Value exceeds supported field length.")
                    value = typed_value(raw, field)
                    candidates.append(
                        {
                            "value": value,
                            "raw": raw,
                            "segment": index,
                            "method": "exact_label",
                            "unit": field.get("unit", ""),
                        }
                    )
                except (ValueError, InvalidOperation, OverflowError):
                    candidates.append(
                        {
                            "raw": raw[:2000],
                            "segment": index,
                            "method": "exact_label",
                            "error": "Value does not conform to schema type, unit or enumeration.",
                        }
                    )
                if len(candidates) >= 100:
                    break
            if len(candidates) >= 100:
                break
        valid = [c for c in candidates if "value" in c]
        distinct = {repr(c["value"]) for c in valid}
        state = (
            "conflict"
            if len(distinct) > 1 or (valid and len(valid) != len(candidates))
            else ("known" if len(distinct) == 1 else "unknown")
        )
        limited = len(candidates) >= 100
        if limited:
            state = "unknown"
        result[field["key"]] = {
            "candidate_limit_reached": limited,
            "status": state,
            "value": valid[0]["value"] if state == "known" else None,
            "candidates": candidates,
            "method": "exact_label",
            "evidence_required": field["evidence_required"],
            "required": field["required"],
            "unit": field.get("unit", ""),
        }
    return result


@transaction.atomic
def preview_extraction(actor, record, schema_version):
    record = editable_record(actor, record.workspace_id, record.pk)
    record = ContentRecord.objects.select_for_update().get(pk=record.pk)
    schema = get_object_or_404(
        schemas_for(actor, record.workspace_id), pk=schema_version.schema_id
    )
    if schema.system_id and not system_can_read(schema.system, record.source):
        raise PermissionDenied("The system cannot read this source.")
    if schema.state == "retired":
        raise ValidationError("Retired schemas cannot start extraction.")
    revision = record.revisions.order_by("-number").first()
    if not revision:
        raise ValidationError("A parsed content version is required.")
    analysis = record.current_analysis
    result = extract_fields(analysis or revision, schema_version.fields)
    job = ExtractionJob.objects.create(
        workspace_id=record.workspace_id,
        record=record,
        actor=actor,
        classification_version=record.classification_version,
        content_version=revision,
        analysis=analysis,
        schema_version=schema_version,
        preview=True,
        state="preview",
        result=result,
    )
    audit(
        actor,
        record.workspace_id,
        "extraction.previewed",
        job.pk,
        {"schema_version": schema_version.number, "content_version": revision.number},
    )
    return job


@transaction.atomic
def decide_duplicate(actor, left, right, decision, reason):
    from workspaces.models import Workspace

    Workspace.objects.select_for_update().get(pk=left.workspace_id)
    if member(actor, left.workspace_id).role not in {"administrator", "reviewer"}:
        raise PermissionDenied("Reviewer access required.")
    visible = records_for(actor, left.workspace_id)
    if (
        left.pk == right.pk
        or not visible.filter(pk=left.pk).exists()
        or not visible.filter(pk=right.pk).exists()
    ):
        raise PermissionDenied()
    if (
        decision not in {"link", "separate"}
        or not isinstance(reason, str)
        or not reason.strip()
        or len(reason) > 2000
    ):
        raise ValidationError(
            "Choose link/separate and supply a reason of up to 2000 characters."
        )
    left = visible.get(pk=left.pk)
    right = visible.get(pk=right.pk)
    left, right = sorted([left, right], key=lambda r: str(r.pk))
    # Rebuild only the visible groups involved. Hidden provenance is never loaded or changed.
    identities = [i for i in [left.identity_id, right.identity_id] if i]
    group = list(
        visible.filter(Q(identity_id__in=identities) | Q(pk__in=[left.pk, right.pk]))
        .select_for_update()
        .order_by("pk")[:501]
    )
    if len(group) > 500:
        raise ValidationError(
            "This identity group exceeds the supported 500-record review budget."
        )
    ids = [r.pk for r in group]
    parent = {pk: pk for pk in ids}

    def root(pk):
        while parent[pk] != pk:
            parent[pk] = parent[parent[pk]]
            pk = parent[pk]
        return pk

    def union(a, b):
        parent[root(a)] = root(b)

    result, _ = DuplicateDecision.objects.update_or_create(
        left=left,
        right=right,
        defaults={
            "workspace_id": left.workspace_id,
            "actor": actor,
            "decision": decision,
            "reason": reason,
        },
    )
    for a, b in DuplicateDecision.objects.filter(
        workspace_id=left.workspace_id,
        left_id__in=ids,
        right_id__in=ids,
        decision="link",
    ).values_list("left_id", "right_id"):
        union(a, b)
    digests = {}
    for pk, digest in ContentVersion.objects.filter(
        record_id__in=ids, number=F("record__version")
    ).values_list("record_id", "digest"):
        if digest in digests:
            union(pk, digests[digest])
        else:
            digests[digest] = pk
    if decision == "separate" and root(left.pk) == root(right.pk):
        raise ValidationError(
            "Other retained links or exact-byte identity still connect these records; resolve those links first."
        )
    for separated_left, separated_right in DuplicateDecision.objects.filter(
        workspace_id=left.workspace_id,
        left_id__in=ids,
        right_id__in=ids,
        decision="separate",
    ).values_list("left_id", "right_id"):
        if root(separated_left) == root(separated_right):
            raise ValidationError(
                "This link would contradict a retained separation decision; explicitly review that decision first."
            )
    groups = {}
    for pk in ids:
        groups.setdefault(root(pk), []).append(pk)
    for members in groups.values():
        fingerprint = hashlib.sha256(
            ("review-group:" + ":".join(sorted(str(pk) for pk in members))).encode()
        ).hexdigest()
        identity, _ = ContentIdentity.objects.get_or_create(
            workspace_id=left.workspace_id, fingerprint=fingerprint
        )
        ContentRecord.objects.filter(pk__in=members).update(identity=identity)
    audit(
        actor,
        left.workspace_id,
        "duplicate.decided",
        result.pk,
        {"decision": decision, "reason": reason},
    )
    return result


def duplicate_candidates(actor, record):
    visible = (
        records_for(actor, record.workspace_id).exclude(pk=record.pk).order_by("pk")
    )
    latest = record.revisions.order_by("-number").first()
    exact = (
        visible.filter(
            revisions__digest=latest.digest,
            revisions__number=F("version"),
        ).distinct()
        if latest
        else visible.none()
    )
    exact_ids = list(exact.values_list("pk", flat=True)[:50])
    approximate = []
    for candidate in visible.exclude(pk__in=exact_ids)[:200]:
        similarity = difflib.SequenceMatcher(
            None, record.title[:300].casefold(), candidate.title[:300].casefold()
        ).ratio()
        if similarity >= 0.7:
            approximate.append(
                {"record": candidate, "similarity": round(similarity, 3)}
            )
    return {"exact": visible.filter(pk__in=exact_ids), "approximate": approximate[:50]}


@transaction.atomic
def process_extraction(job_id):
    from django.http import Http404

    job = ExtractionJob.objects.select_for_update().get(pk=job_id)
    if job.state != "queued" or job.preview:
        return job
    try:
        record = editable_record(job.actor, job.workspace_id, job.record_id)
        record = ContentRecord.objects.select_for_update().get(pk=record.pk)
        if job.content_version_id and job.content_version.record_id != record.pk:
            raise ValidationError("Content version does not belong to this record.")
        schema = (
            get_object_or_404(
                schemas_for(job.actor, job.workspace_id),
                pk=job.schema_version.schema_id,
            )
            if job.schema_version_id
            else None
        )
        if (
            schema
            and schema.system_id
            and not system_can_read(schema.system, record.source)
        ):
            raise PermissionDenied()
        if not schema or schema.state != "published" or not job.content_version_id:
            raise ValidationError(
                "A published schema and parsed content version are required."
            )
        if (
            record.version != job.content_version.number
            or record.classification_version != job.classification_version
            or record.current_analysis_id != job.analysis_id
        ):
            job.state = "stale"
            job.reason = "Content or classification changed; explicitly queue current extraction."
        else:
            if (
                job.analysis_id
                and job.analysis.content_version_id != job.content_version_id
            ):
                raise ValidationError("Analysis is not bound to the content version.")
            job.result = extract_fields(
                job.analysis or job.content_version, job.schema_version.fields
            )
            Derivative.objects.create(
                record=record,
                kind="typed",
                content_version=job.content_version.number,
                value={
                    "schema_id": str(schema.pk),
                    "schema_version": job.schema_version.number,
                    "fields": job.result,
                    "job": str(job.pk),
                },
            )
            job.state = "completed"
    except (PermissionDenied, Http404, ValidationError):
        job.state = "blocked"
        job.reason = "Current access, schema or content does not permit extraction."
    job.save(update_fields=["state", "reason", "result"])
    audit(
        job.actor, job.workspace_id, "extraction.finished", job.pk, {"state": job.state}
    )
    return job


@transaction.atomic
def classify_record(actor, record):
    record = editable_record(actor, record.workspace_id, record.pk)
    record = ContentRecord.objects.select_for_update().get(pk=record.pk)
    supported = set(
        schemas_for(actor, record.workspace_id)
        .filter(scope="type", state="published")
        .values_list("key", flat=True)
    )
    # This bounded connector only recognises explicit source-declared type keys.
    # Unstructured mentions are uncertain and remain Unknown.
    selected = set()
    for line in record.body.splitlines():
        label, separator, raw = line.partition(":")
        if separator and label.strip().casefold() == "type":
            selected.update(
                k.strip().casefold()
                for k in raw.split(",")
                if k.strip().casefold() in supported
            )
    return correct_types(
        actor,
        record,
        sorted(selected),
        "Explicit source Type labels matched published keys; uncertain/unmatched content is Unknown.",
        record.classification_version,
        expected_content_version=record.version,
        expected_analysis_id=record.current_analysis_id,
    )
