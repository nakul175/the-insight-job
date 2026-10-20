from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, render
from django.views.decorators.http import require_GET

from .decision_support import setup_progress
from .permissions import systems_for
from .views import context


@login_required
@require_GET
def system_setup(request, workspace_id, system_id):
    system = get_object_or_404(systems_for(request.user, workspace_id), pk=system_id)
    steps = setup_progress(request.user, workspace_id, system)
    return render(
        request,
        "intelligence/system_setup.html",
        {
            **context(request, workspace_id),
            "system": system,
            "steps": steps,
            "next_step": next((step for step in steps if not step["complete"]), None),
        },
    )
