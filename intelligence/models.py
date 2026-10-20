import uuid
from typing import ClassVar

from django.conf import settings
from django.db import models
from django.utils import timezone

from workspaces.models import Membership, Workspace


class Entity(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    workspace = models.ForeignKey(Workspace, on_delete=models.CASCADE)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        abstract = True


class AuditEvent(Entity):
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL
    )
    action = models.CharField(max_length=80)
    object_id = models.CharField(max_length=80)
    details = models.JSONField(default=dict)


class ConsumerSystem(Entity):
    name = models.CharField(max_length=120)
    state = models.CharField(max_length=30, default="draft")


class Connection(Entity):
    bound_source = models.ForeignKey("Source", null=True, on_delete=models.PROTECT)
    session_ciphertext = models.TextField(blank=True)
    pending_ciphertext = models.TextField(blank=True)
    login_url = models.URLField(max_length=2000, blank=True)
    identity_selector = models.CharField(max_length=300, blank=True)
    expected_identity = models.CharField(max_length=200, blank=True)
    allowed_login_hosts = models.JSONField(default=list)

    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    name = models.CharField(max_length=120)
    domain = models.CharField(max_length=253)
    method = models.CharField(
        max_length=20, choices=[("api", "API"), ("session", "Browser session")]
    )
    secret_ref = models.CharField(max_length=120, blank=True)
    state = models.CharField(max_length=30, default="disconnected")
    verified_identity = models.CharField(max_length=200, blank=True)
    checked_at = models.DateTimeField(null=True)


class Source(Entity):
    name = models.CharField(max_length=120)
    url = models.URLField(max_length=2000)
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    state = models.CharField(max_length=30, default="draft")
    config_version = models.PositiveIntegerField(default=1)
    archive_policy = models.CharField(max_length=20, blank=True)

    class Meta:
        constraints: ClassVar[list] = [
            models.UniqueConstraint(
                fields=["workspace", "url"], name="source_canonical_url_unique"
            )
        ]


class SourceVersion(models.Model):
    source = models.ForeignKey(
        Source, related_name="versions", on_delete=models.CASCADE
    )
    version = models.PositiveIntegerField()
    config = models.JSONField(default=dict)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        constraints: ClassVar[list] = [
            models.UniqueConstraint(
                fields=["source", "version"], name="source_version_unique"
            )
        ]


class SourceGrant(models.Model):
    membership = models.ForeignKey(Membership, on_delete=models.CASCADE)
    source = models.ForeignKey(Source, on_delete=models.CASCADE)
    can_export = models.BooleanField(default=False)

    class Meta:
        constraints: ClassVar[list] = [
            models.UniqueConstraint(
                fields=["membership", "source"], name="source_grant_unique"
            )
        ]


class SystemGrant(models.Model):
    membership = models.ForeignKey(Membership, on_delete=models.CASCADE)
    system = models.ForeignKey(ConsumerSystem, on_delete=models.CASCADE)

    class Meta:
        constraints: ClassVar[list] = [
            models.UniqueConstraint(
                fields=["membership", "system"], name="system_grant_unique"
            )
        ]


class SystemSourceGrant(models.Model):
    system = models.ForeignKey(ConsumerSystem, on_delete=models.CASCADE)
    source = models.ForeignKey(Source, on_delete=models.CASCADE)
    active = models.BooleanField(default=True)

    class Meta:
        constraints: ClassVar[list] = [
            models.UniqueConstraint(
                fields=["system", "source"], name="system_source_unique"
            )
        ]


class Invitation(Entity):
    email = models.EmailField()
    role = models.CharField(max_length=20)
    source_ids = models.JSONField(default=list)
    system_ids = models.JSONField(default=list)
    can_export = models.BooleanField(default=False)
    token_hash = models.CharField(max_length=64, unique=True)
    expires_at = models.DateTimeField()
    accepted_at = models.DateTimeField(null=True)
    invited_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)


class ContentRecord(Entity):
    source = models.ForeignKey(Source, on_delete=models.PROTECT)
    title = models.CharField(max_length=300)
    body = models.TextField(blank=True)
    status = models.CharField(max_length=30, default="captured")
    version = models.PositiveIntegerField(default=1)


class Derivative(models.Model):
    record = models.ForeignKey(ContentRecord, on_delete=models.CASCADE)
    kind = models.CharField(max_length=30)
    value = models.JSONField(default=dict)


class WorkItem(Entity):
    checkpoint = models.JSONField(default=dict)
    limits = models.JSONField(default=dict)
    usage = models.JSONField(default=dict)
    attempts = models.PositiveIntegerField(default=0)
    next_attempt = models.DateTimeField(default=timezone.now)
    started_at = models.DateTimeField(null=True)
    lease_token = models.UUIDField(null=True)
    lease_until = models.DateTimeField(null=True)
    result = models.JSONField(default=dict)

    source = models.ForeignKey(Source, on_delete=models.PROTECT)
    system = models.ForeignKey(ConsumerSystem, null=True, on_delete=models.PROTECT)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    source_version = models.ForeignKey(SourceVersion, on_delete=models.PROTECT)
    kind = models.CharField(max_length=30, default="collection")
    state = models.CharField(max_length=30, default="queued")
    request_key = models.CharField(max_length=100, default=uuid.uuid4)
    reason = models.CharField(max_length=200, blank=True)

    class Meta:
        constraints: ClassVar[list] = [
            models.UniqueConstraint(
                fields=["workspace", "request_key"], name="work_request_unique"
            )
        ]


class DeliveryReceipt(Entity):
    source = models.ForeignKey(Source, on_delete=models.PROTECT)
    system = models.ForeignKey(ConsumerSystem, on_delete=models.PROTECT)
    external_key = models.CharField(max_length=200)
    supports_withdrawal = models.BooleanField(default=False)
    state = models.CharField(max_length=30, default="delivered")


class Withdrawal(Entity):
    receipt = models.OneToOneField(DeliveryReceipt, on_delete=models.PROTECT)
    state = models.CharField(max_length=30)
    reason = models.CharField(max_length=200)


class SourceTest(Entity):
    source = models.ForeignKey(Source, related_name="tests", on_delete=models.CASCADE)
    source_version = models.ForeignKey(SourceVersion, on_delete=models.PROTECT)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    state = models.CharField(max_length=30, default="queued")
    report = models.JSONField(default=dict)


class Schedule(Entity):
    source = models.ForeignKey(Source, on_delete=models.CASCADE)
    enabled = models.BooleanField(default=False)


class BrowserHandoff(Entity):
    connection = models.ForeignKey(Connection, on_delete=models.CASCADE)
    source = models.ForeignKey(Source, on_delete=models.CASCADE)
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    state = models.CharField(max_length=30, default="pending")
    expires_at = models.DateTimeField()
    frame_ciphertext = models.TextField(blank=True)
    reason = models.CharField(max_length=200, blank=True)


class BrowserAction(models.Model):
    handoff = models.ForeignKey(BrowserHandoff, on_delete=models.CASCADE)
    kind = models.CharField(max_length=20)
    payload_ciphertext = models.TextField()
    created_at = models.DateTimeField(default=timezone.now)


class Capture(Entity):
    run = models.ForeignKey(WorkItem, on_delete=models.PROTECT)
    source = models.ForeignKey(Source, on_delete=models.PROTECT)
    url = models.URLField(max_length=2000)
    digest = models.CharField(max_length=64)
    media_type = models.CharField(max_length=100)
    original = models.BinaryField(default=bytes)
    status = models.CharField(max_length=30)
    missing = models.JSONField(default=list)
    record = models.OneToOneField(ContentRecord, null=True, on_delete=models.SET_NULL)

    class Meta:
        constraints: ClassVar[list] = [
            models.UniqueConstraint(
                fields=["run", "url"], name="capture_run_url_unique"
            )
        ]


class ParseResult(models.Model):
    capture = models.OneToOneField(Capture, on_delete=models.CASCADE)
    status = models.CharField(max_length=30)
    segments = models.JSONField(default=list)
    reason = models.CharField(max_length=200, blank=True)
