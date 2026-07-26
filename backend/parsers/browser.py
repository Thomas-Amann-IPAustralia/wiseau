"""Headless Chromium initialization with stealth hardening.

`initialize_driver()` returns a configured Selenium WebDriver suitable for
rendering JavaScript-heavy pages while clearing basic bot protections. Chrome
and chromedriver locations are read from the environment so the same code runs
locally and inside the version-locked Docker image.

`fetch_bytes()` is the other half of the browser transport: it downloads a URL's
*raw bytes* from inside the already-navigated page, so the request carries the
session's cookies, TLS handshake, and any WAF clearance the render just earned.
That is what makes direct-PDF links (very common on government sites) reachable
— a second, plain HTTP client would be challenged all over again.
"""

from __future__ import annotations

import base64
import binascii
import logging
import os

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium_stealth import stealth

logger = logging.getLogger("markdown_engine.browser")

CHROME_BIN = os.environ.get("CHROME_BIN")
CHROMEDRIVER_PATH = os.environ.get("CHROMEDRIVER_PATH")

# Ceiling for the in-page download script. Generous: a scanned government PDF
# can be tens of megabytes over a slow origin, and the caller already sits
# behind the backend's concurrency ceiling.
DOWNLOAD_TIMEOUT = 60  # seconds

# Downloads the URL from the current page's context and hands the bytes back as
# base64 (the WebDriver bridge is text-only). Resolves with `null` on any
# failure so the caller degrades instead of hanging until the script timeout.
# Chunked base64 conversion keeps a large file from blowing the argument limit
# of `String.fromCharCode.apply`.
_DOWNLOAD_SCRIPT = """
const url = arguments[0];
const done = arguments[arguments.length - 1];
fetch(url, {credentials: 'include', redirect: 'follow'})
  .then(function (response) {
    if (!response.ok) { throw new Error('HTTP ' + response.status); }
    return response.arrayBuffer();
  })
  .then(function (buffer) {
    const bytes = new Uint8Array(buffer);
    const chunk = 0x8000;
    let binary = '';
    for (let i = 0; i < bytes.length; i += chunk) {
      binary += String.fromCharCode.apply(null, bytes.subarray(i, i + chunk));
    }
    done(btoa(binary));
  })
  .catch(function () { done(null); });
"""

# Robust arguments for containerized, headless execution.
CHROME_ARGS = [
    "--headless=new",
    "--no-sandbox",
    "--disable-dev-shm-usage",
    "--disable-gpu",
    "--window-size=1920,1080",
    "--disable-blink-features=AutomationControlled",
    "--lang=en-US",
]


def initialize_driver() -> webdriver.Chrome:
    """Build a stealth-configured headless Chrome driver."""
    options = Options()
    for arg in CHROME_ARGS:
        options.add_argument(arg)
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option("useAutomationExtension", False)
    if CHROME_BIN:
        options.binary_location = CHROME_BIN

    service = Service(executable_path=CHROMEDRIVER_PATH) if CHROMEDRIVER_PATH else Service()
    driver = webdriver.Chrome(service=service, options=options)

    stealth(
        driver,
        languages=["en-US", "en"],
        vendor="Google Inc.",
        platform="Win32",
        webgl_vendor="Intel Inc.",
        renderer="Intel Iris OpenGL Engine",
        fix_hairline=True,
    )
    return driver


def fetch_bytes(driver: webdriver.Chrome, url: str, *, timeout: int = DOWNLOAD_TIMEOUT) -> bytes | None:
    """Download ``url`` through the driver's own session; ``None`` if it fails.

    Runs `fetch()` inside the page the driver is already on, so the request
    inherits its cookies and origin — the point being that a link which only
    answered the *browser* (WAF clearance, session cookie) answers this too.
    Never raises: any failure (script error, timeout, non-2xx, undecodable
    payload) returns ``None`` so the caller can fall back to the HTML path.
    """
    try:
        driver.set_script_timeout(timeout)
        encoded = driver.execute_async_script(_DOWNLOAD_SCRIPT, url)
    except Exception as exc:  # noqa: BLE001 - the browser is best-effort here
        logger.info("In-page download of %s failed: %s", url, exc)
        return None

    if not isinstance(encoded, str) or not encoded:
        return None
    try:
        return base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError) as exc:
        logger.info("In-page download of %s returned an undecodable payload: %s", url, exc)
        return None
