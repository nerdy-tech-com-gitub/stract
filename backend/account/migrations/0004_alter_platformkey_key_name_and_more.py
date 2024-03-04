# Generated by Django 4.2.1 on 2023-11-15 11:37

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("account", "0003_platformkey"),
    ]

    operations = [
        migrations.AlterField(
            model_name="platformkey",
            name="key_name",
            field=models.CharField(blank=True, default="", max_length=64),
        ),
        migrations.AddConstraint(
            model_name="platformkey",
            constraint=models.UniqueConstraint(
                fields=("key_name", "organization"), name="unique_key_name"
            ),
        ),
    ]