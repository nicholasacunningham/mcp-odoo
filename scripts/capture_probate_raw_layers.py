import base64
import json
import time
from pathlib import Path

from selenium import webdriver
from selenium.common.exceptions import TimeoutException
from selenium.webdriver.chrome.options import Options

URL = "https://www.southcarolinaprobate.net/search/ViewImage.aspx?id=61fd5200-b215-49a2-ba59-0ca38cc3aa3e"
OUT = Path("probate_raw_layers")
OUT.mkdir(exist_ok=True)

opts = Options()
opts.page_load_strategy = "eager"
opts.add_argument("--headless=new")
opts.add_argument("--no-sandbox")
opts.add_argument("--disable-dev-shm-usage")
opts.add_argument("--window-size=1800,2400")
opts.add_argument("--ignore-certificate-errors")

driver = webdriver.Chrome(options=opts)
driver.set_page_load_timeout(30)

def save_data_url(data_url, path):
    if not data_url or ',' not in data_url:
        return False
    payload = data_url.split(',', 1)[1]
    path.write_bytes(base64.b64decode(payload))
    return True

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

    records = []
    for i in range(6):
        driver.execute_script("arguments[0].click();", driver.find_element("id", f"thumbContainer{i}"))
        time.sleep(3)
        container = driver.find_element("id", f"pageContainer{i}")
        for _ in range(30):
            imgs = container.find_elements("tag name", "img")
            canvases = container.find_elements("tag name", "canvas")
            if imgs and canvases:
                break
            time.sleep(0.5)
        meta = driver.execute_script("""
          const c = arguments[0];
          return {
            html: c.innerHTML,
            imgs: Array.from(c.querySelectorAll('img')).map(x => ({id:x.id, src:x.src, naturalWidth:x.naturalWidth, naturalHeight:x.naturalHeight, width:x.width, height:x.height, z:getComputedStyle(x).zIndex, opacity:getComputedStyle(x).opacity})),
            canvases: Array.from(c.querySelectorAll('canvas')).map(x => ({className:x.className, width:x.width, height:x.height, cssWidth:x.clientWidth, cssHeight:x.clientHeight, z:getComputedStyle(x).zIndex, opacity:getComputedStyle(x).opacity}))
          };
        """, container)
        records.append({"page": i+1, **meta})
        (OUT / f"page-{i+1:02d}-meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")

        # Extract the raw image pixels, bypassing composited overlays.
        img_data = driver.execute_script("""
          const img = arguments[0].querySelector('img');
          if (!img || !img.naturalWidth) return null;
          const cv = document.createElement('canvas');
          cv.width = img.naturalWidth; cv.height = img.naturalHeight;
          const ctx = cv.getContext('2d');
          ctx.drawImage(img, 0, 0);
          return cv.toDataURL('image/png');
        """, container)
        save_data_url(img_data, OUT / f"page-{i+1:02d}-raw-img.png")

        # Extract each canvas's own backing pixels, not the composited browser screenshot.
        canvases = container.find_elements("tag name", "canvas")
        for j, cv in enumerate(canvases, 1):
            data = driver.execute_script("return arguments[0].toDataURL('image/png');", cv)
            save_data_url(data, OUT / f"page-{i+1:02d}-raw-canvas-{j}.png")
        print("page", i+1, "raw image and", len(canvases), "canvases", flush=True)

    (OUT / "all-meta.json").write_text(json.dumps(records, indent=2), encoding="utf-8")
finally:
    driver.quit()
