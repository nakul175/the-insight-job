from django import forms

from workspaces.models import Membership

from .models import Connection


class SourceForm(forms.Form):
    name = forms.CharField(max_length=120)
    url = forms.URLField(max_length=2000, label="Public HTTP(S) URL")
    owner = forms.ModelChoiceField(queryset=Membership.objects.none())

    def __init__(self, *args, workspace, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["owner"].queryset = Membership.objects.filter(
            workspace=workspace
        ).select_related("user")
        self.fields["owner"].label_from_instance = lambda m: m.user.get_username()


class ScopeForm(forms.Form):
    include = forms.CharField(
        widget=forms.Textarea, help_text="One absolute path prefix per line."
    )
    exclude = forms.CharField(widget=forms.Textarea, required=False)
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
        widget=forms.Textarea, required=False, max_length=1000
    )

    def __init__(self, *args, workspace, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["connection"].queryset = Connection.objects.filter(
            workspace=workspace
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
