# Generated by Django 3.2b1 on 2021-03-09 19:21

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('tom_nonlocalizedevents', '0003_auto_20210225_0034'),
    ]

    operations = [
        migrations.AddField(
            model_name='superevent',
            name='superevent_type',
            field=models.CharField(default='', max_length=50),
            preserve_default=False,
        ),
    ]