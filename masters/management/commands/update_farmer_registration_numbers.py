from django.core.management.base import BaseCommand
from django.db import transaction

from masters.models import Farmer


def _first_alpha(value, fallback):
    for ch in str(value or "").strip():
        if ch.isalpha():
            return ch.upper()
    return fallback


class Command(BaseCommand):
    help = (
        "Regenerate farmer registration numbers as "
        "F + branch initial + route initial + 5-digit sequence (e.g. FAW00006)."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Preview changes without writing to database.",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]

        farmers = list(
            Farmer.objects.select_related("branch", "route", "collection_point", "collection_point__route")
            .order_by("branch_id", "route_id", "id")
        )

        if not farmers:
            self.stdout.write(self.style.WARNING("No farmers found."))
            return

        skipped = []
        eligible = []

        for farmer in farmers:
            route = farmer.route or (farmer.collection_point.route if farmer.collection_point_id else None)
            branch = farmer.branch or (route.branch if route else None)
            if not route or not branch:
                skipped.append(farmer.id)
                continue
            eligible.append((farmer, branch, route))

        if skipped:
            self.stdout.write(
                self.style.WARNING(
                    f"Skipping {len(skipped)} farmer(s) without resolvable branch/route: {', '.join(map(str, skipped[:20]))}"
                    + (" ..." if len(skipped) > 20 else "")
                )
            )

        to_update = []
        used_numbers = set(
            Farmer.objects.exclude(registration_number__isnull=True)
            .exclude(registration_number="")
            .values_list("registration_number", flat=True)
        )

        # Clear own existing values from uniqueness guard to allow reassignment.
        for farmer in farmers:
            if farmer.registration_number:
                used_numbers.discard(farmer.registration_number)

        eligible.sort(key=lambda t: t[0].id)
        seq = 1
        for farmer, branch, route in eligible:
            prefix = f"F{_first_alpha(branch.name, 'B')}{_first_alpha(route.name, 'R')}"
            reg = f"{prefix}{seq:05d}"
            # Guard against collisions where prefixes can match.
            while reg in used_numbers:
                seq += 1
                reg = f"{prefix}{seq:05d}"
            seq += 1
            used_numbers.add(reg)

            changed = (
                farmer.registration_number != reg
                or farmer.route_id != route.id
                or farmer.branch_id != branch.id
            )
            if changed:
                farmer.registration_number = reg
                farmer.route = route
                farmer.branch = branch
                to_update.append(farmer)

        self.stdout.write(
            f"Farmers scanned: {len(farmers)} | "
            f"eligible: {len(eligible)} | "
            f"to update: {len(to_update)} | skipped: {len(skipped)}"
        )

        if dry_run:
            self.stdout.write(self.style.WARNING("Dry-run mode: no database changes applied."))
            for farmer in to_update[:20]:
                self.stdout.write(
                    f" - Farmer #{farmer.id}: {farmer.registration_number} "
                    f"(branch={farmer.branch_id}, route={farmer.route_id})"
                )
            if len(to_update) > 20:
                self.stdout.write(f" ... and {len(to_update) - 20} more")
            return

        if not to_update:
            self.stdout.write(self.style.SUCCESS("No updates required."))
            return

        with transaction.atomic():
            Farmer.objects.bulk_update(to_update, ["registration_number", "route", "branch"])

        self.stdout.write(self.style.SUCCESS(f"Updated {len(to_update)} farmer(s)."))
