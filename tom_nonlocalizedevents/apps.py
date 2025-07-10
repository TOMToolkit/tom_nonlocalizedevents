from django.apps import AppConfig
from django.urls import path, include


class TomNonlocalizedeventsConfig(AppConfig):
    name = 'tom_nonlocalizedevents'
    # default value of `label` required for migrations to work

    def ready(self):
        import tom_nonlocalizedevents.signals.handlers  # noqa
        super().ready()

    def nav_items(self):
        """
        Integration point for adding items to the navbar.
        This method should return a list of dictionaries that include a `partial` key pointing to the html templates to
        be included in the navbar. The `position` key, if included, should be either "left" or "right" to specify which
        side of the navbar the partial should be included on. If not included, a right side nav item is assumed.
        """
        # add a single navbar item (defined in the partial) goto the NLE index page
        return [{'partial': f'{self.name}/partials/navbar_nonlocalizedevents.html', 'position': 'left'}]

    def include_url_paths(self):
        """
        Integration point for adding URL patterns to the Tom Common URL configuration.
        This method should return a list of URL patterns to be included in the main URL configuration.
        """
        urlpatterns = [
            path('nonlocalizedevents/', include(f'{self.name}.urls', namespace='nonlocalizedevents'))
        ]
        return urlpatterns

    def profile_details(self):
        """
        Integration point for adding items to the user profile page.

        This method should return a list of dictionaries that include a `partial` key pointing to the path of the html
        profile partial. The `context` key should point to the dot separated string path to the templatetag that will
        return a dictionary containing new context for the accompanying partial.
        Typically, this partial will be a bootstrap card displaying some app specific user data.
        """
        profile_config = [
            {
                'partial': f'{self.name}/partials/nonlocalizedevents_user_profile.html',
                'context': f'{self.name}.templatetags.nonlocalizedevents_extras.nonlocalizedevents_profile_data',
            }
        ]
        return profile_config

