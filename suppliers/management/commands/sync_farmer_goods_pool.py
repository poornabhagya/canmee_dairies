from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError

from suppliers.farmer_goods_pool_sync import (
    apply_farmer_goods_pool_sync,
    find_farmer_goods_pool_sync,
)


class Command(BaseCommand):
    help = (
        "Set FarmerGoodsStock.quantity = confirmed FG GRN + stock-correction qty "
        "− applied issues (excludes pending/rejected). "
        "UI: /stock/correct-negative/ (superuser)."
    )

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true")
        parser.add_argument("--apply", action="store_true")
        parser.add_argument("--superuser", type=str, required=True)
        parser.add_argument("--product", type=int, default=None)

    def handle(self, *args, **options):
        if options["dry_run"] == options["apply"]:
            raise CommandError("Specify exactly one of --dry-run or --apply.")

        User = get_user_model()
        actor = User.objects.filter(username=options["superuser"]).first()
        if actor is None or not actor.is_superuser or not actor.is_active:
            raise CommandError(
                f"User '{options['superuser']}' is not an active Django superuser."
            )

        rows = find_farmer_goods_pool_sync(product_id=options["product"])
        if not rows:
            self.stdout.write(self.style.SUCCESS("Farmer goods pool already in sync."))
            return

        self.stdout.write(f"Authorized by {actor.username}. {len(rows)} product(s) to sync:")
        for row in rows[:50]:
            self.stdout.write(
                f"  {row['product_name']}: {row['current']} -> {row['target']} "
                f"(GRN {row['received']} - issued {row['issued']}, delta {row['delta']})"
            )
        if len(rows) > 50:
            self.stdout.write(f"  ... and {len(rows) - 50} more.")

        if options["dry_run"]:
            self.stdout.write(self.style.WARNING("Dry run only. No changes written."))
            return

        try:
            result = apply_farmer_goods_pool_sync(
                product_id=options["product"], actor=actor
            )
        except ValidationError as exc:
            raise CommandError(str(exc)) from exc

        self.stdout.write(
            self.style.SUCCESS(
                f"Updated {result['updated']} row(s), created {result['created']}."
            )
        )
