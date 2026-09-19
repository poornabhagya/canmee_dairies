"""Verify dispatch list dependencies (templates, DB columns, migrations)."""

from django.core.management.base import BaseCommand
from django.db import connection
from django.template.loader import get_template
from django.template import TemplateDoesNotExist


REQUIRED_TEMPLATES = [
    "dispatch/distribution_list.html",
    "dispatch/_distribution_table_styles.html",
    "dispatch/_distribution_status_controls.html",
    "dispatch/_distribution_payment_controls.html",
    "dispatch/_distribution_return_modal.html",
    "dispatch/_distribution_inline_script.html",
]

REQUIRED_COLUMNS = [
    ("dispatch_milkdistribution", "destination_branch_id"),
    ("dispatch_milkdistribution", "returned_branch_id"),
    ("dispatch_milkdistribution", "buyer_result_quantity"),
    ("dispatch_milkdistribution", "payment_status"),
]

REQUIRED_MIGRATIONS = [
    ("dispatch", "0005_distribution_return_status"),
    ("dispatch", "0006_branch_to_branch_dispatch"),
]


class Command(BaseCommand):
    help = "Check templates, DB columns, and migrations needed for /dispatch/ to work."

    def handle(self, *args, **options):
        ok = True

        self.stdout.write("Templates:")
        for name in REQUIRED_TEMPLATES:
            try:
                get_template(name)
                self.stdout.write(self.style.SUCCESS(f"  OK  {name}"))
            except TemplateDoesNotExist:
                ok = False
                self.stdout.write(self.style.ERROR(f"  MISSING  {name}"))

        self.stdout.write("\nDatabase columns:")
        table_columns = {}
        with connection.cursor() as cursor:
            for table, column in REQUIRED_COLUMNS:
                if table not in table_columns:
                    description = connection.introspection.get_table_description(cursor, table)
                    table_columns[table] = {row.name for row in description}
                if column in table_columns[table]:
                    self.stdout.write(self.style.SUCCESS(f"  OK  {table}.{column}"))
                else:
                    ok = False
                    self.stdout.write(self.style.ERROR(f"  MISSING  {table}.{column}"))

        self.stdout.write("\nMigrations:")
        from django.db.migrations.recorder import MigrationRecorder

        applied = set(
            MigrationRecorder.Migration.objects.filter(
                app__in={app for app, _ in REQUIRED_MIGRATIONS}
            ).values_list("app", "name")
        )
        for app, name in REQUIRED_MIGRATIONS:
            if (app, name) in applied:
                self.stdout.write(self.style.SUCCESS(f"  OK  {app}.{name}"))
            else:
                ok = False
                self.stdout.write(self.style.ERROR(f"  NOT APPLIED  {app}.{name}"))

        self.stdout.write("\nImports:")
        try:
            from dispatch.forms import MilkDistributionStatusForm  # noqa: F401
            from dispatch.views import MilkDistributionListView  # noqa: F401

            self.stdout.write(self.style.SUCCESS("  OK  dispatch.forms and dispatch.views"))
        except Exception as exc:
            ok = False
            self.stdout.write(self.style.ERROR(f"  FAILED  {exc}"))

        if ok:
            self.stdout.write(self.style.SUCCESS("\nAll dispatch deploy checks passed."))
        else:
            self.stdout.write(
                self.style.ERROR(
                    "\nOne or more checks failed. Upload missing templates/code, then run: python manage.py migrate"
                )
            )
