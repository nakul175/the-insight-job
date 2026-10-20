"""Destination registration and explicit non-transmitting contract tests."""

from django.core.exceptions import ValidationError
from django.db import transaction
from django.shortcuts import get_object_or_404

from .models import Destination
from .services import audit
from .systems import owned_system
from .urlsafety import canonical_url


@transaction.atomic
def register_destination(
    actor, workspace_id, system_id, endpoint, kind="sandbox_http", secret_ref=""
):
    system = owned_system(actor, workspace_id, system_id)
    if kind != "sandbox_http":
        raise ValidationError("Only the declared sandbox HTTP contract is supported.")
    endpoint = canonical_url(endpoint, resolve=False)
    if (
        not isinstance(secret_ref, str)
        or len(secret_ref) > 120
        or any(
            c
            not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-:/"
            for c in secret_ref
        )
    ):
        raise ValidationError(
            "Use an opaque secret reference, never a credential value."
        )
    destination = Destination.objects.create(
        workspace_id=workspace_id,
        system=system,
        owner=actor,
        kind=kind,
        endpoint=endpoint,
        secret_ref=secret_ref,
    )
    audit(
        actor,
        workspace_id,
        "destination.registered",
        destination.pk,
        {"system": str(system.pk), "mode": "declared_test"},
    )
    return destination


@transaction.atomic
def test_destination(actor, workspace_id, system_id, destination_id):
    owned_system(actor, workspace_id, system_id)
    destination = get_object_or_404(
        Destination.objects.select_for_update(),
        pk=destination_id,
        workspace_id=workspace_id,
        system_id=system_id,
    )
    try:
        canonical_url(destination.endpoint, resolve=False)
        if destination.secret_ref:
            raise ValidationError(
                "Secret resolution/credential verification is unavailable in declared test mode."
            )
        destination.test_report = {
            "mode": "declared_test",
            "transmitted": False,
            "reachable": "unverified",
            "credentials": "anonymous",
            "sample": {"external_key": "synthetic-test", "fields": {"example": "test"}},
            "version": destination.version,
            "checks": ["public endpoint syntax", "sandbox contract shape"],
            "production_delivery": False,
        }
        destination.state = "test_passed"
    except ValidationError as error:
        destination.state = "blocked"
        destination.test_report = {
            "mode": "declared_test",
            "transmitted": False,
            "reason": "; ".join(error.messages),
            "version": destination.version,
        }
    destination.save(update_fields=["state", "test_report"])
    audit(
        actor,
        workspace_id,
        "destination.tested",
        destination.pk,
        {"state": destination.state, "mode": "declared_test", "transmitted": False},
    )
    return destination
