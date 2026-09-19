from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError

from suppliers.models import GRN
from suppliers.negative_branch_stock import (
    apply_negative_branch_stock_corrections,
    find_negative_branch_stock,
    resolve_adjustment_supplier,
)


class Command(BaseCommand):
    help = (
        "Find negative Branch Stock Available / BranchStock rows and create confirmed "
        "STOCK CORRECTION GRNs (rate/issue from last purchase GRN) so available is no "
        "longer negative. Requires a superuser username and --apply to write. "
        "Also available in the UI at /stock/correct-negative/ for superusers."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Preview corrections without creating GRNs.",
        )
        parser.add_argument(
            "--apply",
            action="store_true",
            help="Create and confirm adjustment GRNs.",
        )
        parser.add_argument(
            "--superuser",
            type=str,
            required=True,
            help="Username of a Django superuser authorizing this correction.",
        )
        parser.add_argument(
            "--supplier",
            type=int,
            default=None,
            help=(
                "Supplier id for adjustment GRNs. Defaults to the first active "
                "farmer-goods supplier."
            ),
        )
        parser.add_argument(
            "--grn-type",
            choices=["farmer_goods", "corporate"],
            default="farmer_goods",
            help=(
                "Supplier channel preference for default supplier selection "
                "(created documents are always STOCK CORRECTION)."
            ),
        )
        parser.add_argument(
            "--branch",
            type=int,
            default=None,
            help="Limit to one branch id.",
        )
        parser.add_argument(
            "--product",
            type=int,
            default=None,
            help="Limit to one product id.",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        apply = options["apply"]
        if dry_run and apply:
            raise CommandError("Use either --dry-run or --apply, not both.")
        if not dry_run and not apply:
            raise CommandError("Specify --dry-run (preview) or --apply (write).")

        User = get_user_model()
        actor = User.objects.filter(username=options["superuser"]).first()
        if actor is None or not actor.is_superuser or not actor.is_active:
            raise CommandError(
                f"User '{options['superuser']}' is not an active Django superuser."
            )

        try:
            supplier = resolve_adjustment_supplier(
                options["supplier"], options["grn_type"]
            )
        except ValidationError as exc:
            raise CommandError(str(exc)) from exc

        grn_type = (
            GRN.GRNType.FARMER_GOODS
            if options["grn_type"] == "farmer_goods"
            else GRN.GRNType.CORPORATE
        )

        corrections_by_branch, sync_only, _ = find_negative_branch_stock(
            branch_id=options["branch"],
            product_id=options["product"],
        )

        if not corrections_by_branch and not sync_only:
            self.stdout.write(self.style.SUCCESS("No negative branch stock found."))
            return

        self.stdout.write(
            f"Authorized by superuser: {actor.username} | supplier: {supplier.name} "
            f"(#{supplier.id}) | grn_type: {grn_type}"
        )

        for row in sync_only:
            self.stdout.write(
                f"SYNC {row['product'].name} @ {row['branch'].name}: "
                f"on-hand {row['on_hand']} -> movement available {row['available']}"
            )

        for _branch_id, rows in corrections_by_branch.items():
            branch_name = rows[0]["branch"].name
            self.stdout.write(f"\nGRN adjustment for {branch_name}:")
            for row in rows:
                self.stdout.write(
                    f"  +{row['shortfall']} {row['product'].name} "
                    f"(available {row['available']}, on-hand {row['on_hand']})"
                )

        if dry_run:
            grn_lines = sum(len(v) for v in corrections_by_branch.values())
            self.stdout.write(
                self.style.WARNING(
                    f"Dry run only. Would create {len(corrections_by_branch)} GRN(s) "
                    f"with {grn_lines} line(s); sync {len(sync_only)} BranchStock row(s)."
                )
            )
            return

        try:
            result = apply_negative_branch_stock_corrections(
                actor=actor,
                supplier=supplier,
                grn_type=grn_type,
                branch_id=options["branch"],
                product_id=options["product"],
            )
        except ValidationError as exc:
            raise CommandError(str(exc)) from exc

        self.stdout.write(
            self.style.SUCCESS(
                f"Done. GRNs: {', '.join(result['created_grns']) or 'none'}; "
                f"synced BranchStock rows: {result['synced']}."
            )
        )
