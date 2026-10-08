from types import SimpleNamespace
from uuid import UUID

from django.template.loader import render_to_string
from django.test import SimpleTestCase, override_settings


@override_settings(STORAGES={"staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"}})
class DescriptionLinksTests(SimpleTestCase):
    def render_text(self, text):
        actor = SimpleNamespace(first_name="Alex", email="alex@example.com")
        issue = SimpleNamespace(created_by=actor, pk=UUID("00000000-0000-0000-0000-000000000001"), description_stripped=text)
        return render_to_string("desk/detail.html", {
            "issue": issue,
            "request": SimpleNamespace(user=actor),
            "timeline": [SimpleNamespace(kind="comment", actor=actor, comment_stripped=text)],
        })

    def test_reference_links_are_clickable_and_html_is_escaped(self):
        rendered = self.render_text('Diagnostic\nhttps://example.com/report?country=fr&month=2026-09\n<script>alert(1)</script>')
        self.assertEqual(rendered.count('href="https://example.com/report?country=fr&amp;month=2026-09"'), 2)
        self.assertIn('Diagnostic<br', rendered)
        self.assertNotIn('<script>alert(1)</script>', rendered)
        self.assertEqual(rendered.count('&lt;script&gt;'), 2)

    def test_markdown_renders_in_descriptions_and_comments(self):
        rendered = self.render_text("## Diagnosis\n\n**Fixed** and *verified* with `ticket_id`.\n\n- First\n- Second\n\n1. Review\n2. Ship\n\n> Evidence\n\n[Report](https://example.com/report)\n\n| Country | Status |\n| --- | --- |\n| France | Done |\n\n```python\nif total < 10:\n    print(123)\n```")
        for expected in ('<h2>Diagnosis</h2>', '<strong>Fixed</strong>', '<em>verified</em>',
                         '<code>ticket_id</code>', '<ul>', '<ol>', '<blockquote>',
                         'href="https://example.com/report"', '<table>', '<td>France</td>',
                         '<pre><code class="language-python">', 'if total &lt; 10:'):
            with self.subTest(expected=expected):
                self.assertEqual(rendered.count(expected), 2)

    def test_unsafe_html_and_link_schemes_stay_inert(self):
        for source in ('<img src=x onerror=alert(1)>', '<iframe src="https://evil.example"></iframe>',
                       '[click](javascript:alert%281%29)', '[click](jav&#x61;script:alert%281%29)',
                       '[click](data:text/html;base64,PHNjcmlwdD4=)', '[click](vbscript:msgbox%281%29)'):
            with self.subTest(source=source):
                rendered = self.render_text(source)
                for unsafe in ('<img ', '<iframe ', 'href="javascript:', 'href="data:', 'href="vbscript:'):
                    self.assertNotIn(unsafe, rendered)

    def test_code_remains_literal_and_does_not_linkify(self):
        rendered = self.render_text('```html\n<script>alert(1)</script>\nhttps://example.com/code\n**literal**\n```')
        self.assertEqual(rendered.count('<pre><code class="language-html">'), 2)
        self.assertEqual(rendered.count('&lt;script&gt;alert(1)&lt;/script&gt;'), 2)
        self.assertNotIn('href="https://example.com/code"', rendered)
        self.assertNotIn('<strong>literal</strong>', rendered)

    def test_markdown_images_do_not_load_remote_resources(self):
        rendered = self.render_text('![Screenshot](https://example.com/tracking.png)')
        self.assertNotIn('<img src="https://example.com/tracking.png"', rendered)
        self.assertIn('Screenshot', rendered)
