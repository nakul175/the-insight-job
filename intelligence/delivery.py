"""Validated local sandbox outbox. Never contacts a configured HTTP endpoint."""

import hashlib
import json
from copy import deepcopy
from datetime import timedelta

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.http import Http404
from django.shortcuts import get_object_or_404
from django.utils import timezone

from workspaces.models import Workspace

from .content import validate_fields
from .models import (
    ContentRecord,
    DeliveryIntent,
    Destination,
    DestinationContract,
    DestinationMapping,
    PublishedBundle,
    SandboxMessage,
    SandboxReceipt,
    SystemResult,
)
from .qualification import valid_value
from .routing import live_access, result_is_current, results_for
from .services import audit
from .systems import owned_system


def digest(*parts):
    return hashlib.sha256("|".join(str(p) for p in parts).encode()).hexdigest()


@transaction.atomic
def save_contract(
    actor,
    workspace_id,
    system_id,
    destination_id,
    expected_number,
    fields,
    safe_upsert=True,
    supports_withdrawal=False,
    update_policy="upsert",
):
    system = owned_system(actor, workspace_id, system_id)
    destination = get_object_or_404(
        Destination.objects.select_for_update(),
        pk=destination_id,
        system=system,
        workspace_id=workspace_id,
        state="test_passed",
    )
    latest = destination.contracts.order_by("-number").first()
    if expected_number != (latest.number if latest else 0):
        raise ValidationError("Destination contract changed; reload before saving.")
    if (
        type(safe_upsert) is not bool
        or type(supports_withdrawal) is not bool
        or update_policy not in ("upsert", "create")
    ):
        raise ValidationError(
            "Declare safe-upsert/withdrawal capabilities and upsert/create update policy."
        )
    contract = DestinationContract.objects.create(
        destination=destination,
        number=expected_number + 1,
        fields=validate_fields(fields),
        actor=actor,
        safe_upsert=safe_upsert,
        supports_withdrawal=supports_withdrawal,
        update_policy=update_policy,
        mode="local_sandbox",
    )
    audit(
        actor,
        workspace_id,
        "destination.local_contract_saved",
        destination.pk,
        {
            "version": contract.number,
            "mode": "local_sandbox",
            "external_transmission": False,
        },
    )
    return contract


def validate_mapping(schema_version, contract, entries):
    if not isinstance(entries, list) or not 1 <= len(entries) <= 50:
        raise ValidationError("Provide 1–50 target/source/transform mappings.")
    sources = {f["key"]: f for f in schema_version.fields}
    targets = {f["key"]: f for f in contract.fields}
    seen = set()
    for entry in entries:
        if (
            not isinstance(entry, dict)
            or set(entry) != {"target", "source", "transform"}
            or not all(isinstance(entry[k], str) for k in entry)
        ):
            raise ValidationError(
                "Each mapping requires string target, source and transform."
            )
        target, source = entry["target"], entry["source"]
        if target not in targets or source not in sources or target in seen:
            raise ValidationError(
                f"Target {target}: unavailable source/target or duplicate mapping."
            )
        seen.add(target)
        left, right = sources[source], targets[target]
        if entry["transform"] == "identity":
            if not (
                left["type"] == right["type"]
                or left["type"] == "integer"
                and right["type"] == "number"
            ) or left.get("unit", "") != right.get("unit", ""):
                raise ValidationError(f"Target {target}: incompatible type or unit.")
            if left["type"] == "enum" and not set(left["allowed_values"]) <= set(
                right["allowed_values"]
            ):
                raise ValidationError(
                    f"Target {target}: target enum excludes source values."
                )
        elif entry["transform"] == "text":
            if right["type"] != "text" or right.get("unit"):
                raise ValidationError(
                    f"Target {target}: text transformation requires a unit-free text target."
                )
        else:
            raise ValidationError(
                f"Target {target}: supported transformations are identity and text."
            )
    missing = [
        key for key, field in targets.items() if field["required"] and key not in seen
    ]
    if missing:
        raise ValidationError("Unmapped required targets: " + ", ".join(missing))
    return entries


@transaction.atomic
def save_mapping(
    actor, workspace_id, system_id, schema_version_id, contract_id, entries
):
    system = owned_system(actor, workspace_id, system_id)
    from .models import SchemaVersion

    definition = get_object_or_404(
        SchemaVersion,
        pk=schema_version_id,
        schema__system=system,
        schema__workspace_id=workspace_id,
        schema__state="published",
    )
    contract = get_object_or_404(
        DestinationContract,
        pk=contract_id,
        destination__system=system,
        destination__workspace_id=workspace_id,
    )
    if (
        contract.number
        != contract.destination.contracts.order_by("-number").first().number
    ):
        raise ValidationError("Map the current destination contract.")
    mapping = DestinationMapping.objects.create(
        workspace_id=workspace_id,
        system=system,
        schema_version=definition,
        contract=contract,
        entries=validate_mapping(definition, contract, entries),
        actor=actor,
    )
    audit(
        actor,
        workspace_id,
        "destination.mapping_saved",
        mapping.pk,
        {"schema_version": definition.number, "contract_version": contract.number},
    )
    return mapping


def mapped_payload(result, mapping):
    contract = mapping.contract
    if (
        mapping.system_id != result.system_id
        or mapping.workspace_id != result.workspace_id
        or mapping.schema_version_id != result.schema_version_id
        or contract.destination.system_id != result.system_id
        or contract.mode != "local_sandbox"
    ):
        raise ValidationError("Mapping/system/schema/contract pins are inconsistent.")
    latest = contract.destination.contracts.order_by("-number").first()
    if not latest or latest.pk != contract.pk:
        raise ValidationError(
            "Destination schema changed; update mapping before delivery."
        )
    validate_mapping(mapping.schema_version, contract, mapping.entries)
    definitions = {f["key"]: f for f in mapping.schema_version.fields}
    targets = {f["key"]: f for f in contract.fields}
    payload = {}
    for entry in mapping.entries:
        target, source = entry["target"], entry["source"]
        fact = result.fields.get(source, {})
        if fact.get("status") != "known" or not valid_value(
            fact.get("value"), definitions[source]
        ):
            if targets[target]["required"]:
                raise ValidationError(
                    f"Target {target}: required source field missing or conflicting."
                )
            continue
        if fact.get("unit", "") != definitions[source].get("unit", ""):
            raise ValidationError(f"Target {target}: source units are invalid.")
        value = fact["value"]
        if entry["transform"] == "text":
            value = str(value) + (" " + fact["unit"] if fact.get("unit") else "")
        if not valid_value(value, targets[target]):
            raise ValidationError(
                f"Target {target}: transformed value is incompatible."
            )
        payload[target] = value
    return payload


def validate_result(actor, result, bundle):
    from .recovery import delivery_held

    if delivery_held(result.workspace_id):
        raise ValidationError("Recovery reconciliation hold blocks all new delivery.")
    live_access(actor, result)
    if (result.analysis or result.content_version).status not in {"parsed", "partial"}:
        raise ValidationError("Immutable base source security/parsing gate failed.")
    if (
        result.preview
        or not result.bundle_id
        or result.decision != "pass"
        or not result_is_current(result)
    ):
        raise ValidationError(
            "Only current, non-preview passing results can create sendable intents."
        )
    if result.assigned_to_id and result.review_state != "resolved":
        raise ValidationError("Resolve assigned review before final storage.")
    if (
        bundle.system_id != result.system_id
        or bundle.configuration_id != result.configuration_id
        or bundle.workspace_id != result.workspace_id
    ):
        raise ValidationError("Published delivery bundle does not match the result.")
    if (
        not result.schema_version
        or result.schema_version.schema.state != "published"
        or bundle.mapping.contract.destination.state != "test_passed"
    ):
        raise ValidationError("Schema or destination validation is no longer valid.")
    for field in result.schema_version.fields:
        fact = result.fields.get(field["key"], {})
        if field["required"] and (
            fact.get("status") != "known" or not valid_value(fact.get("value"), field)
        ):
            raise ValidationError(
                "Required facts are incomplete; no delivery intent created."
            )
    return mapped_payload(result, bundle.mapping)


@transaction.atomic
def store_result(actor, workspace_id, result_id, expected_version):
    Workspace.objects.select_for_update(no_key=True).get(pk=workspace_id)
    result = get_object_or_404(
        results_for(actor, workspace_id).select_for_update(), pk=result_id
    )
    owned_system(actor, workspace_id, result.system_id)
    if result.version != expected_version:
        raise ValidationError("Record changed; compare and reload before storage.")
    bundle = result.bundle
    if not bundle:
        raise ValidationError("Publish a tested bundle first.")
    payload = validate_result(actor, result, bundle)
    destination = bundle.mapping.contract.destination
    key = digest(result.pk, result.version, destination.pk)
    external_parts = [result.record_id, result.system_id, destination.pk]
    if bundle.mapping.contract.update_policy == "create":
        external_parts.extend([result.content_version_id, result.version])
    snapshot = {
        "fields": deepcopy(result.fields),
        "relevance": deepcopy(result.relevance),
        "qualification": deepcopy(result.qualification),
        "content_version": str(result.content_version_id),
        "analysis": str(result.analysis_id) if result.analysis_id else None,
        "classification_version": result.classification_version,
        "schema_version": result.schema_version_id,
        "configuration_version": result.configuration.number,
        "bundle": str(bundle.pk),
        "result_version": result.version,
        "review_changes": [
            str(pk) for pk in result.changes.values_list("pk", flat=True)
        ],
        "model": "none; deterministic literal-terms-v1/exact_label",
        "mode": "local_sandbox",
    }
    intent, created = DeliveryIntent.objects.get_or_create(
        idempotency_key=key,
        defaults={
            "workspace_id": workspace_id,
            "result": result,
            "result_version": result.version,
            "bundle": bundle,
            "destination": destination,
            "actor": actor,
            "payload": payload,
            "snapshot": snapshot,
            "external_key": digest(*external_parts),
            "operation": bundle.mapping.contract.update_policy,
        },
    )
    if created:
        result.stored_version = result.version
        result.save(update_fields=["stored_version"])
        audit(
            actor,
            workspace_id,
            "result.stored_with_local_intent",
            result.pk,
            {
                "intent": str(intent.pk),
                "version": result.version,
                "external_transmission": False,
            },
        )
    return intent, created


def payload_digest(intent):
    return digest(
        intent.operation,
        json.dumps(intent.payload, sort_keys=True, separators=(",", ":")),
    )


def local_receiver(intent):
    """Idempotent workspace-local receiver; no HTTP/network implementation exists."""
    receipt = SandboxReceipt.objects.filter(
        idempotency_key=intent.idempotency_key,
        payload_digest=payload_digest(intent),
    ).first()
    if receipt:
        return {
            "receipt": str(receipt.pk),
            "status": "already_applied",
            "mode": "local_sandbox",
        }
    if (
        not intent.bundle.mapping.contract.safe_upsert
        and SandboxReceipt.objects.filter(
            idempotency_key=intent.idempotency_key
        ).exists()
    ):
        raise ValidationError(
            "Changed payload requires safe upsert reconciliation, not blind deduplication."
        )
    message, _created = SandboxMessage.objects.get_or_create(
        workspace_id=intent.workspace_id,
        destination=intent.destination,
        external_key=intent.external_key,
    )
    if intent.operation == "withdraw":
        message.withdrawn = True
        message.payload = {}
    else:
        message.payload = intent.payload
        message.withdrawn = False
    message.save(update_fields=["payload", "withdrawn"])
    receipt = SandboxReceipt.objects.create(
        workspace_id=intent.workspace_id,
        message=message,
        idempotency_key=intent.idempotency_key,
        payload_digest=payload_digest(intent),
        operation=intent.operation,
    )
    return {
        "receipt": str(receipt.pk),
        "status": "withdrawn" if intent.operation == "withdraw" else "stored",
        "mode": "local_sandbox",
    }


@transaction.atomic
def deliver(intent_id, transport=None):
    original = get_object_or_404(DeliveryIntent, pk=intent_id)
    Workspace.objects.select_for_update(no_key=True).get(pk=original.workspace_id)
    intent = DeliveryIntent.objects.select_for_update().get(pk=intent_id)
    from .recovery import delivery_held

    if delivery_held(intent.workspace_id):
        return intent
    current = SystemResult.objects.select_for_update().get(pk=intent.result_id)
    current.record = ContentRecord.objects.select_for_update().get(pk=current.record_id)
    intent.result = current
    if intent.state not in {"pending", "retry"} or intent.next_attempt > timezone.now():
        return intent
    if intent.attempts >= intent.attempt_budget:
        intent.state, intent.reason = (
            "action_required",
            "Retry budget exhausted; owner action required",
        )
    else:
        try:
            if intent.operation == "withdraw":
                from .permissions import member

                member(intent.actor, intent.workspace_id, admin=True)
                if not intent.bundle.mapping.contract.supports_withdrawal:
                    intent.state, intent.reason = (
                        "manual_follow_up",
                        "Local destination lacks withdrawal support; residual copy remains",
                    )
                    intent.save(update_fields=["state", "reason"])
                    return intent
            else:
                current_payload = validate_result(
                    intent.actor, intent.result, intent.bundle
                )
                if (
                    intent.result.version != intent.result_version
                    or current_payload != intent.payload
                ):
                    raise ValidationError(
                        "Result or mapping changed; review the stored delivery snapshot."
                    )
            intent.attempts += 1
            response = (transport or local_receiver)(intent)
            if (
                not isinstance(response, dict)
                or response.get("mode") != "local_sandbox"
                or response.get("status")
                not in {"stored", "withdrawn", "already_applied"}
            ):
                raise ValidationError("Unsupported receiver acknowledgement.")
            intent.response = {
                k: str(response[k])[:120]
                for k in ["receipt", "status", "mode"]
                if k in response
            }
            intent.state, intent.reason = (
                "delivered_sandbox",
                "Recorded in local sandbox only; configured HTTP endpoint not contacted",
            )
        except OSError:
            if intent.bundle.mapping.contract.safe_upsert:
                receipt = SandboxReceipt.objects.filter(
                    idempotency_key=intent.idempotency_key,
                    payload_digest=payload_digest(intent),
                ).first()
                if receipt:
                    intent.state, intent.reason = (
                        "delivered_sandbox",
                        "Reconciled local receipt after uncertain acknowledgement",
                    )
                    intent.response = {
                        "receipt": str(receipt.pk),
                        "mode": "local_sandbox",
                        "status": "reconciled",
                    }
                else:
                    intent.state, intent.reason = (
                        "retry",
                        "No local receipt; safe idempotent retry scheduled",
                    )
            else:
                intent.state, intent.reason = (
                    "ambiguous",
                    "Uncertain write without safe deduplication; review required, no blind retry",
                )
        except (PermissionDenied, ValidationError, Http404):
            intent.state, intent.reason = (
                "action_required",
                "Current permissions, eligibility or mapping require owner review",
            )
        if intent.state == "retry":
            intent.next_attempt = timezone.now() + timedelta(
                seconds=min(60, 2**intent.attempts)
            )
            if intent.attempts >= intent.attempt_budget:
                intent.state, intent.reason = (
                    "action_required",
                    "Retry budget exhausted; owner action required",
                )
    intent.save(
        update_fields=["state", "reason", "attempts", "next_attempt", "response"]
    )
    audit(
        intent.actor,
        intent.workspace_id,
        "delivery.local_attempt",
        intent.pk,
        {
            "state": intent.state,
            "attempts": intent.attempts,
            "external_transmission": False,
        },
    )
    return intent


@transaction.atomic
def retry_delivery(
    actor, workspace_id, intent_id, reason, bundle_id=None, attempt_budget=None
):
    Workspace.objects.select_for_update(no_key=True).get(pk=workspace_id)
    intent = get_object_or_404(
        DeliveryIntent.objects.select_for_update(),
        pk=intent_id,
        workspace_id=workspace_id,
    )
    owned_system(actor, workspace_id, intent.result.system_id)
    if (
        intent.state not in {"retry", "action_required"}
        or not isinstance(reason, str)
        or not reason.strip()
        or len(reason) > 500
    ):
        raise ValidationError(
            "Only retryable owner-reviewed failures may retry; ambiguous writes require reconciliation."
        )
    if bundle_id:
        candidate = get_object_or_404(
            PublishedBundle,
            pk=bundle_id,
            workspace_id=workspace_id,
            system=intent.result.system,
            configuration=intent.result.configuration,
        )
        if candidate.mapping.contract.destination_id != intent.destination_id:
            raise ValidationError("A retry cannot silently change destination.")
        intent.bundle = candidate
    if intent.operation == "withdraw":
        from .permissions import member

        member(actor, workspace_id, admin=True)
        if not intent.bundle.mapping.contract.supports_withdrawal:
            raise ValidationError(
                "Withdrawal unsupported; manual follow-up remains required."
            )
        payload = {}
    else:
        payload = validate_result(actor, intent.result, intent.bundle)
        if intent.result.version != intent.result_version:
            raise ValidationError("Result changed; store a new version instead.")
    if attempt_budget is not None:
        if (
            type(attempt_budget) is not int
            or not intent.attempts < attempt_budget <= 10
        ):
            raise ValidationError("Raise retry budget above attempts and at most 10.")
        intent.attempt_budget = attempt_budget
    if intent.attempts >= intent.attempt_budget:
        raise ValidationError(
            "Retry budget exhausted; explicitly raise it within authority."
        )
    intent.payload = payload
    intent.actor = actor
    intent.state, intent.reason, intent.next_attempt = "pending", reason, timezone.now()
    intent.save()
    audit(
        actor,
        workspace_id,
        "delivery.retry_authorized",
        intent.pk,
        {
            "reason": reason,
            "budget": intent.attempt_budget,
            "bundle": str(intent.bundle_id),
        },
    )
    return intent


@transaction.atomic
def queue_withdrawals(actor, workspace_id, source_id, system_id=None):
    from .permissions import member

    Workspace.objects.select_for_update(no_key=True).get(pk=workspace_id)
    member(actor, workspace_id, admin=True)
    originals = DeliveryIntent.objects.filter(
        workspace_id=workspace_id,
        state__in=["delivered_sandbox", "ambiguous", "retry", "action_required"],
        attempts__gt=0,
    ).exclude(operation="withdraw")
    if system_id:
        originals = originals.filter(result__system_id=system_id)
    queued = []
    for original in originals.iterator(chunk_size=100):
        evidence_sources = {
            str(candidate.get("source"))
            for fact in original.snapshot.get("fields", {}).values()
            if isinstance(fact, dict)
            for candidate in fact.get("candidates", [])
            if isinstance(candidate, dict)
        }
        if (
            str(original.result.record.source_id) != str(source_id)
            and str(source_id) not in evidence_sources
        ):
            continue
        supported = original.bundle.mapping.contract.supports_withdrawal
        intent, _created = DeliveryIntent.objects.get_or_create(
            idempotency_key=digest(original.idempotency_key, "withdraw"),
            defaults={
                "workspace_id": workspace_id,
                "result": original.result,
                "result_version": original.result_version,
                "bundle": original.bundle,
                "destination": original.destination,
                "actor": actor,
                "payload": {},
                "snapshot": {"withdraws": str(original.pk), "mode": "local_sandbox"},
                "external_key": original.external_key,
                "operation": "withdraw",
                "state": "pending" if supported else "manual_follow_up",
                "reason": "Permission revoked: withdraw local copy"
                if supported
                else "No withdrawal support; residual local copy needs manual follow-up",
            },
        )
        queued.append(intent)
    audit(
        actor,
        workspace_id,
        "delivery.local_withdrawals_queued",
        source_id,
        {"count": len(queued), "external_deletion_claimed": False},
    )
    from .external_delivery import revoke_source

    revoke_source(actor, workspace_id, source_id=source_id, system_id=system_id)
    return queued


@transaction.atomic
def reconcile_delivery(actor, workspace_id, intent_id, reason):
    """Confirm an existing local receipt; this action never invokes a receiver."""
    Workspace.objects.select_for_update(no_key=True).get(pk=workspace_id)
    intent = get_object_or_404(
        DeliveryIntent.objects.select_for_update(),
        pk=intent_id,
        workspace_id=workspace_id,
    )
    owned_system(actor, workspace_id, intent.result.system_id)
    from .permissions import member

    try:
        live_access(actor, intent.result, allow_inactive=True)
    except PermissionDenied:
        if member(actor, workspace_id).role != "administrator":
            raise
    if intent.state == "delivered_sandbox":
        return intent
    if (
        intent.state not in {"ambiguous", "retry", "action_required"}
        or not isinstance(reason, str)
        or not reason.strip()
        or len(reason) > 1000
    ):
        raise ValidationError(
            "Choose an uncertain/failed delivery and record a reconciliation reason."
        )
    receipt = SandboxReceipt.objects.filter(
        workspace_id=workspace_id,
        idempotency_key=intent.idempotency_key,
        payload_digest=payload_digest(intent),
        operation=intent.operation,
        message__workspace_id=workspace_id,
        message__destination_id=intent.destination_id,
        message__external_key=intent.external_key,
    ).first()
    if receipt:
        intent.state = "delivered_sandbox"
        intent.reason = (
            "Confirmed an existing local sandbox receipt; no second receiver write"
        )
        intent.response = {
            "receipt": str(receipt.pk),
            "mode": "local_sandbox",
            "status": "confirmed_existing_receipt",
        }
    elif intent.bundle.mapping.contract.safe_upsert:
        intent.state = "action_required"
        intent.reason = "No matching local receipt. Owner may authorise a bounded safe-upsert retry after current validation."
    else:
        intent.state = "ambiguous"
        intent.reason = "No confirmed receipt and no safe deduplication. Review required; blind retry is blocked."
    intent.save(update_fields=["state", "reason", "response"])
    audit(
        actor,
        workspace_id,
        "delivery.reconciled",
        intent.pk,
        {"state": intent.state, "reason": reason, "receiver_called": False},
    )
    return intent
