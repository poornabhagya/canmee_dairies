from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("branches", "0001_initial"),
        ("dispatch", "0012_mark_stale_incoming_notifications_read"),
    ]

    operations = [
        migrations.AddField(
            model_name="milkdistribution",
            name="diverted_by_branch",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="dispatches_diverted_from_here",
                to="branches.branch",
                verbose_name="Diverted by branch",
            ),
        ),
    ]
