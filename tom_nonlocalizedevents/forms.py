from django import forms


class GraceDBEventIngestionForm(forms.Form):
    """A simple form to accept a GraceDB event ID for manual ingestion."""
    event_id = forms.CharField(
        label='GraceDB SuperEvent ID',
        max_length=100,
        help_text='Superevent IDs begin with S (or MS for mock events), e.g. S230518h. '
                  'Per-detection G-number events are not superevents and cannot be ingested.',
        widget=forms.TextInput(attrs={'placeholder': 'S230518h'})
    )
