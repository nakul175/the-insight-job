from django.urls import path

from . import access_views, views

urlpatterns = [
    path(
        "<uuid:workspace_id>/sources/<uuid:source_id>/connect/",
        access_views.connection_setup,
        name="connection-setup",
    ),
    path(
        "<uuid:workspace_id>/connections/<uuid:connection_id>/<str:action>/",
        access_views.connection_action,
        name="connection-action",
    ),
    path(
        "<uuid:workspace_id>/login/<uuid:handoff_id>/",
        access_views.handoff_view,
        name="browser-handoff",
    ),
    path(
        "<uuid:workspace_id>/login/<uuid:handoff_id>/state/",
        access_views.handoff_state,
        name="handoff-state",
    ),
    path(
        "<uuid:workspace_id>/login/<uuid:handoff_id>/frame/",
        access_views.handoff_frame,
        name="handoff-frame",
    ),
    path(
        "<uuid:workspace_id>/login/<uuid:handoff_id>/action/",
        access_views.handoff_action,
        name="handoff-action",
    ),
    path(
        "<uuid:workspace_id>/sources/<uuid:source_id>/collect/",
        access_views.run_start,
        name="run-start",
    ),
    path(
        "<uuid:workspace_id>/runs/<uuid:run_id>/",
        access_views.run_detail,
        name="run-detail",
    ),
    path(
        "<uuid:workspace_id>/runs/<uuid:run_id>/cancel/",
        access_views.run_cancel,
        name="run-cancel",
    ),
    path(
        "<uuid:workspace_id>/captures/<uuid:capture_id>/original/",
        access_views.original_file,
        name="original-file",
    ),
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
