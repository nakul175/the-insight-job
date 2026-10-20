"""Opt-in evidence outbox; an uncertain write is never automatically replayed."""

import hashlib
import http.client
import json
import re
import uuid
from copy import deepcopy

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import connection, transaction
from django.shortcuts import get_object_or_404
from django.utils import timezone

from . import external_http
from .delivery import digest, validate_result
from .models import (
    ConsumerSystem,
    DeliveryIntent,
    ExternalAttempt,
    ExternalDestination,
    ExternalOperation,
)
from .permissions import member, sources_for
from .routing import live_access
from .services import audit
from .systems import owned_system


def payload_hash(payload):
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def admin_destination(actor, workspace_id, destination):
    member(actor, workspace_id, admin=True)
    get_object_or_404(
        ConsumerSystem, pk=destination.destination.system_id, workspace_id=workspace_id
    )
    if (
        destination.workspace_id != workspace_id
        or destination.destination.workspace_id != workspace_id
    ):
        raise PermissionDenied("Destination belongs to another workspace.")


@transaction.atomic
def configure(
    actor,
    workspace_id,
    destination_id,
    endpoint,
    secret_ref,
    approval_ref,
    receipt_contract_ref="",
    supports_notices=False,
):
    from .models import Destination

    member(actor, workspace_id, admin=True)
    original = get_object_or_404(
        Destination.objects.select_for_update(),
        pk=destination_id,
        workspace_id=workspace_id,
        state="test_passed",
    )
    owned_system(actor, workspace_id, original.system_id)
    endpoint = external_http.endpoint_url(endpoint)
    external_http.authorized_binding(workspace_id, endpoint, secret_ref)
    if (
        not re.fullmatch(r"TIJ_SECRET_[A-Z0-9_]{1,100}", secret_ref)
        or not approval_ref.strip()
        or len(approval_ref) > 300
        or len(receipt_contract_ref) > 300
        or type(supports_notices) is not bool
    ):
        raise ValidationError(
            "Provide protected authentication and bounded approval/receiver contract references."
        )
    contract = original.contracts.order_by("-number").first()
    if not contract:
        raise ValidationError("Save a permitted payload contract first.")
    if ExternalDestination.objects.filter(destination=original).exists():
        raise ValidationError(
            "Binding is immutable; deactivate and configure a new destination for changes."
        )
    item = ExternalDestination.objects.create(
        workspace_id=workspace_id,
        destination=original,
        owner=actor,
        contract=contract,
        endpoint=endpoint,
        secret_ref=secret_ref,
        approval_ref=approval_ref,
        receipt_contract_ref=receipt_contract_ref,
        supports_notices=supports_notices,
    )
    audit(
        actor,
        workspace_id,
        "external.configured_inactive",
        item.pk,
        {"contract": contract.number},
    )
    return item


@transaction.atomic
def activate(actor, workspace_id, destination_id, active):
    item = get_object_or_404(
        ExternalDestination.objects.select_for_update(),
        pk=destination_id,
        workspace_id=workspace_id,
    )
    admin_destination(actor, workspace_id, item)
    if type(active) is not bool:
        raise ValidationError("Explicit activation choice required.")
    external_http.endpoint_url(item.endpoint)
    if active:
        external_http.authorized_binding(workspace_id, item.endpoint, item.secret_ref)
    item.active = active
    item.save(update_fields=["active"])
    audit(
        actor, workspace_id, "external.activation_changed", item.pk, {"active": active}
    )
    return item


def evidence_package(actor, intent, destination):
    result = intent.result
    validate_result(actor, result, intent.bundle)
    if (
        result.version != intent.result_version
        or intent.bundle.mapping.contract_id != destination.contract_id
        or intent.destination_id != destination.destination_id
    ):
        raise ValidationError("Result/contract changed; store a new pinned version.")
    if (
        not sources_for(actor, intent.workspace_id, export=True)
        .filter(pk=result.record.source_id)
        .exists()
    ):
        raise PermissionDenied("Source export permission required.")
    # Only mapped schema fields and opaque provenance IDs may leave this workspace.
    selected = {entry["source"] for entry in intent.bundle.mapping.entries}
    from .models import Capture

    facts = {}
    permitted_sources = {
        str(pk)
        for pk in sources_for(actor, intent.workspace_id, export=True).values_list(
            "pk", flat=True
        )
    }
    for key in selected:
        fact = result.fields.get(key, {})
        facts[key] = {
            k: deepcopy(fact[k])
            for k in ["status", "value", "unit", "method"]
            if k in fact
        }
        if any(
            str(c.get("source")) not in permitted_sources
            for c in fact.get("candidates", [])
            if isinstance(c, dict) and c.get("source")
        ):
            raise PermissionDenied("Supporting source export permission required.")
        for candidate in fact.get("candidates", []):
            if (
                isinstance(candidate, dict)
                and candidate.get("capture")
                and not Capture.objects.filter(
                    pk=candidate["capture"],
                    workspace_id=intent.workspace_id,
                    source_id__in=permitted_sources,
                ).exists()
            ):
                raise PermissionDenied("Supporting capture export permission required.")
        facts[key]["candidates"] = [
            {
                k: deepcopy(c[k])
                for k in ["value", "status", "unit", "capture", "source"]
                if k in c
            }
            for c in fact.get("candidates", [])
            if isinstance(c, dict)
        ]
        facts[key]["references"] = [
            {k: str(c[k]) for k in ["capture", "source"] if c.get(k)}
            for c in fact.get("candidates", [])
            if isinstance(c, dict)
        ]
    return {
        "schema": "source-almighty.evidence.v1",
        "result": str(result.pk),
        "result_version": result.version,
        "source": str(result.record.source_id),
        "content_version": str(result.content_version_id),
        "capture_versions": [
            str(pk)
            for pk in Capture.objects.filter(
                content_version_id=result.content_version_id,
                workspace_id=intent.workspace_id,
            ).values_list("pk", flat=True)[:100]
        ],
        "analysis_number": result.analysis.number if result.analysis_id else None,
        "analysis_version": str(result.analysis_id) if result.analysis_id else None,
        "configuration": str(result.configuration_id),
        "configuration_version": result.configuration.number,
        "extraction": {
            "method": "per-field method; review changes may supersede extraction",
            "review_changes": [
                str(pk) for pk in result.changes.values_list("pk", flat=True)
            ],
            "bundle": str(intent.bundle_id),
            "classification_version": result.classification_version,
        },
        "schema_version": str(result.schema_version_id),
        "contract_version": destination.contract.number,
        "review_state": result.review_state,
        "receiver_approval": "not_implied",
        "fields": facts,
        "mapped_values": deepcopy(intent.payload),
    }


@transaction.atomic
def queue(
    actor, workspace_id, intent_id, destination_id, previous_id=None, withdrawal=False
):
    member(actor, workspace_id, admin=True)
    intent = get_object_or_404(DeliveryIntent, pk=intent_id, workspace_id=workspace_id)
    target = get_object_or_404(
        ExternalDestination, pk=destination_id, workspace_id=workspace_id
    )
    admin_destination(actor, workspace_id, target)
    from workspaces.models import Workspace

    Workspace.objects.select_for_update(no_key=True).get(pk=workspace_id)
    previous = None
    if previous_id:
        previous = get_object_or_404(
            ExternalOperation,
            pk=previous_id,
            workspace_id=workspace_id,
            destination=target,
        )
        if (
            previous.operation == "withdrawal"
            or previous.intent.result.record_id != intent.result.record_id
            or previous.attempted_at is None
        ):
            raise ValidationError(
                "Choose an attempted prior package for the same record/destination."
            )
    if withdrawal:
        if not previous:
            raise ValidationError("Withdrawal must reference a prior package.")
        package = {
            "schema": "source-almighty.evidence.v1",
            "withdraws": str(previous.pk),
            "prior_operation_key": previous.operation_key,
            "required_action": "withdraw",
            "receiver_approval": "not_implied",
        }
    else:
        package = evidence_package(actor, intent, target)
        if (
            previous
            and intent.result_id == previous.intent.result_id
            and intent.result_version <= previous.intent.result_version
        ):
            raise ValidationError("Correction requires a newer result version.")
    operation = "withdrawal" if withdrawal else "correction" if previous else "finding"
    if previous:
        package["supersedes"] = str(previous.pk)
    package["operation"] = operation
    key = digest(target.pk, intent.pk, operation, previous.pk if previous else "")
    package["operation_key"] = key
    item, created = ExternalOperation.objects.get_or_create(
        operation_key=key,
        defaults={
            "workspace_id": workspace_id,
            "destination": target,
            "intent": intent,
            "previous": previous,
            "actor": actor,
            "operation": operation,
            "payload": package,
            "payload_digest": payload_hash(package),
        },
    )
    if item.payload_digest != payload_hash(
        package
    ) or item.payload_digest != payload_hash(item.payload):
        raise ValidationError("Operation key is already bound to a different payload.")
    if created:
        audit(
            actor,
            workspace_id,
            "external.queued",
            item.pk,
            {"operation": operation, "digest": item.payload_digest},
        )
    return item


def current_authority(actor, item, lookup=False):
    from .recovery import delivery_held

    if not lookup and delivery_held(item.workspace_id):
        raise ValidationError("Recovery hold blocks all external writes.")
    target = item.destination
    admin_destination(actor, item.workspace_id, target)
    member(target.owner, item.workspace_id, admin=True)
    if (
        not target.active
        or target.contract_id != item.intent.bundle.mapping.contract_id
    ):
        raise ValidationError("Destination inactive or contract no longer permitted.")
    if item.payload_digest != payload_hash(item.payload):
        raise ValidationError("Stored package digest changed.")
    if lookup or item.operation == "withdrawal":
        # Receipt lookup and content-free withdrawal remain possible after source revocation.
        if item.operation == "withdrawal" and not target.supports_notices:
            raise ValidationError(
                "Receiver notice support is unverified; manual follow-up required."
            )
    else:
        live_access(actor, item.intent.result)
        package = evidence_package(actor, item.intent, target)
        for key, value in package.items():
            if item.payload.get(key) != value:
                raise ValidationError("Evidence changed; queue a new package version.")
        if item.operation == "correction" and not target.supports_notices:
            raise ValidationError("Receiver notice support is unverified.")
    if lookup and not target.receipt_contract_ref:
        raise ValidationError("No verified receipt contract; record manual follow-up.")


def send(actor, workspace_id, operation_id, lookup=False):
    if connection.in_atomic_block:
        raise ValidationError("Run external operations outside a database transaction.")
    claim = uuid.uuid4()
    with transaction.atomic():
        from workspaces.models import Workspace

        Workspace.objects.select_for_update(no_key=True).get(pk=workspace_id)
        item = get_object_or_404(
            ExternalOperation.objects.select_for_update(),
            pk=operation_id,
            workspace_id=workspace_id,
        )
        admin_destination(actor, workspace_id, item.destination)
        if item.state == "acknowledged" and not lookup:
            return item
        if item.state == "sending":
            raise ValidationError(
                "An attempt may still be running; use stale recovery after30seconds."
            )
        if (not lookup and item.state != "pending") or (
            lookup
            and item.state not in {"uncertain", "manual_follow_up", "acknowledged"}
        ):
            raise ValidationError(
                "Uncertain writes require receipt reconciliation, never blind retry."
            )
        try:
            current_authority(actor, item, lookup)
        except (PermissionDenied, ValidationError):
            item.state = "manual_follow_up"
            item.reason = (
                "Current permission, destination or contract requires owner action."
            )
            item.save(update_fields=["state", "reason"])
            ExternalAttempt.objects.create(
                workspace_id=workspace_id,
                operation=item,
                actor=actor,
                action="lookup" if lookup else "send",
                outcome="blocked",
            )
            return item
        item.state = "sending"
        item.claim = claim
        item.attempted_at = timezone.now()
        item.save(update_fields=["state", "claim", "attempted_at"])
    response = deepcopy(item.acknowledgement)
    applied = item.applied
    deleted = item.deletion_confirmed
    try:
        # Recheck revocable source/destination authority immediately before network I/O.
        item.refresh_from_db()
        current_authority(actor, item, lookup)
        received = external_http.request(item.destination, item, lookup)
        if (
            not isinstance(received, dict)
            or received.get("operation_key") != item.operation_key
            or received.get("payload_digest") != item.payload_digest
            or received.get("status") != "accepted"
            or not isinstance(received.get("receipt"), str)
            or not 1 <= len(received["receipt"]) <= 120
            or type(received.get("applied")) is not bool
            or type(received.get("deleted")) is not bool
        ):
            raise ValueError("Unconfirmed receiver receipt.")
        response = {
            key: received[key]
            for key in ["operation_key", "payload_digest", "receipt", "status"]
        }
        applied = received["applied"]
        deleted = item.operation == "withdrawal" and applied and received["deleted"]
        state = "acknowledged"
        reason = "Receiver receipt confirmed; application/deletion evidence tracked separately."
    except (
        OSError,
        ValueError,
        http.client.HTTPException,
        ValidationError,
        PermissionDenied,
    ):
        state = "uncertain"
        reason = "Receipt unconfirmed; reconcile or record manual follow-up. Never blindly replay."
    with transaction.atomic():
        locked = ExternalOperation.objects.select_for_update().get(pk=item.pk)
        if locked.claim != claim or locked.state != "sending":
            return locked
        locked.state = state
        locked.reason = reason
        locked.acknowledgement = response
        locked.applied = applied
        locked.deletion_confirmed = deleted
        locked.claim = None
        locked.save(
            update_fields=[
                "state",
                "reason",
                "acknowledgement",
                "applied",
                "deletion_confirmed",
                "claim",
            ]
        )
        ExternalAttempt.objects.create(
            workspace_id=workspace_id,
            operation=locked,
            actor=actor,
            action="lookup" if lookup else "send",
            outcome=state,
        )
        audit(
            actor,
            workspace_id,
            "external.attempt",
            locked.pk,
            {"state": state, "lookup": lookup, "digest": locked.payload_digest},
        )
        return locked


@transaction.atomic
def recover(actor, workspace_id, operation_id, reason):
    from workspaces.models import Workspace

    Workspace.objects.select_for_update(no_key=True).get(pk=workspace_id)
    item = get_object_or_404(
        ExternalOperation.objects.select_for_update(),
        pk=operation_id,
        workspace_id=workspace_id,
    )
    admin_destination(actor, workspace_id, item.destination)
    if not reason.strip() or len(reason) > 300:
        raise ValidationError(
            "Record a bounded reconciliation/manual follow-up reason."
        )
    if (
        item.state == "sending"
        and (timezone.now() - item.attempted_at).total_seconds() < 30
    ):
        raise ValidationError("Wait for the bounded in-flight attempt before recovery.")
    if item.state not in {"sending", "uncertain", "manual_follow_up"}:
        raise ValidationError("Choose an uncertain operation.")
    item.state = "uncertain"
    item.claim = None
    item.reason = reason
    item.save(update_fields=["state", "claim", "reason"])
    ExternalAttempt.objects.create(
        workspace_id=workspace_id,
        operation=item,
        actor=actor,
        action="recover",
        outcome="uncertain",
    )
    audit(actor, workspace_id, "external.manual_follow_up", item.pk, {"reason": reason})
    return item


def revoke_source(
    actor, workspace_id, source_id=None, intent_ids=None, scrub=False, system_id=None
):
    """Block pending copies; issue minimal notices for attempted external operations."""
    items = ExternalOperation.objects.filter(workspace_id=workspace_id).exclude(
        operation="withdrawal"
    )
    if system_id is not None:
        items = items.filter(intent__result__system_id=system_id)
    if intent_ids is not None:
        items = items.filter(intent_id__in=intent_ids)
    for item in items.select_for_update():
        encoded = json.dumps(item.payload)
        if (
            source_id is not None
            and str(source_id) not in encoded
            and str(item.intent.result.record.source_id) != str(source_id)
        ):
            continue
        if item.attempted_at is not None:
            notice = queue(
                actor,
                workspace_id,
                item.intent_id,
                item.destination_id,
                previous_id=item.pk,
                withdrawal=True,
            )
            if not item.destination.supports_notices:
                notice.state = "manual_follow_up"
                notice.reason = "Receiver notice support unverified; residual copy requires manual follow-up."
                notice.save(update_fields=["state", "reason"])
        if scrub:
            item.payload = {}
        item.state = "blocked"
        item.claim = None
        item.reason = (
            "Source revoked/deleted; delivery blocked; inspect withdrawal follow-up."
        )
        item.save(update_fields=["payload", "state", "claim", "reason"])
