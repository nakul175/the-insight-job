from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction

from .collector import fetch_public, sample_report
from .models import Source, SourceTest
from .permissions import member, sources_for
from .services import audit


def test_source(actor, source):
    member(actor, source.workspace_id, write=True)
    if not sources_for(actor, source.workspace_id).filter(pk=source.pk).exists():
        raise PermissionDenied()
    if source.state == "archived":
        raise ValidationError("Restore the source before testing.")
    source = Source.objects.get(pk=source.pk)
    version = source.versions.get(version=source.config_version)
    test = SourceTest.objects.create(
        workspace=source.workspace,
        source=source,
        source_version=version,
        actor=actor,
        state="running",
    )
    config = version.config
    try:
        if config.get("access") != "public":
            report = {
                "state": "unsupported",
                "formats": [],
                "missing": [
                    {
                        "reason": "This access connector is not enabled; source remains draft."
                    }
                ],
                "downstream_delivery": False,
            }
        else:
            report = sample_report(fetch_public(source.url, source.url, config))
        test.report = report
        test.state = report["state"]
    except (ValidationError, OSError, TimeoutError):
        test.state = "failed"
        test.report = {
            "state": "failed",
            "reason": "Sample could not be captured within the configured access and network limits.",
            "downstream_delivery": False,
        }
    test.save(update_fields=["state", "report"])
    audit(
        actor,
        source.workspace_id,
        "source.tested",
        source.id,
        {"test_id": str(test.id), "version": version.version, "state": test.state},
    )
    return test


@transaction.atomic
def activate(actor, source, test_id):
    member(actor, source.workspace_id, write=True)
    if not sources_for(actor, source.workspace_id).filter(pk=source.pk).exists():
        raise PermissionDenied()
    source = Source.objects.select_for_update().get(pk=source.pk)
    test = SourceTest.objects.get(
        pk=test_id, source=source, source_version__version=source.config_version
    )
    config = test.source_version.config
    acceptable = test.state == "passed" or (
        test.state == "partial"
        and config.get("partial_policy") == "accept_documented"
        and config.get("partial_reason", "").strip()
    )
    if not acceptable or source.state == "archived":
        raise ValidationError(
            "Resolve the test error or document and retest an acceptable partial-capture policy before activation."
        )
    if config.get("access") != "public":
        raise ValidationError("The selected access connector is not enabled.")
    source.state = "active"
    source.save(update_fields=["state"])
    audit(
        actor,
        source.workspace_id,
        "source.activated",
        source.id,
        {"version": source.config_version, "test_id": str(test.id)},
    )
    return source
