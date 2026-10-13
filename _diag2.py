"""Definitive structure diagnostics for the served app page.

1) html.parser-based div-stack walk of the RENDERED template:
   - where each page-section actually sits (its div ancestors)
   - whether the document's div balance is clean at the end
2) Compare: same walk on the ORIGINAL (git HEAD) template for reference.
"""
import io
import os
import subprocess
from html.parser import HTMLParser
from types import SimpleNamespace

import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'billflow.settings')
django.setup()

from django.template.loader import get_template  # noqa: E402

CTX = {
    'state_json': 'null',
    'sharing_json': '{}',
    'sharing': {'viewing_shared': False, 'owner_name': '', 'role': 'owner',
                'readOnly': False, 'owner_id': 1,
                'my_pages': [{'id': 'mine', 'name': 'My BillFlow', 'active': True}]},
    'display_name': 'Test', 'user_initial': 'T', 'google_ready': False,
    'user': SimpleNamespace(email='', is_authenticated=True),
}


class DivWalk(HTMLParser):
    """Track ONLY div elements (immune to valid unclosed <p> etc.).
    html.parser treats <script>/<style> bodies as CDATA, so JS template
    literals full of fake tags cannot pollute the walk."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack = []
        self.sections = []   # (section id, div-ancestor chain)
        self.errors = []

    def handle_starttag(self, tag, attrs):
        if tag != 'div':
            return
        d = dict(attrs)
        label = d.get('id') or ('.' + d['class'].split()[0] if d.get('class') else '<div>')
        if 'page-section' in (d.get('class') or ''):
            self.sections.append((d.get('id'), list(self.stack)))
        self.stack.append(label)

    def handle_startendtag(self, tag, attrs):
        pass  # self-closing <div/> never appears; ignore

    def handle_endtag(self, tag):
        if tag != 'div':
            return
        if self.stack:
            self.stack.pop()
        else:
            self.errors.append('EXTRA </div> near line %s' % str(self.getpos()))


def walk(label, html_text):
    w = DivWalk()
    w.feed(html_text)
    w.close()
    out = ['== %s ==' % label]
    for sid, chain in w.sections:
        out.append('  page-section %-14s ancestors: %s' % (sid, chain or 'NONE (direct child)'))
    out.append('  unclosed divs at EOF: %s' % (w.stack or 'none'))
    out.append('  extra </div>: %s' % (w.errors or 'none'))
    return out


current = io.open('templates/tracker/app.html', encoding='utf-8').read()
rendered = get_template('tracker/app.html').render(CTX)

result = walk('RENDERED (what the browser receives)', rendered)
result += walk('RAW current template', current)

head = subprocess.run(
    ['git', 'show', 'HEAD:templates/tracker/app.html'],
    capture_output=True, text=True, encoding='utf-8').stdout
result += walk('RAW git-HEAD template (pre-changes, reference)', head)

io.open('_diag2.txt', 'w', encoding='utf-8').write('\n'.join(result))