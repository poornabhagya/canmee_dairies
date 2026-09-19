from django.db import migrations


def drop_orphan_default_tables(apps, schema_editor):
  connection = schema_editor.connection
  tables = connection.introspection.table_names()
  orphans = [
      "collection_point_loans_collectionpointloan",
      "collection_point_loans_collectionpointloanactionlog",
  ]
  with connection.cursor() as cursor:
      for table in orphans:
          if table in tables:
              cursor.execute(f"DROP TABLE `{table}`")


class Migration(migrations.Migration):

    dependencies = [
        ("collection_point_loans", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(drop_orphan_default_tables, migrations.RunPython.noop),
    ]
