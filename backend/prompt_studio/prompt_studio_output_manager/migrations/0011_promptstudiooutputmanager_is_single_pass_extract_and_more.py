# Generated by Django 4.2.1 on 2024-03-23 08:12

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("prompt_studio_core", "0008_customtool_exclude_failed_customtool_monitor_llm"),
        ("prompt_profile_manager", "0009_alter_profilemanager_prompt_studio_tool"),
        ("prompt_studio", "0006_alter_toolstudioprompt_prompt_key_and_more"),
        ("prompt_studio_output_manager", "0010_delete_duplicate_rows"),
    ]

    operations = [
        migrations.AddField(
            model_name="promptstudiooutputmanager",
            name="is_single_pass_extract",
            field=models.BooleanField(
                db_comment="Is the single pass extraction mode active", default=False
            ),
        ),
        migrations.AlterField(
            model_name="promptstudiooutputmanager",
            name="profile_manager",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="prompt_output_linked_prompt",
                to="prompt_profile_manager.profilemanager",
            ),
        ),
        migrations.AlterField(
            model_name="promptstudiooutputmanager",
            name="prompt_id",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="prompt_output_linked_prompt",
                to="prompt_studio.toolstudioprompt",
            ),
        ),
        migrations.AlterField(
            model_name="promptstudiooutputmanager",
            name="tool_id",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="prompt_ouput_linked_tool",
                to="prompt_studio_core.customtool",
            ),
        ),
        migrations.AddConstraint(
            model_name="promptstudiooutputmanager",
            constraint=models.UniqueConstraint(
                fields=(
                    "prompt_id",
                    "document_manager",
                    "profile_manager",
                    "tool_id",
                    "is_single_pass_extract",
                ),
                name="unique_prompt_output",
            ),
        ),
    ]