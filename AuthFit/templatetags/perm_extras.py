# AuthFit/templatetags/perm_extras.py
from django import template
register = template.Library()

@register.filter
def get_attr(obj, attr_name):
    return getattr(obj, attr_name, False)