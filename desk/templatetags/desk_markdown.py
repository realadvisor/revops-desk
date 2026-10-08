from django import template
from django.utils.safestring import mark_safe
from markdown_it import MarkdownIt

register = template.Library()

# js-default disables raw HTML. Keep it explicit: this renders untrusted input.
# Images stay as text/links; screenshots use the existing private attachments.
_renderer = MarkdownIt("js-default", {"html": False, "breaks": True, "linkify": True})
_renderer.enable("linkify").disable("image")


@register.filter
def markdown_text(value):
    """Render stored plain text without allowing HTML or unsafe link schemes."""
    return mark_safe(_renderer.render(str(value or "")))
