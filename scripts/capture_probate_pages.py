import time
from pathlib import Path

from selenium import webdriver
from selenium.common.exceptions import TimeoutException
from selenium.webdriver.chrome.options import Options

URL = "https://www.southcarolinaprobate.net/search/ViewImage.aspx?id=61fd5200-b215-49a2-ba59-0ca38cc3aa3e"
OUT = Path("probate_pages")
OUT.mkdir(exist_ok=True)

opts = Options()
opts.page_load_strategy = "eager"
opts.add_argument("--headless=new")
opts.add_argument("--no-sandbox")
opts.add_argument("--disable-dev-shm-usage")
opts.add_argument("--window-size=1800,2400")
opts.add_argument("--force-device-scale-factor=1")
opts.add_argument("--ignore-certificate-errors")

driver = webdriver.Chrome(options=opts)
driver.set_page_load_timeout(30)
try:
    try:
        driver.get(URL)
    except TimeoutException:
        pass
    time.sleep(10)

    frames = driver.find_elements("tag name", "iframe")
    if not frames:
        raise RuntimeError("Viewer iframe not found")
    driver.switch_to.frame(frames[0])

    # Wait until the viewer has all six page sections/thumbnails.
    for _ in range(30):
        thumbs = driver.find_elements("css selector", ".thumbContainer")
        sections = driver.find_elements("css selector", ".pageSection")
        if len(thumbs) >= 6 and len(sections) >= 6:
            break
        time.sleep(1)
    print("thumbs", len(thumbs), "sections", len(sections), flush=True)

    for i in range(6):
        # Clicking the thumbnail tells the viewer to load/render the requested page.
        driver.execute_script("arguments[0].click();", driver.find_element("id", f"thumbContainer{i}"))
        time.sleep(3)
        container = driver.find_element("id", f"pageContainer{i}")
        # Wait until the page has its rendered image/canvas content.
        for _ in range(20):
            imgs = container.find_elements("tag name", "img")
            canvases = container.find_elements("tag name", "canvas")
            if imgs or canvases:
                break
            time.sleep(0.5)
        driver.execute_script("arguments[0].scrollIntoView({block:'center'});", container)
        time.sleep(1)
        path = OUT / f"page-{i+1:02d}.png"
        ok = container.screenshot(str(path))
        print("captured", i + 1, ok, path, "size", container.size, "imgs", len(imgs), "canvases", len(canvases), flush=True)

    driver.switch_to.default_content()
    driver.save_screenshot(str(OUT / "viewer-full.png"))
finally:
    driver.quit()
