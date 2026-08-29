import time
from pathlib import Path
from selenium import webdriver
from selenium.common.exceptions import TimeoutException
from selenium.webdriver.chrome.options import Options

URL = "https://www.southcarolinaprobate.net/search/ViewImage.aspx?id=61fd5200-b215-49a2-ba59-0ca38cc3aa3e"
OUT = Path("probate_layers")
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
        raise RuntimeError("viewer iframe not found")
    driver.switch_to.frame(frames[0])

    for i in range(6):
        driver.execute_script("arguments[0].click();", driver.find_element("id", f"thumbContainer{i}"))
        time.sleep(3)
        container = driver.find_element("id", f"pageContainer{i}")
        for _ in range(20):
            imgs = container.find_elements("tag name", "img")
            canvases = container.find_elements("tag name", "canvas")
            if imgs and canvases:
                break
            time.sleep(0.5)
        driver.execute_script("arguments[0].scrollIntoView({block:'center'});", container)
        time.sleep(1)
        container.screenshot(str(OUT / f"page-{i+1:02d}-composite.png"))
        imgs = container.find_elements("tag name", "img")
        for j, el in enumerate(imgs):
            el.screenshot(str(OUT / f"page-{i+1:02d}-img-{j+1}.png"))
        canvases = container.find_elements("tag name", "canvas")
        for j, el in enumerate(canvases):
            el.screenshot(str(OUT / f"page-{i+1:02d}-canvas-{j+1}.png"))
        print(i+1, "imgs", len(imgs), "canvases", len(canvases), flush=True)
finally:
    driver.quit()
