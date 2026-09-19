from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError

from stock_management.grn_log_cleanup import (
    apply_duplicate_grn_stock_log_cleanup,
    find_duplicate_grn_stock_logs,
)


class Command(BaseCommand):
    help = (
        "Remove duplicate StockLog GRN rows so history keeps one log per confirmed "
        "GRN line. Also backfills grn:{id} references on kept rows. "
        "UI: /stock/correct-negative/ (superuser)."
    )

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", help="Preview only.")
        parser.add_argument("--apply", action="store_true", help="Delete duplicate logs.")
        parser.add_argument(
            "--superuser",
            type=str,
            required=True,
            help="Active superuser username authorizing the cleanup.",
        )
        parser.add_argument("--product", type=int, default=None)
        parser.add_argument("--branch", type=int, default=None)

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        apply = options["apply"]
        if dry_run == apply:
            raise CommandError("Specify exactly one of --dry-run or --apply.")

        User = get_user_model()
        actor = User.objects.filter(username=options["superuser"]).first()
        if actor is None or not actor.is_superuser or not actor.is_active:
            raise CommandError(
                f"User '{options['superuser']}' is not an active Django superuser."
            )

        plan = find_duplicate_grn_stock_logs(
            product_id=options["product"], branch_id=options["branch"]
        )
        if not plan["delete_ids"] and not plan["set_references"]:
            self.stdout.write(self.style.SUCCESS("No duplicate GRN stock logs found."))
            return

        self.stdout.write(
            f"Authorized by {actor.username}. "
            f"Groups: {plan['group_count']}, delete: {plan['delete_count']}, "
            f"set refs: {len(plan['set_references'])}."
        )
        for g in plan["groups"][:40]:
            self.stdout.write(
                f"  {g['kind']} {g['product_name']} qty={g['quantity']} "
                f"{g['source']} -> {g['destination']}: "
                f"logs {g['actual']} / expected {g['expected']}, delete {g['delete_count']}"
            )
        if len(plan["groups"]) > 40:
            self.stdout.write(f"  ... and {len(plan['groups']) - 40} more group(s).")

        if dry_run:
            self.stdout.write(self.style.WARNING("Dry run only. No changes written."))
            return

        try:
            result = apply_duplicate_grn_stock_log_cleanup(
                product_id=options["product"],
                branch_id=options["branch"],
                actor=actor,
            )
        except ValidationError as exc:
            raise CommandError(str(exc)) from exc

        self.stdout.write(
            self.style.SUCCESS(
                f"Deleted {result['deleted']} StockLog row(s); "
                f"updated {result['updated_refs']} reference(s)."
            )
        )
