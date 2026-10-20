from django import forms

from workspaces.models import Membership

from .models import Connection


class SourceForm(forms.Form):
    name = forms.CharField(max_length=120)
    url = forms.URLField(max_length=2000, label="Public HTTP(S) URL")
    owner = forms.ModelChoiceField(queryset=Membership.objects.none())

    def __init__(self, *args, workspace, source=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["owner"].queryset = Membership.objects.filter(
            workspace=workspace
        ).select_related("user")
        self.fields["owner"].label_from_instance = lambda m: m.user.get_username()


class ScopeForm(forms.Form):
    page_limit = forms.IntegerField(min_value=1, max_value=100, initial=10)
    depth_limit = forms.IntegerField(min_value=0, max_value=5, initial=2)
    time_limit_seconds = forms.IntegerField(min_value=1, max_value=900, initial=180)
    byte_limit = forms.IntegerField(min_value=1, max_value=20000000, initial=10000000)

    include = forms.CharField(
        widget=forms.Textarea(attrs={"rows": 3}),
        help_text="One absolute path prefix per line.",
    )
    exclude = forms.CharField(widget=forms.Textarea(attrs={"rows": 3}), required=False)
    access = forms.ChoiceField(
        choices=[
            ("public", "Public"),
            ("api", "API connection"),
            ("session", "Browser session"),
        ]
    )
    connection = forms.ModelChoiceField(
        queryset=Connection.objects.none(), required=False
    )
    partial_policy = forms.ChoiceField(
        choices=[
            ("reject", "Require complete text capture"),
            ("accept_documented", "Allow documented partial capture"),
        ]
    )
    partial_reason = forms.CharField(
        widget=forms.Textarea(attrs={"rows": 3}), required=False, max_length=1000
    )

    def __init__(self, *args, workspace, source=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["connection"].queryset = Connection.objects.filter(
            workspace=workspace, bound_source=source
        )


class InviteForm(forms.Form):
    email = forms.EmailField()
    role = forms.ChoiceField(
        choices=[
            ("administrator", "Administrator"),
            ("operator", "Operator"),
            ("reviewer", "Reviewer"),
            ("viewer", "Viewer"),
        ]
    )
    sources = forms.MultipleChoiceField(required=False)
    systems = forms.MultipleChoiceField(required=False)
    can_export = forms.BooleanField(required=False)

    def __init__(self, *args, sources, systems, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["sources"].choices = [(str(s.pk), s.name) for s in sources]
        self.fields["systems"].choices = [(str(s.pk), s.name) for s in systems]
