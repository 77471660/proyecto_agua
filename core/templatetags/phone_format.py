import re

from django import template


register = template.Library()


@register.filter
def format_phone_display(value):
    digits = re.sub(r'\D', '', str(value or ''))

    if len(digits) == 11 and digits.startswith('51'):
        digits = digits[2:]

    if len(digits) == 9:
        return f'{digits[:3]} {digits[3:6]} {digits[6:]}'

    return value or ''
