from django.urls import path

from . import views

urlpatterns = [
    path(
        "<uuid:workspace_id>/api/systems/<uuid:system_id>/records/",
        views.system_records,
        name="system-records",
    ),
    path("<uuid:workspace_id>/overview/", views.overview, name="overview"),
    path("<uuid:workspace_id>/sources/", views.source_list, name="sources"),
    path("<uuid:workspace_id>/sources/new/", views.source_create, name="source-create"),
    path(
        "<uuid:workspace_id>/sources/<uuid:source_id>/",
        views.source_detail,
        name="source-detail",
    ),
    path(
        "<uuid:workspace_id>/sources/<uuid:source_id>/preview/",
        views.scope_preview,
        name="scope-preview",
    ),
    path(
        "<uuid:workspace_id>/sources/<uuid:source_id>/<str:action>/",
        views.source_action,
        name="source-action",
    ),
    path("<uuid:workspace_id>/members/", views.members, name="members"),
    path(
        "<uuid:workspace_id>/members/<int:membership_id>/<str:action>/",
        views.membership_action,
        name="membership-action",
    ),
    path(
        "<uuid:workspace_id>/systems/<uuid:system_id>/",
        views.system_detail,
        name="system-detail",
    ),
    path(
        "<uuid:workspace_id>/api/systems/<uuid:system_id>/",
        views.system_api,
        name="system-api",
    ),
    path("<uuid:workspace_id>/records/", views.record_list, name="records"),
    path(
        "<uuid:workspace_id>/records/export/", views.record_export, name="record-export"
    ),
    path(
        "<uuid:workspace_id>/records/<uuid:record_id>/",
        views.record_detail,
        name="record-detail",
    ),
    path(
        "<uuid:workspace_id>/records/<uuid:record_id>/file/",
        views.record_file,
        name="record-file",
    ),
    path("invitations/accept/", views.accept, name="accept-invitation"),
]
