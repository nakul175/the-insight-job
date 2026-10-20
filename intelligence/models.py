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
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, on_delete=models.PROTECT
    )
    purpose = models.CharField(max_length=500, blank=True)
    config_version = models.PositiveIntegerField(default=0)
    name = models.CharField(max_length=120)
    state = models.CharField(max_length=30, default="draft")


class Connection(Entity):
    current_handoff_id = models.UUIDField(null=True, editable=False)
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
    current_analysis = models.ForeignKey(
        "ContentAnalysis",
        null=True,
        on_delete=models.PROTECT,
        related_name="current_records",
    )
    canonical_url = models.URLField(max_length=2000, blank=True)
    identity = models.ForeignKey("ContentIdentity", null=True, on_delete=models.PROTECT)
    classification_version = models.PositiveIntegerField(default=1)

    class Meta:
        constraints: ClassVar[list] = [
            models.UniqueConstraint(
                fields=["source", "canonical_url"],
                condition=~models.Q(canonical_url=""),
                name="content_source_url_unique",
            )
        ]

    source = models.ForeignKey(Source, on_delete=models.PROTECT)
    title = models.CharField(max_length=300)
    body = models.TextField(blank=True)
    status = models.CharField(max_length=30, default="captured")
    version = models.PositiveIntegerField(default=1)


class Derivative(models.Model):
    content_version = models.PositiveIntegerField(default=1)
    stale = models.BooleanField(default=False)
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
    final_url = models.URLField(max_length=2000, blank=True)
    run = models.ForeignKey(WorkItem, on_delete=models.PROTECT)
    source = models.ForeignKey(Source, on_delete=models.PROTECT)
    url = models.URLField(max_length=2000)
    digest = models.CharField(max_length=64)
    media_type = models.CharField(max_length=100)
    original = models.BinaryField(default=bytes)
    status = models.CharField(max_length=30)
    missing = models.JSONField(default=list)
    record = models.ForeignKey(
        ContentRecord, null=True, on_delete=models.SET_NULL, related_name="captures"
    )
    content_version = models.ForeignKey(
        "ContentVersion", null=True, on_delete=models.PROTECT
    )

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


class ContentIdentity(Entity):
    fingerprint = models.CharField(max_length=64)

    class Meta:
        constraints: ClassVar[list] = [
            models.UniqueConstraint(
                fields=["workspace", "fingerprint"], name="identity_fingerprint_unique"
            )
        ]


class ContentVersion(Entity):
    record = models.ForeignKey(
        ContentRecord, on_delete=models.PROTECT, related_name="revisions"
    )
    number = models.PositiveIntegerField()
    digest = models.CharField(max_length=64)
    media_type = models.CharField(max_length=100)
    language = models.CharField(max_length=50, default="unknown")
    completeness = models.CharField(max_length=30)
    metadata = models.JSONField(default=dict)
    summary = models.JSONField(default=dict)
    body = models.TextField(blank=True)
    segments = models.JSONField(default=list)
    changes = models.JSONField(default=dict)

    class Meta:
        constraints: ClassVar[list] = [
            models.UniqueConstraint(
                fields=["record", "number"], name="content_revision_unique"
            )
        ]


class ContentType(models.Model):
    record = models.ForeignKey(
        ContentRecord, on_delete=models.CASCADE, related_name="type_assignments"
    )
    key = models.CharField(max_length=60)

    class Meta:
        constraints: ClassVar[list] = [
            models.UniqueConstraint(
                fields=["record", "key"], name="content_type_unique"
            )
        ]


class ClassificationChange(Entity):
    record = models.ForeignKey(ContentRecord, on_delete=models.PROTECT)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    version = models.PositiveIntegerField()
    original = models.JSONField(default=list)
    selected = models.JSONField(default=list)
    reason = models.TextField()


class ContentSchema(Entity):
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    name = models.CharField(max_length=120)
    key = models.SlugField(max_length=60)
    scope = models.CharField(max_length=20, default="type")
    system = models.ForeignKey(ConsumerSystem, null=True, on_delete=models.PROTECT)
    state = models.CharField(max_length=20, default="draft")
    published_version = models.PositiveIntegerField(null=True)

    class Meta:
        constraints: ClassVar[list] = [
            models.UniqueConstraint(
                fields=["workspace", "key"], name="schema_key_unique"
            )
        ]


class SchemaVersion(models.Model):
    schema = models.ForeignKey(
        ContentSchema, on_delete=models.PROTECT, related_name="versions"
    )
    number = models.PositiveIntegerField()
    fields = models.JSONField(default=list)
    created_at = models.DateTimeField(default=timezone.now)
    state = models.CharField(max_length=20, default="draft")

    class Meta:
        constraints: ClassVar[list] = [
            models.UniqueConstraint(
                fields=["schema", "number"], name="schema_revision_unique"
            )
        ]


class ExtractionJob(Entity):
    analysis = models.ForeignKey("ContentAnalysis", null=True, on_delete=models.PROTECT)
    record = models.ForeignKey(ContentRecord, on_delete=models.PROTECT)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    classification_version = models.PositiveIntegerField()
    content_version = models.ForeignKey(
        ContentVersion, null=True, on_delete=models.PROTECT
    )
    schema_version = models.ForeignKey(
        SchemaVersion, null=True, on_delete=models.PROTECT
    )
    state = models.CharField(max_length=30, default="queued")
    reason = models.CharField(max_length=200, blank=True)
    result = models.JSONField(default=dict)
    preview = models.BooleanField(default=True)


class DuplicateDecision(Entity):
    left = models.ForeignKey(
        ContentRecord, on_delete=models.PROTECT, related_name="duplicate_left"
    )
    right = models.ForeignKey(
        ContentRecord, on_delete=models.PROTECT, related_name="duplicate_right"
    )
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    decision = models.CharField(max_length=20)
    reason = models.TextField()

    class Meta:
        constraints: ClassVar[list] = [
            models.UniqueConstraint(
                fields=["left", "right"], name="duplicate_pair_unique"
            )
        ]


class ContentAnalysis(Entity):
    content_version = models.ForeignKey(
        ContentVersion, on_delete=models.PROTECT, related_name="analyses"
    )
    capture = models.ForeignKey(Capture, on_delete=models.PROTECT)
    number = models.PositiveIntegerField()
    status = models.CharField(max_length=30)
    reason = models.CharField(max_length=200, blank=True)
    body = models.TextField(blank=True)
    segments = models.JSONField(default=list)
    summary = models.JSONField(default=dict)
    completeness = models.CharField(max_length=30)

    class Meta:
        ordering = ("-number",)
        constraints: ClassVar[list] = [
            models.UniqueConstraint(
                fields=["content_version", "number"], name="content_analysis_unique"
            )
        ]


class SystemConfiguration(models.Model):
    system = models.ForeignKey(
        ConsumerSystem, related_name="configurations", on_delete=models.PROTECT
    )
    number = models.PositiveIntegerField()
    config = models.JSONField(default=dict)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        constraints: ClassVar[list] = [
            models.UniqueConstraint(
                fields=["system", "number"], name="system_config_number_unique"
            )
        ]


class EvaluationSet(Entity):
    schema = models.ForeignKey(
        ContentSchema, on_delete=models.PROTECT, related_name="evaluation_sets"
    )
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    name = models.CharField(max_length=120)
    version = models.PositiveIntegerField(default=0)


class EvaluationVersion(models.Model):
    sample_set = models.ForeignKey(
        EvaluationSet, on_delete=models.PROTECT, related_name="versions"
    )
    number = models.PositiveIntegerField()
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        constraints: ClassVar[list] = [
            models.UniqueConstraint(
                fields=["sample_set", "number"], name="evaluation_version_unique"
            )
        ]


class EvaluationSample(models.Model):
    version = models.ForeignKey(
        EvaluationVersion, on_delete=models.PROTECT, related_name="samples"
    )
    content_version = models.ForeignKey(ContentVersion, on_delete=models.PROTECT)
    analysis = models.ForeignKey(ContentAnalysis, null=True, on_delete=models.PROTECT)
    expected_fields = models.JSONField(default=dict)
    expected_routing = models.CharField(max_length=20)
    expected_qualification = models.CharField(max_length=20)


class SchemaCheck(Entity):
    schema_version = models.ForeignKey(SchemaVersion, on_delete=models.PROTECT)
    evaluation_version = models.ForeignKey(EvaluationVersion, on_delete=models.PROTECT)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    passed = models.BooleanField(default=False)
    results = models.JSONField(default=list)


class Destination(Entity):
    system = models.ForeignKey(
        ConsumerSystem, on_delete=models.PROTECT, related_name="destinations"
    )
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    kind = models.CharField(max_length=30, default="sandbox_http")
    endpoint = models.URLField(max_length=2000)
    secret_ref = models.CharField(max_length=120, blank=True)
    state = models.CharField(max_length=30, default="draft")
    version = models.PositiveIntegerField(default=1)
    test_report = models.JSONField(default=dict)


class EnrichmentPolicy(models.Model):
    system = models.ForeignKey(
        ConsumerSystem, on_delete=models.PROTECT, related_name="enrichment_policies"
    )
    number = models.PositiveIntegerField()
    config = models.JSONField(default=dict)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        constraints: ClassVar[list] = [
            models.UniqueConstraint(
                fields=["system", "number"], name="enrichment_policy_number_unique"
            )
        ]


class EnrichmentAttempt(Entity):
    policy = models.ForeignKey(EnrichmentPolicy, on_delete=models.PROTECT)
    record = models.ForeignKey(ContentRecord, on_delete=models.PROTECT)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    trigger = models.CharField(max_length=500)
    unresolved_fields = models.JSONField(default=list)
    pending = models.JSONField(default=list)
    visited = models.JSONField(default=list)
    coverage = models.JSONField(default=list)
    usage = models.JSONField(default=dict)
    state = models.CharField(max_length=30, default="queued")
    reason = models.CharField(max_length=200, blank=True)
    previous = models.ForeignKey("self", null=True, on_delete=models.PROTECT)
