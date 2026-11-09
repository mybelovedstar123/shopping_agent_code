"""Walk through the app in a real browser, and save screenshots. Not part of the test suite.

Usage: python scripts/browser_check.py http://127.0.0.1:8000 screenshots/
Start from an empty database. Needs Selenium and Chrome.
"""

import glob
import os
import sys
import time
from pathlib import Path

from selenium import webdriver
from selenium.common.exceptions import TimeoutException
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait

BASE = sys.argv[1]
OUT = Path(sys.argv[2])
OUT.mkdir(parents=True, exist_ok=True)

options = webdriver.ChromeOptions()
options.add_argument("--headless=new")
options.add_argument("--window-size=1200,1000")
options.set_capability("goog:loggingPrefs", {"browser": "ALL"})
found = glob.glob(
    os.path.expandvars(r"%LOCALAPPDATA%\ms-playwright\chromium-*\chrome-win*\chrome.exe")
)
if found:
    options.binary_location = found[0]
driver = webdriver.Chrome(options=options)
wait = WebDriverWait(driver, 25)


def shot(name):
    driver.save_screenshot(str(OUT / f"{name}.png"))


def go(path):
    driver.get(BASE + path)


def fill(name, value):
    field = driver.find_element(By.NAME, name)
    field.clear()
    field.send_keys(value)


def click(text):
    found = driver.find_elements(
        By.XPATH,
        f"//main//button[normalize-space()='{text}'] | //main//a[normalize-space()='{text}']",
    )
    found[0].click()


try:
    go("/")
    shot("1-home")
    go("/search?category=mugs")
    shot("2-search")
    product_link = driver.find_element(By.CSS_SELECTOR, ".product-card a").get_attribute("href")

    go("/register")
    fill("name", "Browser Shopper")
    fill("email", "browser@example.com")
    fill("password", "a long password")
    click("Create account")
    wait.until(lambda d: "Log out" in d.page_source)
    print("registered and signed in")

    # Buy by hand: find a product that is in stock.
    go("/search?category=mugs&in_stock=true")
    driver.find_element(By.CSS_SELECTOR, ".product-card a").click()
    shot("3-product")
    click("Add to cart")
    wait.until(lambda d: "Your cart" in d.page_source)
    shot("4-cart")
    click("Check out")
    fill("card_number", "4242 4242 4242 4242")
    shot("5-checkout")
    driver.find_element(By.XPATH, "//button[starts-with(normalize-space(), 'Pay')]").click()
    wait.until(lambda d: "Your order is placed" in d.page_source)
    shot("6-order")
    print("bought by hand through the browser")

    # The assistant, relying on live updates with no reload.
    go("/assistant")
    fill("request", "Buy a mug. Ask me at most 1 question.")
    shot("7-assistant")
    click("Start shopping")
    print("assistant started; waiting for the page to update itself, with no reload")
    url_before = driver.current_url
    wait.until(lambda d: d.find_elements(By.NAME, "answer"))
    assert driver.current_url == url_before
    shot("8-question")
    print("question appeared by live update:", driver.find_element(By.CSS_SELECTOR, ".ask p").text)
    fill("answer", "I don't mind.")
    click("Answer")
    wait.until(lambda d: d.find_elements(By.NAME, "card_number"))
    shot("9-approval")
    print("approval appeared:", driver.find_element(By.CSS_SELECTOR, ".ask .total").text)

    # Typing must survive the page's live updates.
    field = driver.find_element(By.NAME, "card_number")
    field.send_keys("4242 4242 ")
    time.sleep(4)
    assert driver.find_element(By.NAME, "card_number").get_attribute("value") == "4242 4242 ", (
        "typing was lost"
    )
    print("half-typed card number survived live updates")
    field = driver.find_element(By.NAME, "card_number")
    field.send_keys("4242 4242")
    driver.find_element(
        By.XPATH, "//button[starts-with(normalize-space(), 'Approve and pay')]"
    ).click()
    wait.until(lambda d: 'data-status="purchased"' in d.page_source)
    shot("10-purchased")
    print("assistant purchase completed after approval")
    driver.find_element(By.LINK_TEXT, "See the order").click()
    wait.until(lambda d: "Bought by your assistant" in d.page_source)
    shot("11-assistant-order")
    go("/orders")
    print("orders listed:", len(driver.find_elements(By.CSS_SELECTOR, ".order-list li")))
    go("/assistant/1/timeline")
    shot("12-timeline")

    # A phone-sized screen.
    driver.set_window_size(390, 800)
    for name, path in (
        ("13-phone-search", "/search?category=mugs"),
        ("14-phone-cart", "/orders/1"),
    ):
        go(path)
        wide = driver.execute_script(
            "return document.documentElement.scrollWidth > window.innerWidth + 1"
        )
        print(f"{name}: sideways scrolling = {wide}")
        shot(name)

    problems = [e for e in driver.get_log("browser") if e["level"] == "SEVERE"]
    print("browser errors:", len(problems))
    for entry in problems[:10]:
        print("  ", entry["message"][:200])
    print("BROWSER CHECK PASSED")
except (AssertionError, TimeoutException) as error:
    shot("failure")
    print("BROWSER CHECK FAILED:", repr(error)[:500])
    print(driver.current_url)
    print(driver.find_element(By.TAG_NAME, "body").text[:900])
    sys.exit(1)
finally:
    driver.quit()
