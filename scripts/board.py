"""Add and remove links on the board.

Every link gets a square preview image (the page's link-preview image, or a
screenshot of the page when it has none), a short name and a folder. The raw
metadata fetched from the page is kept on each record under "source".
"""

import datetime
import hashlib
import html
import io
import json
import os
import re
import urllib.parse
import urllib.request
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
LINKS_FILE = ROOT / "links.json"
IMG_DIR = ROOT / "img"
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130 Safari/537.36"
TILE_PX = 480

DEFAULT_FOLDERS = [
    "Portfolios",
    "Fonts",
    "Colors",
    "Textures",
    "Logos",
    "UI kits",
    "Companies",
    "Landing pages",
    "Guides",
    "Events",
    "Articles",
    "Videos",
    "Notes",
    "Inspiration",
    "Dev tools",
    "Other",
]

KEYWORD_FOLDERS = [
    ("Fonts", ["font", "typeface", "type foundry", "typography", "lettering"]),
    ("Colors", ["color", "colour", "palette", "gradient"]),
    ("Textures", ["texture", "grain", "pattern"]),
    ("Logos", ["logo", "trademark", "symbol", "brand mark"]),
    ("Portfolios", ["portfolio", "designer", "my work", "case stud", "framer.website"]),
    ("Guides", ["guide", "how to", "tutorial", "templates", "tips"]),
    ("Events", ["conference", "summit", "apply to speak", "tickets", "meetup"]),
    ("Landing pages", ["landing page"]),
    ("Videos", ["youtube.com", "youtu.be", "tiktok.com", "vimeo.com"]),
    ("Notes", ["notes.granola.ai"]),
    ("Articles", ["medium.com", "/blog/", "substack.com"]),
]

TRACKING_PARAMS = re.compile(
    r"^(utm_.*|gclid|gbraid|wbraid|gad_source|gad_campaignid|fbclid|msclkid|mc_cid|mc_eid|igsh|shem|si|_hsenc|_hsmi|content|ref_src)$",
    re.I,
)
SHORTENERS = {"share.google", "t.co", "bit.ly", "goo.gl", "tinyurl.com", "lnkd.in", "buff.ly", "ow.ly", "search.app"}
OEMBED = {
    "youtube.com": "https://www.youtube.com/oembed?format=json&url=",
    "youtu.be": "https://www.youtube.com/oembed?format=json&url=",
    "tiktok.com": "https://www.tiktok.com/oembed?url=",
    "vimeo.com": "https://vimeo.com/api/oembed.json?url=",
}


def load_links():
    if LINKS_FILE.exists():
        return json.loads(LINKS_FILE.read_text())
    return []


def save_links(links):
    LINKS_FILE.write_text(json.dumps(links, indent=1, ensure_ascii=False) + "\n")


def normalize(url):
    url = url.strip()
    if not re.match(r"^https?://", url, re.I):
        url = "https://" + url
    parts = urllib.parse.urlsplit(url)
    query = urllib.parse.urlencode(
        [(k, v) for k, v in urllib.parse.parse_qsl(parts.query, keep_blank_values=True) if not TRACKING_PARAMS.match(k)]
    )
    return urllib.parse.urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path or "/", query, ""))


def same_link(a, b):
    def key(u):
        p = urllib.parse.urlsplit(normalize(u))
        return (p.netloc.removeprefix("www."), p.path.rstrip("/"), p.query)

    return key(a) == key(b)


def site_of(url):
    return urllib.parse.urlsplit(url).netloc.removeprefix("www.")


def link_id(url):
    return hashlib.sha1(url.encode()).hexdigest()[:10]


def _meta(page, keys):
    for k in keys:
        for pattern in (
            rf'<meta[^>]+(?:property|name)=["\']{k}["\'][^>]*content=["\']([^"\']+)',
            rf'<meta[^>]+content=["\']([^"\']+)["\'][^>]*(?:property|name)=["\']{k}["\']',
        ):
            m = re.search(pattern, page, re.I)
            if m:
                return html.unescape(m.group(1)).strip()
    return None


def _parse_page(page, final_url):
    title_tag = re.search(r"<title[^>]*>(.*?)</title>", page, re.I | re.S)
    image = _meta(page, ["og:image", "og:image:url", "twitter:image", "twitter:image:src"])
    if image:
        image = urllib.parse.urljoin(final_url, image)
    return {
        "final_url": final_url,
        "title": _meta(page, ["og:title", "twitter:title"])
        or (html.unescape(title_tag.group(1)).strip() if title_tag else None),
        "description": _meta(page, ["og:description", "description", "twitter:description"]),
        "site_name": _meta(page, ["og:site_name"]),
        "image": image,
    }


BLOCK_TITLES = re.compile(r"^(just a moment|attention required|access denied|forbidden|403|security check|are you a robot|verify you are human)", re.I)


def is_block_page(meta):
    return bool(meta.get("title") and BLOCK_TITLES.match(meta["title"].strip()))


def _oembed(url):
    endpoint = next((e for d, e in OEMBED.items() if site_of(url) == d or site_of(url).endswith("." + d)), None)
    if not endpoint:
        return {}
    try:
        req = urllib.request.Request(endpoint + urllib.parse.quote(url, safe=""), headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=20) as r:
            data = json.loads(r.read())
    except Exception:
        return {}
    return {"title": data.get("title"), "author": data.get("author_name"), "image": data.get("thumbnail_url")}


def _open_browser(p):
    import subprocess
    import sys

    if os.environ.get("CI") and not os.environ.get("BROWSER_PATH"):
        subprocess.run([sys.executable, "-m", "playwright", "install", "--with-deps", "chromium"], check=True)
    launch = {"args": ["--no-sandbox"]}
    if os.environ.get("BROWSER_PATH"):
        launch["executable_path"] = os.environ["BROWSER_PATH"]
    browser = p.chromium.launch(**launch)
    return browser, browser.new_page(viewport={"width": 1200, "height": 900}, user_agent=UA, locale="en-US")


def _browser_metadata(url):
    """For pages that refuse plain requests or only fill in their details with scripts."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser, page = _open_browser(p)
        page.goto(url, wait_until="domcontentloaded", timeout=45000)
        page.wait_for_timeout(4000)
        meta = _parse_page(page.content(), page.url)
        browser.close()
    return meta


def fetch_metadata(url):
    """The page's own title, description and preview image, as fetched."""
    meta = None
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "en"})
        with urllib.request.urlopen(req, timeout=25) as r:
            meta = _parse_page(r.read(1_500_000).decode("utf-8", "ignore"), r.url)
    except Exception as e:
        print(f"  plain fetch failed ({e}); opening it in a browser")
    if meta is None or not (meta["image"] or meta["description"]):
        try:
            meta = _browser_metadata(url)
        except Exception as e:
            if meta is None:
                raise
            print(f"  browser fetch failed ({e})")
    if is_block_page(meta):
        # The site refused automated visitors; keep only what the address tells us.
        meta = {"final_url": url, "title": None, "description": None, "site_name": None, "image": None, "blocked": True}
    embed = _oembed(meta["final_url"])
    if embed.get("title"):
        meta["title"] = embed["title"]
        meta["author"] = embed.get("author")
    if embed.get("image"):
        meta["image"] = embed["image"]
    return meta


def _square(img, anchor_top):
    img = img.convert("RGB")
    w, h = img.size
    side = min(w, h)
    left = (w - side) // 2
    top = 0 if anchor_top else (h - side) // 2
    return img.crop((left, top, left + side, top + side)).resize((TILE_PX, TILE_PX), Image.LANCZOS)


def _download_image(src):
    req = urllib.request.Request(src, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=30) as r:
        return Image.open(io.BytesIO(r.read()))


def screenshot(url):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser, page = _open_browser(p)
        page.goto(url, wait_until="domcontentloaded", timeout=45000)
        page.wait_for_timeout(5000)
        png = page.screenshot()
        title = page.title()
        browser.close()
    return Image.open(io.BytesIO(png)), title


def name_tile(text):
    """A plain tile with the site's name, for sites that refuse to be previewed."""
    from PIL import ImageDraw, ImageFont

    palette = [("#1d1d1f", "#ffffff"), ("#e8e4dc", "#1d1d1f"), ("#dfe7f0", "#1d1d1f"), ("#efe1e6", "#1d1d1f"), ("#e3eadf", "#1d1d1f")]
    bg, fg = palette[int(hashlib.sha1(text.encode()).hexdigest(), 16) % len(palette)]
    img = Image.new("RGB", (TILE_PX, TILE_PX), bg)
    draw = ImageDraw.Draw(img)
    label = text.split(".")[0] if text.count(".") <= 2 else text
    font = None
    for path in ("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf"):
        if os.path.exists(path):
            size = 110
            while size > 30:
                font = ImageFont.truetype(path, size)
                if draw.textlength(label, font=font) <= TILE_PX * 0.8:
                    break
                size -= 6
            break
    font = font or ImageFont.load_default()
    draw.text((TILE_PX / 2, TILE_PX / 2), label, fill=fg, font=font, anchor="mm")
    return img


def make_tile(url, meta):
    """Save the square tile image; returns (relative path, how it was made)."""
    IMG_DIR.mkdir(exist_ok=True)
    out = IMG_DIR / f"{link_id(url)}.webp"
    img, kind = None, None
    generic = meta.get("image") and re.search(r"notion\.(so|com)/images/meta", meta["image"])
    if meta.get("image") and not generic:
        try:
            img, kind = _square(_download_image(meta["image"]), anchor_top=False), "preview"
        except Exception as e:  # a broken preview image falls back to a screenshot
            print(f"  preview image failed ({e}); taking a screenshot")
    if img is None and not meta.get("blocked"):
        try:
            shot = screenshot(url)
            img, kind = _square(shot[0], anchor_top=True), "screenshot"
            if BLOCK_TITLES.match(shot[1] or ""):
                img = None
        except Exception as e:
            print(f"  screenshot failed ({e}); drawing a name tile")
    if img is None:
        img, kind = name_tile(meta.get("site_name") or site_of(url)), "name"
    img.save(out, "WEBP", quality=82)
    return f"img/{out.name}", kind


def _keyword_folder(text):
    text = text.lower()
    for folder, words in KEYWORD_FOLDERS:
        if any(w in text for w in words):
            return folder
    return "Other"


def _plugin_classifier():
    """An optional smarter sorter, named by LINK_BOARD_CLASSIFIER (a .py file defining classify())."""
    path = os.environ.get("LINK_BOARD_CLASSIFIER")
    if not path:
        return None
    import importlib.util

    spec = importlib.util.spec_from_file_location("link_board_classifier", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.classify


def classify(url, meta, folders, hint=None):
    """Pick a short name and a folder."""
    plugin = _plugin_classifier()
    if plugin:
        try:
            name, folder = plugin(url, meta, folders, hint)
            match = next((f for f in folders if f.lower() == folder.lower()), None)
            return name, match or folder[:1].upper() + folder[1:]
        except Exception as e:  # sorting must never block saving the link
            print(f"  automatic sorting failed ({e}); using keywords")
    text = " ".join(filter(None, [hint, url, meta.get("title"), meta.get("description")]))
    title = (meta.get("title") or site_of(url).split(".")[0].capitalize()).split("|")[0].split(" — ")[0].split(" - ")[0].strip()
    return title[:40], _keyword_folder(text)


def add_link(url, folder=None, name=None, hint=None):
    """Add one link. Returns (record, created). An existing link is updated in place."""
    url = normalize(url)
    links = load_links()
    existing = next((l for l in links if same_link(l["url"], url)), None)
    if existing:
        if not (folder or name):
            return existing, False
        existing.update(name=name or existing["name"], folder=folder or existing["folder"])
        save_links(links)
        return existing, False
    meta = fetch_metadata(url)
    if site_of(url) in SHORTENERS:
        url = normalize(meta["final_url"])
        existing = next((l for l in links if same_link(l["url"], url)), None)
        if existing:
            return existing, False
    folders = list(dict.fromkeys(DEFAULT_FOLDERS + [l["folder"] for l in links]))
    if not (folder and name):
        auto_name, auto_folder = classify(url, meta, folders, hint)
        name, folder = name or auto_name, folder or auto_folder
    image, image_kind = make_tile(url, meta)
    record = {
        "id": link_id(url),
        "url": url,
        "name": name,
        "folder": folder,
        "site": site_of(url),
        "image": image,
        "image_kind": image_kind,
        "added": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
        "source": meta,
    }
    links.append(record)
    save_links(links)
    return record, True


def remove_link(url):
    links = load_links()
    keep = [l for l in links if not same_link(l["url"], url)]
    gone = [l for l in links if same_link(l["url"], url)]
    for l in gone:
        (ROOT / l["image"]).unlink(missing_ok=True)
    save_links(keep)
    return gone


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("url")
    ap.add_argument("--folder")
    ap.add_argument("--name")
    ap.add_argument("--remove", action="store_true")
    a = ap.parse_args()
    if a.remove:
        print(f"removed {len(remove_link(a.url))}")
    else:
        rec, created = add_link(a.url, a.folder, a.name)
        print(("added" if created else "exists"), rec["folder"], "/", rec["name"], rec["image_kind"] if created else "")
