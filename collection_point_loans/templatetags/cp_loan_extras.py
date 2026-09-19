from django import template

from canmee_dairies.formatting import format_money

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
