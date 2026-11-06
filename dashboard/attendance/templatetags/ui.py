from django import template
from django.utils.html import format_html

register = template.Library()


@register.simple_tag
def icon(name):
    """Inline an icon from the sprite in _icons.html. Add new icons there first."""
    return format_html('<svg class="i" aria-hidden="true"><use href="#i-{}"></use></svg>', name)
