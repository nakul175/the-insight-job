import uuid
from typing import ClassVar

from django.conf import settings
from django.db import models


class Workspace(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=120)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering: ClassVar[list] = ["name", "id"]

    def __str__(self):
        return self.name


class Membership(models.Model):
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
