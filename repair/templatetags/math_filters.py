from django import template
from decimal import Decimal, DivisionByZero, InvalidOperation

register = template.Library()


@register.filter
def multiply(value, arg):
    try:
        return Decimal(str(value)) * Decimal(str(arg))
    except (InvalidOperation, TypeError, ValueError):
        return 0


@register.filter
def divide(value, arg):
    try:
        v = Decimal(str(value))
        a = Decimal(str(arg))
        if a == 0:
            return 0
        result = v / a
        return result.quantize(Decimal('0.0'))
    except (DivisionByZero, InvalidOperation, TypeError, ValueError):
        return 0
