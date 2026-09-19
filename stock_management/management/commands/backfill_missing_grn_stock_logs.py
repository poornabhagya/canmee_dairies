from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from stock_management.grn_log_cleanup import (
    apply_missing_grn_stock_log_backfill,
    find_missing_grn_stock_logs,
)


class Command(BaseCommand):
    help = (
        "Create missing StockLog GRN rows for confirmed GRN lines "
        "(reference grn:{id}). Fixes Stock History balance when GRNs modal "
        "shows receipts that history does not."
    )

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true")
        parser.add_argument("--apply", action="store_true")
        parser.add_argument("--product", type=int, default=None)
        parser.add_argument("--branch", type=int, default=None)
        parser.add_argument("--superuser", type=str, default=None)

    def handle(self, *args, **options):
        if options["dry_run"] == options["apply"]:
            raise CommandError("Specify exactly one of --dry-run or --apply.")
        rows = find_missing_grn_stock_logs(
            product_id=options["product"], branch_id=options["branch"]
        )
        if not rows:
            self.stdout.write(self.style.SUCCESS("No missing GRN stock logs."))
            return
        for row in rows:
            self.stdout.write(
                f"{row['grn_number']} {row['product_name']} qty={row['quantity']} "
                f"{row['source']} -> {row['destination']}"
            )
        self.stdout.write(f"Missing: {len(rows)} log(s).")
        if options["dry_run"]:
            return
        actor = None
        if options["superuser"]:
            User = get_user_model()
            actor = User.objects.filter(
                username=options["superuser"], is_superuser=True, is_active=True
            ).first()
            if not actor:
                raise CommandError(f"Superuser not found: {options['superuser']}")
        result = apply_missing_grn_stock_log_backfill(
            product_id=options["product"],
            branch_id=options["branch"],
            actor=actor,
        )
        self.stdout.write(
            self.style.SUCCESS(f"Created {result['created']} StockLog GRN row(s).")
        )
