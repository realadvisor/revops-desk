from pathlib import Path
from types import SimpleNamespace

from django.template import Context, Engine
from django.test import SimpleTestCase


class DescriptionLinksTests(SimpleTestCase):
    def test_reference_links_are_clickable_and_html_is_escaped(self):
        source = (Path(__file__).parent / 'templates/desk/detail.html').read_text()
        expression = '{{ issue.description_stripped|urlize|linebreaksbr }}'
        self.assertIn(expression, source)
        text = 'Diagnostic\nhttps://example.com/report?country=fr&month=2026-09\n<script>alert(1)</script>'
        rendered = Engine().from_string(expression).render(Context({'issue': SimpleNamespace(description_stripped=text)}))
        self.assertIn('href="https://example.com/report?country=fr&amp;month=2026-09"', rendered)
        self.assertIn('<br>', rendered)
        self.assertNotIn('<script>', rendered)
        self.assertIn('&lt;script&gt;', rendered)
