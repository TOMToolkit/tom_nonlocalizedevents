from django.urls import path

from tom_common.api_router import SharedAPIRootRouter  # a singleton DRF Router

from . import views

# this mechanism allows ViewSets to be registered with the tom_common Router
# for any of the INSTALLED_APPS (i.e. the routes are added because the APP is
# INSTALLED -- nothing else is required))
router = SharedAPIRootRouter()
router.register(r'nonlocalizedevents', views.NonLocalizedEventViewSet)
router.register(r'eventlocalizations', views.EventLocalizationViewSet)
router.register(r'eventcandidates', views.EventCandidateViewSet)

# app_name provides namespace in {% url %} template tag
# (i.e. {% url 'nonlocalizedevents:detail' <pk> %}
app_name = 'nonlocalizedevents'

urlpatterns = [
    path('', views.NonLocalizedEventListView.as_view(), name='index'),
    # literal routes MUST be registered before the '<str:event_id>/' catch-all,
    # which matches any single path segment
    path('ingest-gracedb/', views.IngestFromGraceDBView.as_view(), name='ingest-gracedb'),
    path('tab/<str:event_type_slug>/', views.EventTypeTabView.as_view(), name='tab'),
    path('<int:pk>/', views.NonLocalizedEventDetailView.as_view(), name='detail'),
    path('<str:event_id>/', views.NonLocalizedEventDetailView.as_view(), name='event-detail'),
]
