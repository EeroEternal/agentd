import shutil
import urllib.parse

from .web import MAX_BYTES, extract

TIMEOUT = 30_000


def fetch(url):
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("URL must use http or https")
    chromium = shutil.which("chromium") or shutil.which("chromium-browser")
    if not chromium:
        raise RuntimeError("Chromium is not installed")
    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            executable_path=chromium,
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-background-networking"],
        )
        try:
            page = browser.new_page()
            page.goto(url, wait_until="commit", timeout=TIMEOUT)
            page.wait_for_timeout(10_000)
            if not page.locator("body").inner_text().strip():
                modules = page.eval_on_selector_all(
                    "script[type='module'][src]",
                    "scripts => scripts.map(script => script.src)",
                )
                if modules:
                    page.evaluate(
                        "urls => Promise.all(urls.map(url => import(url)))",
                        modules,
                    )
                    page.wait_for_timeout(5000)
            data = page.content().encode("utf-8")
        finally:
            browser.close()
    if len(data) > MAX_BYTES:
        raise ValueError("rendered page exceeds the 2 MiB limit")
    return extract(url, data)
