from decimal import Decimal

# kg → liters: liters = kg × MILK_LITER_FACTOR
# liters → kg: kg = liters ÷ MILK_LITER_FACTOR
# Applied on new saves and form conversions only; existing stored kg/liter rows are not migrated.
MILK_LITER_FACTOR = Decimal("0.973709834469328")
