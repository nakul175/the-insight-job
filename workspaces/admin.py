from django.contrib import admin

from .models import Membership, Workspace

admin.site.register(Workspace)
admin.site.register(Membership)
