from django import template

from canmee_dairies.formatting import format_money
from reports.payslip_labels import branch_name_sinhala, branch_payslip_name

register = template.Library()


@register.filter
def money(value):
    return format_money(value)


@register.filter
def get_item(mapping, key):
    if not mapping:
        return 0
    try:
        return mapping.get(key, 0)
    except AttributeError:
        return 0


@register.filter
def branch_payslip_name_si(branch):
    return branch_payslip_name(branch, lang="si")


@register.filter
def branch_name_si(name):
    return branch_name_sinhala(name)
