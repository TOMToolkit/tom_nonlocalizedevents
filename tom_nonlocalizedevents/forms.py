from django import forms

from tom_common.session_utils import set_encrypted_field
from tom_nonlocalizedevents.models import NonLocalizedEventsProfile


class NonLocalizedEventsProfileForm(forms.ModelForm):

    # even though this is a ModelForm (and we automatically have forms.Fields for
    # each model field), we have to add CharFields for any encrypted
    # fields because they exist in the model as a combination property descriptor
    # and BinaryField. )
    treasuremap_apikey = forms.CharField(
        required=False,
        label="TreasureMap API Key",
        help_text="Enter your TreasureMap API Key. Leave blank to keep unchanged."
    )

    class Meta:
        model = NonLocalizedEventsProfile
        fields = ['treasuremap_username', 'treasuremap_apikey']

    def save(self, commit=True):
        """Override save to handle the custom encrypted property."""
        # The form's 'treasuremap_apikey' is not a model field, so super().save() will ignore it,
        # because super() is forms.ModelForm.
        instance = super().save(commit=False)

        cleaned_treasuremap_apikey = self.cleaned_data.get('treasuremap_apikey')
        if cleaned_treasuremap_apikey:
            # The user object is available from the instance
            user = instance.user
            # Use the helper to set the encrypted field
            success = set_encrypted_field(user, instance, 'treasuremap_apikey', cleaned_treasuremap_apikey)

            if not success:
                # The helper function returns False on failure. We can add an error to the form.
                self.add_error(None, "Could not save encrypted field due to a server error. "
                                     "Please ensure you are logged in correctly.")

        if commit and not self.errors:
            instance.save()
        return instance
