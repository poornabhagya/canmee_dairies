from django import template

from canmee_dairies.formatting import format_money

register = template.Library()


@register.filter
def money(value):
    return format_money(value)


@register.simple_tag
def pagination_page_window(page_obj, on_each_side=2):
    """Page numbers with None entries for ellipsis gaps (DataTables-style)."""
    paginator = page_obj.paginator
    num_pages = paginator.num_pages
    if num_pages <= 1:
        return list(range(1, num_pages + 1))

    current = page_obj.number
    pages = set(range(1, min(2, num_pages) + 1))
    pages.update(range(max(1, num_pages - 1), num_pages + 1))
    pages.update(range(max(1, current - on_each_side), min(num_pages, current + on_each_side) + 1))

    ordered = sorted(pages)
    window = []
    prev = None
    for page in ordered:
        if prev is not None and page - prev > 1:
            window.append(None)
        window.append(page)
        prev = page
    return window
