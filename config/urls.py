from django.contrib import admin
from django.contrib.auth.views import LogoutView
from django.urls import include, path
from django.utils.translation import gettext_lazy as _

from workspaces import views

admin.site.site_header = _("Source Almighty administration")
admin.site.site_title = "Source Almighty"
admin.site.index_title = _("Workspace administration")

urlpatterns = [
    path("app/", include("intelligence.urls")),
    path("", views.home, name="home"),
    path("sign-in/", views.SignInView.as_view(), name="login"),
    path("sign-out/", LogoutView.as_view(), name="logout"),
    path("workspaces/", views.workspace_list, name="workspaces"),
    path(
        "workspaces/<uuid:workspace_id>/select/",
        views.select_workspace,
        name="select-workspace",
    ),
    path("workspaces/<uuid:workspace_id>/", views.workspace_detail, name="workspace"),
    path("health/ready/", views.readiness, name="readiness"),
    path("admin/", admin.site.urls),
]
