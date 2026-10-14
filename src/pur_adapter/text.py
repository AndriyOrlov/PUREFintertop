"""Note text. PureRef reads note text as HTML (Qt rich text).

Measured with native PureRef 2.1.3: a bare newline in note text is shown as a
space, exactly like HTML. Text written by PureRef itself is a full HTML
document starting with <!DOCTYPE HTML ...>.
"""

import html
from html.parser import HTMLParser
import re

# Rich text starts with a doctype, <html>, <qt> or a paragraph tag.
# A plain note that merely mentions "<paint>" is not rich text.
_RICH_TEXT_START = re.compile(r"\s*<(!doctype|html|qt|p)[\s>]", re.IGNORECASE)


def is_rich_text(text):
    return bool(_RICH_TEXT_START.match(text))


class _PlainTextCollector(HTMLParser):
    """Collect visible text; paragraphs and <br> become line breaks."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.hidden_depth = 0  # inside <head>, <style> or <script>

    def handle_starttag(self, tag, attrs):
        if tag in ("head", "style", "script"):
            self.hidden_depth += 1
        if not self.hidden_depth and tag == "br":
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in ("head", "style", "script"):
            self.hidden_depth = max(0, self.hidden_depth - 1)
        if not self.hidden_depth and tag in ("p", "li", "div"):
            self.parts.append("\n")

    def handle_data(self, data):
        if not self.hidden_depth:
            self.parts.append(data)


def plain_text(text):
    """Visible text of a note; plain notes are returned unchanged."""
    if not is_rich_text(text):
        return text
    collector = _PlainTextCollector()
    collector.feed(text)
    return "".join(collector.parts).strip()


def html_from_plain(text):
    """Wrap plain text so PureRef shows it as written, line breaks included."""
    lines = [html.escape(line) for line in text.split("\n")]
    return "<html><body><p>" + "<br>".join(lines) + "</p></body></html>"
