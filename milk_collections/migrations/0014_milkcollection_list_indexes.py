from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("collections", "0013_collectionpointmilkreconcilesyncsnapshot"),
    ]

    operations = [
        migrations.AddIndex(
            model_name="milkcollection",
            index=models.Index(
                fields=["collection_point", "source", "date"],
                name="milkcoll_point_src_date",
            ),
        ),
        migrations.AddIndex(
            model_name="milkcollection",
            index=models.Index(
                fields=["farmer", "source", "date"],
                name="milkcoll_farmer_src_date",
            ),
        ),
        migrations.AddIndex(
            model_name="milkcollection",
            index=models.Index(
                fields=["source", "is_deleted", "date"],
                name="milkcoll_src_del_date",
            ),
        ),
    ]
