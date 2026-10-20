"""Local isolated restore exercises; no production backup or RPO/RTO claims."""

import hashlib
import io
import json
import tempfile
import uuid
from datetime import timedelta
from pathlib import Path

from django.apps import apps
from django.contrib.auth import get_user_model
from django.core import serializers
from django.core.exceptions import ValidationError
from django.core.management import CommandError, call_command
from django.db import DatabaseError, connections, transaction
from django.utils import timezone

from workspaces.models import Membership, Workspace

from . import models as m
from .permissions import member
from .services import audit


def checksum(payload):
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def delivery_held(workspace_id):
    return m.RecoveryControl.objects.filter(
        workspace_id=workspace_id, delivery_hold=True
    ).exists()


@transaction.atomic
def take_snapshot(actor, workspace_id, retention_minutes):
    Workspace.objects.select_for_update(no_key=True).get(pk=workspace_id)
    member(actor, workspace_id, admin=True)
    if type(retention_minutes) is not int or not 1 <= retention_minutes <= 10080:
        raise ValidationError(
            "Declare prototype snapshot retention of 1–10080 minutes; no production policy is implied."
        )
    objects = [
        Workspace.objects.get(pk=workspace_id),
        *Membership.objects.filter(workspace_id=workspace_id),
    ]
    excluded = {
        "Connection",
        "BrowserHandoff",
        "BrowserAction",
        "Invitation",
        "RecoverySnapshot",
        "RecoveryExercise",
        "RecoveryControl",
    }
    # All workspace content and its dependent version/contract rows are captured coherently.
    for model in apps.get_app_config("intelligence").get_models():
        if model.__name__ in excluded:
            continue
        fields = {field.name for field in model._meta.fields}
        if "workspace" in fields:
            objects.extend(model.objects.filter(workspace_id=workspace_id))
        elif "membership" in fields:
            objects.extend(model.objects.filter(membership__workspace_id=workspace_id))
        elif "destination" in fields:
            objects.extend(model.objects.filter(destination__workspace_id=workspace_id))
        elif "schema" in fields:
            objects.extend(model.objects.filter(schema__workspace_id=workspace_id))
        elif "sample_set" in fields:
            objects.extend(model.objects.filter(sample_set__workspace_id=workspace_id))
        elif "version" in fields and model.__name__ == "EvaluationSample":
            objects.extend(
                model.objects.filter(version__sample_set__workspace_id=workspace_id)
            )
        elif "capture" in fields:
            objects.extend(model.objects.filter(capture__workspace_id=workspace_id))
        elif "record" in fields:
            objects.extend(model.objects.filter(record__workspace_id=workspace_id))
        elif "source" in fields:
            objects.extend(model.objects.filter(source__workspace_id=workspace_id))
        elif "message" in fields:
            objects.extend(model.objects.filter(message__workspace_id=workspace_id))
        elif "handoff" in fields:
            continue
        elif "system" in fields:
            objects.extend(model.objects.filter(system__workspace_id=workspace_id))
    user_ids = set(
        Membership.objects.filter(workspace_id=workspace_id).values_list(
            "user_id", flat=True
        )
    )
    for obj in objects:
        for field in obj._meta.fields:
            if (
                field.is_relation
                and field.related_model == get_user_model()
                and getattr(obj, field.attname)
            ):
                user_ids.add(getattr(obj, field.attname))
    objects.extend(get_user_model().objects.filter(pk__in=user_ids))
    payload = json.loads(serializers.serialize("json", objects))
    for row in payload:
        if row["model"] == "auth.user":
            row["fields"].update(
                password="!",
                groups=[],
                user_permissions=[],
                is_staff=False,
                is_superuser=False,
            )
        for field in [
            "secret_ref",
            "session_ciphertext",
            "pending_ciphertext",
            "frame_ciphertext",
            "payload_ciphertext",
        ]:
            if field in row["fields"]:
                row["fields"][field] = ""
    snapshot = m.RecoverySnapshot.objects.create(
        workspace_id=workspace_id,
        actor=actor,
        payload=payload,
        digest=checksum(payload),
        expires_at=timezone.now() + timedelta(minutes=retention_minutes),
        manifest={
            "schema_migrations": max(
                path.name
                for path in Path(__file__)
                .parent.joinpath("migrations")
                .glob("[0-9]*.py")
            ),
            "code_digest": hashlib.sha256(
                b"".join(
                    path.read_bytes()
                    for path in sorted(Path(__file__).parent.glob("*.py"))
                )
            ).hexdigest(),
            "objects": len(payload),
            "credentials": "Excluded: connections, sessions, invitations and user passwords",
            "scope": "One workspace, retained originals/derivatives/configuration/jobs/outbox/receipts",
            "target": "Fresh isolated temporary SQLite database; never a live database",
        },
    )
    audit(
        actor,
        workspace_id,
        "recovery.snapshot_created",
        snapshot.pk,
        {"count": len(payload)},
    )
    return snapshot


def sanitized_rows(snapshot, tombstones):
    rows = json.loads(json.dumps(snapshot.payload))
    deleted = {str(t.record_id) for t in tombstones}
    captures = {
        str(row["pk"])
        for row in rows
        if row["model"] == "intelligence.capture"
        and str(row["fields"].get("record")) in deleted
    }
    versions = {
        str(row["pk"])
        for row in rows
        if row["model"] == "intelligence.contentversion"
        and str(row["fields"].get("record")) in deleted
    }
    affected = deleted | captures | versions
    affected_rows = set()
    # Closure over records, source analyses, results, evidence histories and stored delivery snapshots.
    changed = True
    while changed:
        changed = False
        for row in rows:
            if row["model"] in {
                "auth.user",
                "workspaces.workspace",
                "workspaces.membership",
                "intelligence.source",
                "intelligence.sourceversion",
                "intelligence.consumersystem",
                "intelligence.systemconfiguration",
                "intelligence.systemsourcegrant",
            }:
                continue
            if (
                any(identifier in json.dumps(row["fields"]) for identifier in affected)
                and (row["model"], str(row["pk"])) not in affected_rows
            ):
                affected_rows.add((row["model"], str(row["pk"])))
                try:
                    identifier = str(uuid.UUID(str(row["pk"])))
                except ValueError:
                    continue
                if identifier not in affected:
                    affected.add(identifier)
                    changed = True
    affected_evaluations = {
        str(row["fields"]["version"])
        for row in rows
        if row["model"] == "intelligence.evaluationsample"
        and (row["model"], str(row["pk"])) in affected_rows
    }
    for row in rows:
        if (
            row["model"]
            in {"intelligence.schemacheck", "intelligence.configurationcomparison"}
            and str(row["fields"].get("evaluation_version")) in affected_evaluations
        ):
            affected_rows.add((row["model"], str(row["pk"])))
    affected_external_keys = {
        row["fields"]["external_key"]
        for row in rows
        if row["model"] == "intelligence.deliveryintent"
        and (row["model"], str(row["pk"])) in affected_rows
    }
    for row in rows:
        if (
            row["model"] == "intelligence.sandboxmessage"
            and row["fields"].get("external_key") in affected_external_keys
        ):
            affected_rows.add((row["model"], str(row["pk"])))
    text_fields = {
        "body",
        "title",
        "reason",
        "trigger",
        "url",
        "final_url",
        "canonical_url",
        "digest",
    }
    structured = {
        "segments",
        "summary",
        "metadata",
        "changes",
        "fields",
        "relevance",
        "qualification",
        "original",
        "replacement",
        "evidence",
        "merged",
        "expected_fields",
        "results",
        "coverage",
        "pending",
        "visited",
        "unresolved_fields",
        "checkpoint",
        "result",
        "payload",
        "snapshot",
        "response",
        "missing",
    }
    for row in rows:
        fields = row["fields"]
        if (
            str(row["pk"]) in affected
            or (row["model"], str(row["pk"])) in affected_rows
        ) and row["model"].startswith("intelligence."):
            model = apps.get_model(row["model"])
            for key in set(fields) & text_fields:
                if not model._meta.get_field(key).is_relation:
                    fields[key] = ""
            for key in set(fields) & structured:
                if model._meta.get_field(key).is_relation:
                    continue
                fields[key] = (
                    []
                    if isinstance(fields[key], list)
                    else {}
                    if isinstance(fields[key], dict)
                    else ""
                )
            if row["model"] == "intelligence.contentrecord":
                fields.update(status="deleted", identity=None, current_analysis=None)
            if row["model"] == "intelligence.capture":
                fields.update(status="deleted", original="")
            if row["model"] == "intelligence.systemresult":
                fields.update(decision="blocked", preview=True)
            if "state" in fields and row["model"] != "intelligence.deliveryintent":
                fields["state"] = "blocked"
            if (
                row["model"] == "intelligence.deliveryintent"
                and fields["operation"] != "withdraw"
                and fields["state"] not in {"ambiguous", "manual_follow_up"}
            ):
                fields["state"] = "blocked"
            if "passed" in fields:
                fields["passed"] = False
        if row["model"] == "intelligence.workitem" and fields["state"] not in {
            "running",
            "queued",
            "retry",
        }:
            fields.update(lease_token=None, lease_until=None)
        if row["model"] == "intelligence.workitem" and fields["state"] in {
            "running",
            "queued",
            "retry",
        }:
            fields.update(
                state="queued", lease_token=str(uuid.uuid4()), lease_until=None
            )
    existing_tombstones = {
        str(row["pk"])
        for row in rows
        if row["model"] == "intelligence.contenttombstone"
    }
    rows.extend(
        json.loads(
            serializers.serialize(
                "json", [t for t in tombstones if str(t.pk) not in existing_tombstones]
            )
        )
    )
    return rows, deleted


def new_alias():
    return "recovery_" + uuid.uuid4().hex


def run_exercise(actor, workspace_id, snapshot_id, policy):
    from .delivery import digest, payload_digest

    if (
        not isinstance(policy, dict)
        or set(policy) != {"scope", "failure_mode", "recovery_target_minutes"}
        or not all(
            isinstance(policy[k], str) and policy[k].strip() and len(policy[k]) <= 500
            for k in ["scope", "failure_mode"]
        )
        or type(policy["recovery_target_minutes"]) is not int
        or not 1 <= policy["recovery_target_minutes"] <= 10080
    ):
        raise ValidationError(
            "Declare scope, exercised failure mode and prototype recovery target minutes; no production target is assumed."
        )
    with transaction.atomic():
        Workspace.objects.select_for_update(no_key=True).get(pk=workspace_id)
        member(actor, workspace_id, admin=True)
        snapshot = m.RecoverySnapshot.objects.get(
            pk=snapshot_id, workspace_id=workspace_id
        )
        if (
            snapshot.state != "retained"
            or snapshot.expires_at <= timezone.now()
            or checksum(snapshot.payload) != snapshot.digest
            or len(snapshot.payload) != snapshot.manifest.get("objects")
        ):
            raise ValidationError(
                "Snapshot expired, invalidated or failed integrity validation."
            )
        exercise = m.RecoveryExercise.objects.create(
            workspace_id=workspace_id, actor=actor, snapshot=snapshot, policy=policy
        )
        control, _ = m.RecoveryControl.objects.get_or_create(workspace_id=workspace_id)
        control.delivery_hold, control.exercise = True, exercise
        control.save()
    verified_snapshot_digest = snapshot.digest
    errors, restored = [], 0
    started = timezone.now()
    alias = new_alias()
    tombstones = list(
        m.ContentTombstone.objects.filter(workspace_id=workspace_id).order_by("pk")
    )
    generation = checksum([(str(t.pk), t.url_hash) for t in tombstones])
    target_rows, deleted = sanitized_rows(snapshot, tombstones)
    original_leases = {
        str(row["pk"]): row["fields"].get("lease_token")
        for row in snapshot.payload
        if row["model"] == "intelligence.workitem" and row["fields"].get("lease_token")
    }
    leases_rotated = 0
    try:
        with tempfile.TemporaryDirectory(prefix="insight-recovery-") as directory:
            config = dict(connections["default"].settings_dict)
            config.update(
                ENGINE="django.db.backends.sqlite3",
                NAME=str(Path(directory) / "isolated.sqlite3"),
                OPTIONS={},
                TIME_ZONE=None,
            )
            connections.databases[alias] = config
            call_command(
                "migrate",
                database=alias,
                interactive=False,
                verbosity=0,
                stdout=io.StringIO(),
            )
            connection = connections[alias]
            with (
                connection.constraint_checks_disabled(),
                transaction.atomic(using=alias),
            ):
                for obj in serializers.deserialize("json", json.dumps(target_rows)):
                    obj.save(using=alias)
                    restored += 1
            connection.check_constraints()
            for capture in m.Capture.objects.using(alias).filter(
                workspace_id=workspace_id
            ):
                if (
                    capture.status != "deleted"
                    and hashlib.sha256(bytes(capture.original)).hexdigest()
                    != capture.digest
                ):
                    errors.append("Original digest mismatch: " + str(capture.pk))
            for capture in m.Capture.objects.using(alias).filter(
                workspace_id=workspace_id
            ):
                if (
                    capture.source.workspace_id != workspace_id
                    or capture.run.workspace_id != workspace_id
                    or capture.run.source_id != capture.source_id
                    or capture.record_id
                    and capture.record.source_id != capture.source_id
                ):
                    errors.append("Capture lineage mismatch: " + str(capture.pk))
            for run in m.WorkItem.objects.using(alias).filter(
                workspace_id=workspace_id
            ):
                if str(run.pk) in original_leases:
                    if str(run.lease_token) == original_leases[str(run.pk)]:
                        errors.append("Stale recovered lease token: " + str(run.pk))
                    else:
                        leases_rotated += 1
                if (
                    run.source.workspace_id != run.workspace_id
                    or run.source_version.source_id != run.source_id
                ):
                    errors.append("Run/source lineage mismatch: " + str(run.pk))
            for result in m.SystemResult.objects.using(alias).filter(
                workspace_id=workspace_id
            ):
                if (
                    result.content_version.record_id != result.record_id
                    or result.configuration.system_id != result.system_id
                    or result.system.workspace_id != workspace_id
                    or result.record.workspace_id != workspace_id
                    or result.analysis_id
                    and result.analysis.content_version_id != result.content_version_id
                ):
                    errors.append("Result lineage mismatch: " + str(result.pk))
            for intent in m.DeliveryIntent.objects.using(alias).filter(
                workspace_id=workspace_id
            ):
                if (
                    intent.result.workspace_id != workspace_id
                    or intent.destination.workspace_id != workspace_id
                    or intent.bundle.workspace_id != workspace_id
                    or intent.bundle.system_id != intent.result.system_id
                    or intent.bundle.mapping.contract.destination_id
                    != intent.destination_id
                ):
                    errors.append(
                        "Delivery bundle/destination lineage mismatch: "
                        + str(intent.pk)
                    )
                residual = (
                    m.SandboxMessage.objects.using(alias)
                    .filter(
                        destination_id=intent.destination_id,
                        external_key=intent.external_key,
                        withdrawn=False,
                    )
                    .exists()
                )
                if (
                    intent.state in {"ambiguous", "manual_follow_up"}
                    or intent.operation == "withdraw"
                    and intent.state != "delivered_sandbox"
                    or intent.result.record.status == "deleted"
                    and intent.attempts > 0
                    and residual
                ):
                    errors.append(
                        "Ambiguous write requires manual reconciliation: "
                        + str(intent.pk)
                    )
                if (
                    intent.state == "delivered_sandbox"
                    and not m.SandboxReceipt.objects.using(alias)
                    .filter(
                        idempotency_key=intent.idempotency_key,
                        payload_digest=payload_digest(intent),
                        operation=intent.operation,
                        message__workspace_id=workspace_id,
                        message__destination_id=intent.destination_id,
                        message__external_key=intent.external_key,
                    )
                    .exists()
                ):
                    errors.append("Missing local receipt: " + str(intent.pk))
            for receipt in m.SandboxReceipt.objects.using(alias).filter(
                message__workspace_id=workspace_id
            ):
                if (
                    not m.DeliveryIntent.objects.using(alias)
                    .filter(
                        idempotency_key=receipt.idempotency_key,
                        destination_id=receipt.message.destination_id,
                        external_key=receipt.message.external_key,
                        operation=receipt.operation,
                    )
                    .exists()
                ):
                    errors.append("Orphan or mislinked receipt: " + str(receipt.pk))
            for message in m.SandboxMessage.objects.using(alias).filter(
                workspace_id=workspace_id
            ):
                latest = (
                    m.SandboxReceipt.objects.using(alias)
                    .filter(message=message)
                    .order_by("-created_at", "-pk")
                    .first()
                )
                if latest is None:
                    errors.append(
                        "Missing receipt for local message: " + str(message.pk)
                    )
                elif latest.operation == "withdraw":
                    if not message.withdrawn or message.payload:
                        errors.append(
                            "Withdrawn message state mismatch: " + str(message.pk)
                        )
                elif (
                    message.withdrawn
                    or digest(
                        latest.operation,
                        json.dumps(
                            message.payload, sort_keys=True, separators=(",", ":")
                        ),
                    )
                    != latest.payload_digest
                ):
                    errors.append(
                        "Current local message payload mismatch: " + str(message.pk)
                    )
            for record_id in deleted:
                if (
                    m.ContentRecord.objects.using(alias)
                    .filter(pk=record_id)
                    .exclude(status="deleted")
                    .exists()
                ):
                    errors.append("Deletion tombstone was not preserved: " + record_id)
            connection.close()
    except (
        ValueError,
        ValidationError,
        DatabaseError,
        CommandError,
        OSError,
        serializers.base.DeserializationError,
    ) as error:
        errors.append("Isolated restore failed: " + type(error).__name__)
    finally:
        if alias in connections:
            connections[alias].close()
            del connections[alias]
        connections.databases.pop(alias, None)
    elapsed = (timezone.now() - started).total_seconds()
    if elapsed > policy["recovery_target_minutes"] * 60:
        errors.append("Declared prototype recovery target exceeded")
    with transaction.atomic():
        Workspace.objects.select_for_update(no_key=True).get(pk=workspace_id)
        snapshot.refresh_from_db()
        current_generation = checksum(
            [
                (str(t.pk), t.url_hash)
                for t in m.ContentTombstone.objects.filter(
                    workspace_id=workspace_id
                ).order_by("pk")
            ]
        )
        if (
            snapshot.state != "retained"
            or snapshot.expires_at <= timezone.now()
            or checksum(snapshot.payload) != snapshot.digest
            or snapshot.digest != verified_snapshot_digest
            or current_generation != generation
        ):
            errors.append(
                "Snapshot or deletion tombstones changed during restore; rerun against current retained data"
            )
        exercise.state = "reconciled" if not errors else "action_required"
        exercise.report = {
            "restored_objects": restored,
            "snapshot_digest": verified_snapshot_digest,
            "isolated_restore_executed": True,
            "elapsed_seconds": round(elapsed, 3),
            "errors": errors,
            "delivery": "held until explicit administrator resume",
            "tombstones_reapplied": len(deleted),
            "target": "Temporary isolated SQLite database removed after verification",
            "production_restore": False,
            "tombstone_generation": generation,
            "restored_leases_invalidated": leases_rotated,
        }
        exercise.save(update_fields=["state", "report"])
    audit(
        actor,
        workspace_id,
        "recovery.exercise_finished",
        exercise.pk,
        {"count": restored, "state": exercise.state},
    )
    return exercise


@transaction.atomic
def resume_delivery(actor, workspace_id, exercise_id):
    Workspace.objects.select_for_update(no_key=True).get(pk=workspace_id)
    member(actor, workspace_id, admin=True)
    control = m.RecoveryControl.objects.select_for_update().get(
        workspace_id=workspace_id
    )
    if (
        str(control.exercise_id) != str(exercise_id)
        or control.exercise.state != "reconciled"
    ):
        raise ValidationError(
            "Delivery stays held until the latest exercise reconciles successfully."
        )
    snapshot = control.exercise.snapshot
    current_generation = checksum(
        [
            (str(t.pk), t.url_hash)
            for t in m.ContentTombstone.objects.filter(
                workspace_id=workspace_id
            ).order_by("pk")
        ]
    )
    if (
        snapshot.state != "retained"
        or snapshot.expires_at <= timezone.now()
        or checksum(snapshot.payload) != snapshot.digest
        or current_generation != control.exercise.report.get("tombstone_generation")
    ):
        raise ValidationError(
            "Snapshot or deletion tombstones changed; reconcile again before resuming delivery."
        )
    control.delivery_hold = False
    control.save(update_fields=["delivery_hold"])
    audit(
        actor,
        workspace_id,
        "recovery.delivery_resumed",
        exercise_id,
        {"delivery": True},
    )


@transaction.atomic
def expire_snapshots(workspace_id):
    Workspace.objects.select_for_update(no_key=True).get(pk=workspace_id)
    return m.RecoverySnapshot.objects.filter(
        workspace_id=workspace_id, state="retained", expires_at__lte=timezone.now()
    ).update(payload=[], state="expired")
