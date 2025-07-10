import logging

from django import template

from tom_common.session_utils import get_encrypted_field

# Import the form to consistently get the label for the password field.
from tom_nonlocalizedevents.models import NonLocalizedEventsProfile
from tom_nonlocalizedevents.forms import NonLocalizedEventsProfileForm


logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

register = template.Library()


@register.inclusion_tag('tom_nonlocalizedevents/partials/nonlocalizedevents_user_profile.html')
def nonlocalizedevents_profile_data(user) -> dict:
    """
    Gathers the Non-localized Events profile data for display in the user profile partial.

    This tag prepares a structured list of data for the template, including
    field labels (verbose_name) and their corresponding values. This is more
    robust than using model_to_dict, as it gives full control over the
    presentation and handles non-model fields (like encrypted properties)
    gracefully.
    """
    try:
        profile: NonLocalizedEventsProfileProfile = user.nonlocalizedeventsprofile
    except NonLocalizedEventsProfile.DoesNotExist:
        profile = NonLocalizedEventsProfile.objects.create(user=user)

    profile_data_list = []

    # Define the standard model fields we want to display.
    model_fields_to_display = ['treasuremap_username']

    for field_name in model_fields_to_display:
        field = profile._meta.get_field(field_name)
        # Use get_..._display() for choice fields to get the human-readable value.
        if hasattr(profile, f'get_{field_name}_display'):
            value = getattr(profile, f'get_{field_name}_display')()
        else:
            value = getattr(profile, field_name)

        profile_data_list.append({
            'label': field.verbose_name,
            'value': value,
        })

    # Handle the special case of the encrypted password field.
    field_name = 'treasuremap_apikey'
    decypted_treasuremap_apikey = get_encrypted_field(user, profile, field_name)
    field_label = NonLocalizedEventsProfileForm.base_fields[field_name].label or 'TreasureMap API Key'

    treasuremap_apikey_value = decypted_treasuremap_apikey
    if decypted_treasuremap_apikey is None:
        treasuremap_apikey_value = "[API key not available]"

    profile_data_list.append({'label': field_label, 'value': treasuremap_apikey_value})

    return {'user': user, 'nonlocalizedevents_profile': profile, 'profile_data_list': profile_data_list}
