"""Headless Chromium initialization with stealth hardening.

`initialize_driver()` returns a configured Selenium WebDriver suitable for
rendering JavaScript-heavy pages while clearing basic bot protections. Chrome
and chromedriver locations are read from the environment so the same code runs
locally and inside the version-locked Docker image.
"""

from __future__ import annotations

import os

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium_stealth import stealth

CHROME_BIN = os.environ.get("CHROME_BIN")
CHROMEDRIVER_PATH = os.environ.get("CHROMEDRIVER_PATH")

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
