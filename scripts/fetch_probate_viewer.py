import json
import os
import re
import time
from pathlib import Path
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup
from selenium import webdriver
from selenium.webdriver.chrome.options import Options

URL = "https://www.southcarolinaprobate.net/search/ViewImage.aspx?id=61fd5200-b215-49a2-ba59-0ca38cc3aa3e"
OUT = Path("probate_capture")
OUT.mkdir(exist_ok=True)


def safe_name(url, idx, ctype=""):
    p = urlparse(url)
    base = os.path.basename(p.path) or f"resource_{idx}"
    base = re.sub(r"[^A-Za-z0-9._-]+", "_", base)[:100]
    if "." not in base:
        if "pdf" in ctype:
            base += ".pdf"
        elif "image/jpeg" in ctype:
            base += ".jpg"
        elif "image/png" in ctype:
            base += ".png"
        else:
            base += ".bin"
    return f"{idx:03d}_{base}"

# Plain HTTP request first
try:
    r = requests.get(URL, timeout=30, allow_redirects=True)
    (OUT / "requests_body.bin").write_bytes(r.content)
    (OUT / "requests_meta.json").write_text(json.dumps({
        "status": r.status_code,
        "url": r.url,
        "headers": dict(r.headers),
        "content_type": r.headers.get("content-type"),
        "length": len(r.content),
    }, indent=2))
    print("REQUESTS", r.status_code, r.url, r.headers.get("content-type"), len(r.content))
except Exception as e:
    print("REQUESTS ERROR", repr(e))

opts = Options()
opts.add_argument("--headless=new")
opts.add_argument("--no-sandbox")
opts.add_argument("--disable-dev-shm-usage")
opts.add_argument("--window-size=1600,1200")
opts.add_argument("--ignore-certificate-errors")
opts.set_capability("goog:loggingPrefs", {"performance": "ALL", "browser": "ALL"})

driver = webdriver.Chrome(options=opts)
try:
    driver.get(URL)
    time.sleep(12)
    (OUT / "page_source.html").write_text(driver.page_source, encoding="utf-8")
    driver.save_screenshot(str(OUT / "viewer.png"))
    print("TITLE", driver.title)
    print("FINALURL", driver.current_url)

    # Collect DOM URLs from all accessible frames
    dom_records = []
    def collect_dom(frame_label):
        html = driver.page_source
        soup = BeautifulSoup(html, "html.parser")
        for tag, attr in [("iframe", "src"), ("frame", "src"), ("img", "src"), ("embed", "src"), ("object", "data"), ("a", "href"), ("script", "src")]:
            for el in soup.find_all(tag):
                val = el.get(attr)
                if val:
                    dom_records.append({"frame": frame_label, "tag": tag, "attr": attr, "value": val})
    collect_dom("top")
    frames = driver.find_elements("tag name", "iframe") + driver.find_elements("tag name", "frame")
    for i, fr in enumerate(frames):
        try:
            driver.switch_to.frame(fr)
            collect_dom(f"frame_{i}")
            (OUT / f"frame_{i}.html").write_text(driver.page_source, encoding="utf-8")
            driver.switch_to.default_content()
        except Exception as e:
            print("FRAME ERROR", i, repr(e))
            try:
                driver.switch_to.default_content()
            except Exception:
                pass
    (OUT / "dom_urls.json").write_text(json.dumps(dom_records, indent=2), encoding="utf-8")
    for rec in dom_records:
        print("DOM", rec)

    # Decode Chrome performance network log
    responses = []
    for entry in driver.get_log("performance"):
        try:
            msg = json.loads(entry["message"])["message"]
            if msg["method"] == "Network.responseReceived":
                p = msg["params"]
                resp = p["response"]
                responses.append({
                    "url": resp.get("url"),
                    "status": resp.get("status"),
                    "mimeType": resp.get("mimeType"),
                    "type": p.get("type"),
                    "requestId": p.get("requestId"),
                    "headers": resp.get("headers", {}),
                })
        except Exception:
            pass
    (OUT / "network_responses.json").write_text(json.dumps(responses, indent=2), encoding="utf-8")

    for rec in responses:
        print("NET", rec["status"], rec["type"], rec["mimeType"], rec["url"])

    # Re-fetch candidate resources with browser cookies so binary docs/images are preserved
    sess = requests.Session()
    ua = driver.execute_script("return navigator.userAgent")
    sess.headers.update({"User-Agent": ua, "Referer": driver.current_url})
    for c in driver.get_cookies():
        sess.cookies.set(c["name"], c["value"], domain=c.get("domain"), path=c.get("path", "/"))

    seen = set()
    candidates = []
    for rec in responses:
        u = rec.get("url") or ""
        mime = (rec.get("mimeType") or "").lower()
        typ = (rec.get("type") or "").lower()
        if u.startswith("http") and u not in seen and (
            "pdf" in mime or mime.startswith("image/") or typ in {"document", "xhr", "fetch", "image"}
            or any(x in u.lower() for x in ["image", "document", "view", "pdf", "download", "handler", ".ashx", ".aspx"])
        ):
            seen.add(u)
            candidates.append((u, mime))

    cand_meta = []
    for idx, (u, mime_hint) in enumerate(candidates, 1):
        try:
            rr = sess.get(u, timeout=30, allow_redirects=True)
            ctype = (rr.headers.get("content-type") or mime_hint or "").lower()
            fname = safe_name(rr.url, idx, ctype)
            (OUT / fname).write_bytes(rr.content)
            cand_meta.append({"source_url": u, "final_url": rr.url, "status": rr.status_code, "content_type": ctype, "length": len(rr.content), "file": fname})
            print("SAVE", rr.status_code, ctype, len(rr.content), fname, rr.url)
        except Exception as e:
            print("SAVE ERROR", u, repr(e))
    (OUT / "candidate_fetches.json").write_text(json.dumps(cand_meta, indent=2), encoding="utf-8")
finally:
    driver.quit()
