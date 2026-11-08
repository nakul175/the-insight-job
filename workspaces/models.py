import uuid
from typing import ClassVar

from django.conf import settings
from django.db import models


class Workspace(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    permission_revision = models.PositiveBigIntegerField(default=0)
    name = models.CharField(max_length=120)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering: ClassVar[list] = ["name", "id"]

    def __str__(self):
        return self.name


class Membership(models.Model):
    active = models.BooleanField(default=True)
    generation = models.PositiveBigIntegerField(default=0)
    role = models.CharField(
        max_length=20,
        choices=[
            ("administrator", "Administrator"),
            ("operator", "Operator"),
            ("reviewer", "Reviewer"),
            ("viewer", "Viewer"),
        ],
        default="viewer",
    )
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    workspace = models.ForeignKey(
        Workspace, on_delete=models.CASCADE, related_name="memberships"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints: ClassVar[list] = [
            models.UniqueConstraint(
                fields=["user", "workspace"], name="unique_workspace_member"
            )
        ]


class WorkspaceSelection(models.Model):
    """Minimal audit: no passwords, tokens, IPs or content payloads."""

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    workspace = models.ForeignKey(Workspace, on_delete=models.CASCADE)
    created_at = models.DateTimeField(auto_now_add=True)


class PortalIdentity(models.Model):
    """Explicit reviewed subject mapping. Never discovered by email."""

    issuer = models.CharField(max_length=250)
    subject = models.CharField(max_length=250)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    workspace = models.ForeignKey(Workspace, on_delete=models.PROTECT)
    reviewer = models.BooleanField(default=False)
    review_reference = models.CharField(max_length=250)

    class Meta:
        constraints: ClassVar[list] = [
            models.UniqueConstraint(
                fields=["issuer", "subject", "workspace"], name="source_portal_subject"
            ),
            models.UniqueConstraint(
                fields=["user", "workspace"], name="source_portal_user"
            ),
        ]


class PortalPermissionReceipt(models.Model):
    request_id = models.UUIDField(primary_key=True)
    result = models.JSONField()
    created_at = models.DateTimeField(auto_now_add=True)
