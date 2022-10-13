# Generated by Django 4.1.1 on 2022-10-13 20:38

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('tom_nonlocalizedevents', '0011_eventcandidate_viability_reason_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='nonlocalizedevent',
            name='event_subtype',
            field=models.CharField(default='', help_text='The subtype of the event. Options are type specific, i.e. GW events have initial, preliminary, update types.', max_length=256),
        ),
    ]