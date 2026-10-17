import logging

from rest_framework import serializers

from tom_nonlocalizedevents.models import (CredibleRegion, EventCandidate, EventLocalization,
                                           EventSequence, NonLocalizedEvent, ExternalCoincidence)
from tom_targets.models import Target
from tom_targets.serializers import TargetSerializer

logger = logging.getLogger(__name__)


class CredibleRegionSerializer(serializers.ModelSerializer):

    class Meta:
        model = CredibleRegion
        fields = ['smallest_percent', 'localization']


class EventCandidateSerializer(serializers.ModelSerializer):
    """
    Serializer class for the ``EventCandidate``. ``PrimaryKeyRelatedField``s are used in order to allow creating an
    ``EventCandidate`` with just a primary key, and ``to_representation`` is then overridden for proper display values.
    See: https://www.django-rest-framework.org/api-guide/relations/#custom-relational-fields
    """
    nonlocalizedevent = serializers.PrimaryKeyRelatedField(queryset=NonLocalizedEvent.objects.all())
    target = serializers.PrimaryKeyRelatedField(queryset=Target.objects.all(), required=False,
                                                allow_null=True, default=None)
    target_fields = serializers.DictField(required=False, write_only=True)
    credible_regions = serializers.SerializerMethodField()
    UPDATE_KEYS = ['viable', 'viability_reason', 'priority']

    class Meta:
        model = EventCandidate
        fields = '__all__'
        # list_serializer_class = BulkCreateEventCandidateListSerializer

    def validate(self, data):
        if self.context.get('request').method == 'PATCH':
            # Patch requests on candidates should just be used to change viable boolean and reason and priority
            if not any([key in data for key in self.UPDATE_KEYS]):
                raise serializers.ValidationError(
                    f"PATCH update must contain at least one of {', '.join(self.UPDATE_KEYS)} to update"
                )
        else:
            if 'target' not in data and 'target_fields' not in data:
                raise serializers.ValidationError(
                    "Must specify either target or target_fields to create an EventCandidate"
                )
            if 'target_fields' in data and data['target_fields'].get('name'):
                try:
                    target = Target.objects.get(name=data['target_fields']['name'])
                    data['target'] = target
                    if EventCandidate.objects.filter(
                        target=target, nonlocalizedevent=data['nonlocalizedevent']
                    ).exists():
                        raise serializers.ValidationError(
                            f"Event Candidate already exists for target {target.name} "
                            f"and nonlocalizedevent {data['nonlocalizedevent'].event_id}")
                    del data['target_fields']
                except Target.DoesNotExist:
                    target_serializer = TargetSerializer(data=data['target_fields'])
                    target_serializer.is_valid()
                    data['target_fields'] = target_serializer.validated_data
        return super().validate(data)

    def update(self, instance, validated_data):
        instance.viable = validated_data.get('viable', instance.viable)
        instance.viability_reason = validated_data.get('viability_reason', instance.viability_reason)
        instance.priority = validated_data.get('priority', instance.priority)
        instance.save()
        return instance

    def create(self, validated_data):
        if 'target_fields' in validated_data:
            try:
                target = Target.objects.get(name=validated_data['target_fields']['name'])
            except Target.DoesNotExist:
                target_serializer = TargetSerializer(data=validated_data['target_fields'])
                if target_serializer.is_valid():
                    target = target_serializer.save()
                    logger.info(f"Created target {target.id} with name {target.name}")
            validated_data['target'] = target
            del validated_data['target_fields']
        return super().create(validated_data)

    def get_credible_regions(self, instance):
        localization_id_to_credible_region_percent = {}
        credibleregions = instance.credibleregions.all()
        for cr in credibleregions:
            localization_id_to_credible_region_percent[cr.localization.id] = cr.smallest_percent
        return localization_id_to_credible_region_percent

    def to_representation(self, instance):
        representation = super().to_representation(instance)
        # TODO: this is a little unorthodox. it's convenient for now to handle the data from
        # the ForiengKey objects, but that should be handled separately by their own serializers.
        # (and to_representation could be left undisturbed).
        representation['target'] = TargetSerializer(Target.objects.get(pk=representation['target'])).data
        representation['nonlocalizedevent'] = NonLocalizedEvent.objects.get(
            pk=representation['nonlocalizedevent']).event_id
        return representation


class EventLocalizationSerializer(serializers.HyperlinkedModelSerializer):
    class Meta:
        model = EventLocalization
        fields = ['id', 'date', 'skymap_url', 'skymap_version', 'area_50', 'area_90', 'distance_mean', 'distance_std']


class ExternalCoincidence(serializers.HyperlinkedModelSerializer):
    localization = EventLocalizationSerializer(read_only=True)

    class Meta:
        model = ExternalCoincidence
        fields = ['details', 'localization']


class EventSequenceSerializer(serializers.HyperlinkedModelSerializer):
    localization = EventLocalizationSerializer(read_only=True)
    external_coincidence = ExternalCoincidence(read_only=True)

    class Meta:
        model = EventSequence
        fields = ['id', 'created', 'modified', 'sequence_id', 'event_subtype',
                  'details', 'ingestor_source', 'localization', 'external_coincidence']


class NonLocalizedEventSerializer(serializers.HyperlinkedModelSerializer):
    candidates = serializers.SerializerMethodField()
    sequences = EventSequenceSerializer(many=True)

    class Meta:
        model = NonLocalizedEvent
        fields = ['id', 'event_id', 'state', 'candidates', 'created', 'modified', 'sequences']

    def get_candidates(self, instance):
        alerts = instance.candidates.all()
        # This returns the nonlocalied event identifier, which means it's duplicated in the response.
        # The NonLocalizedEventSerializer should therefore use its own custom EventCandidateSerializer
        # rather than the one defined above.
        return EventCandidateSerializer(alerts, many=True).data
