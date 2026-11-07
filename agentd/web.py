import re
import urllib.parse
import urllib.request
from html.parser import HTMLParser

MAX_BYTES = 2_097_152


class TextExtractor(HTMLParser):
    def __init__(self):
        super().__init__()
        self.title = ""
        self.text = []
        self.links = []
        self.skip = 0
        self.in_title = False

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style", "noscript"}:
            self.skip += 1
        elif tag == "title":
            self.in_title = True
        elif tag == "a":
            href = dict(attrs).get("href")
            if href:
                self.links.append(href)

    def handle_endtag(self, tag):
        if tag in {"script", "style", "noscript"} and self.skip:
            self.skip -= 1
        elif tag == "title":
            self.in_title = False

    def handle_data(self, data):
        if self.skip:
            return
        if self.in_title:
            self.title += data.strip()
        if data.strip():
            self.text.append(data.strip())


def extract(url, data):
    parser = TextExtractor()
    parser.feed(data.decode("utf-8", errors="replace"))
    text = re.sub(r"\s+", " ", " ".join(parser.text)).strip()
    return {
        "url": url,
        "title": parser.title,
        "text": text,
        "links": parser.links,
    }


def fetch(url):
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("URL must use http or https")
    request = urllib.request.Request(url, headers={"User-Agent": "AgentOS/0.1"})
    with urllib.request.urlopen(request, timeout=30) as response:
        data = response.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise ValueError("web response exceeds the 2 MiB limit")
    return extract(url, data)
