from django.contrib import admin
from django.contrib.auth.views import LogoutView
from django.urls import path

from workspaces import views

urlpatterns = [
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
