#!/usr/bin/env python3
"""Generatore del sito statico Progetto Rebecca.

Uso:
    python tools/build.py            # scarica media e genera tutto
    python tools/build.py --no-media # rigenera le pagine senza riscaricare
    python tools/build.py --parse-only
"""
from __future__ import annotations

import argparse
import base64
import json
import math
import os
import re
import shutil
import sys
import time
import urllib.parse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from io import BytesIO
from pathlib import Path
from string import Template

import requests
from bs4 import BeautifulSoup
from PIL import Image, ImageDraw, ImageOps, ImageStat

# ---------------------------------------------------------------------------
# Percorsi
# ---------------------------------------------------------------------------
ROOT = Path(__file__).resolve().parents[1]
SRC = Path(r"C:\Users\Archivio\AppData\Local\Temp\opencode\rebecca-analysis\articles")
TMP = Path(r"C:\Users\Archivio\AppData\Local\Temp\opencode\rebecca-analysis")
ASSETS = ROOT / "assets"
IMG = ASSETS / "img"
NEWS_IMG = IMG / "news"
THUMBS = IMG / "thumbs"
VIDEO_IMG = IMG / "video"
DATA = ASSETS / "data"
TEMPLATES = Path(__file__).resolve().parent / "templates"
OUT_ARTICLES = ROOT / "eventi"
CONTENT = ROOT / "content"

BASE_URL = "https://www.progettorebecca.org"
SITE_URL = "https://www.progettorebecca.org"

STATIC_FILES = {
    "n124711-dove-siamo.htm": "dove",
    "n138208-progetto-rebecca-il-progetto-dei-bimbi.htm": "chi-siamo",
    "n53397-u-l-m-i.htm": "ulmi",
    "n53399-contatti.htm": "contatti",
    "n53401-sostieni-un-bimbo-a-distanza.htm": "sostieni",
    "n53405-5-per-mille-kindergarten.htm": "cinque-per-mille",
    "n53406-come-aiutarci.htm": "come-aiutarci",
    "n53407-video.htm": "video",
}
GALLERY_FILE = "n54624-foto-rebecca.htm"

STATIC_OLD_URLS = {
    "/s90-eventi.htm": "/eventi.html",
    "/s91-donare.htm": "/donare.html",
    "/s92-dove-siamo.htm": "/contatti.html",
    "/s93-chi-siamo.htm": "/chi-siamo.html",
    "/s94-gallery.htm": "/gallery.html",
    "/s95-contatti.htm": "/contatti.html",
    "/i1": "/contatti.html",
    "/i15": "/privacy.html",
    "/n53406-come-aiutarci.htm": "/donare.html#come-aiutarci",
    "/n53405-5-per-mille-kindergarten.htm": "/donare.html#cinque-x-mille",
    "/n53401-sostieni-un-bimbo-a-distanza.htm": "/donare.html#sostieni",
    "/n54624-foto-rebecca.htm": "/gallery.html",
    "/n53407-video.htm": "/gallery.html#video",
    "/n138208-progetto-rebecca-il-progetto-dei-bimbi.htm": "/chi-siamo.html",
    "/n53397-u-l-m-i.htm": "/ulmi.html",
    "/n124711-dove-siamo.htm": "/contatti.html",
    "/n53399-contatti.htm": "/contatti.html",
}

MONTHS = [
    "", "gennaio", "febbraio", "marzo", "aprile", "maggio", "giugno",
    "luglio", "agosto", "settembre", "ottobre", "novembre", "dicembre",
]

UA = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )
}

YT_RE = re.compile(r"(?:youtube\.com/embed/|youtu\.be/|v=)([A-Za-z0-9_-]{11})")
IMAGE_RE = re.compile(r"/image\.php\?id=(\d+)(?:&(?:amp;)?num=(\d+))?")
COVER_RE = re.compile(r"/image-(\d+)-\d+-\d+\.jpg")
GALLERY_LIMIT = 120
MIN_COVER_STDDEV = 12.0
TAG_BLOCKLIST = {"aggiungi il tuo"}


# ---------------------------------------------------------------------------
# Utility
# ---------------------------------------------------------------------------
def clean_text(value: str) -> str:
    value = value.replace("\xa0", " ")
    value = re.sub(r"\s+", " ", value)
    return value.strip()


def parse_date(value: str) -> datetime | None:
    if not value:
        return None
    value = value.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def date_label(dt: datetime | None) -> str:
    if not dt:
        return ""
    return f"{dt.day} {MONTHS[dt.month]} {dt.year}"


def slugify(text: str) -> str:
    text = text.lower()
    text = re.sub(r"[^a-z0-9]+", "-", text)
    return text.strip("-")


def read_soup(path: Path) -> BeautifulSoup:
    return BeautifulSoup(path.read_text(encoding="utf-8"), "lxml")


def article_parts(filename: str) -> tuple[str, str]:
    m = re.match(r"n(\d+)-(.+)\.htm$", filename)
    if not m:
        raise ValueError(f"Nome non riconosciuto: {filename}")
    return m.group(1), m.group(2)


def excerpt_from(text: str, limit: int = 190) -> str:
    text = clean_text(text)
    if len(text) <= limit:
        return text
    cut = text[:limit].rsplit(" ", 1)[0]
    return cut.rstrip(".,;:") + "…"


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------
def extract_images(node: BeautifulSoup | None) -> list[dict]:
    images = []
    if node is None:
        return images
    seen = set()
    for img in node.find_all("img"):
        src = img.get("data-original") or img.get("src") or ""
        m = IMAGE_RE.search(src)
        if not m:
            continue
        aid = m.group(1)
        num = m.group(2) or "1"
        key = (aid, num)
        if key in seen:
            continue
        seen.add(key)
        alt = clean_text(img.get("alt", ""))
        images.append({"id": aid, "num": num, "alt": alt})
    return images


def extract_videos(node: BeautifulSoup | None) -> list[str]:
    videos = []
    if node is None:
        return videos
    for iframe in node.find_all("iframe"):
        src = iframe.get("src", "")
        m = YT_RE.search(src)
        if m:
            videos.append(m.group(1))
    return list(dict.fromkeys(videos))


def extract_tags(soup: BeautifulSoup) -> list[str]:
    tags = []
    for a in soup.select("a.tag"):
        t = clean_text(a.get_text(" ", strip=True))
        if t and t.lower() not in TAG_BLOCKLIST:
            tags.append(t)
    # deduplica mantenendo l'ordine
    return list(dict.fromkeys(tags))


def image_stddev(path: Path) -> float:
    try:
        with Image.open(path) as img:
            img = img.convert("L")
            img.thumbnail((96, 96))
            return ImageStat.Stat(img).stddev[0]
    except Exception:
        return 0.0


def image_is_landscape(path: Path) -> bool:
    try:
        with Image.open(path) as img:
            w, h = img.size
            return w >= h * 1.05
    except Exception:
        return False


BLOCK_TAGS = {
    "div", "p", "h1", "h2", "h3", "h4", "h5", "h6", "ul", "ol", "li",
    "table", "blockquote", "figure", "section", "article", "hr", "img",
}


def paragraphize(html: str) -> str:
    """Trasforma il testo sciolto del vecchio CMS in paragrafi leggibili."""
    if not html:
        return ""
    soup = BeautifulSoup(html, "html.parser")
    out = []
    buf = []
    skip_br = False

    def flush() -> None:
        if not buf:
            return
        inner = "".join(buf).strip()
        inner = re.sub(r"^(?:\s*<br\s*/?>\s*)+", "", inner)
        inner = re.sub(r"(?:\s*<br\s*/?>\s*)+$", "", inner)
        if inner:
            out.append(f"<p>{inner}</p>")
        buf.clear()

    children = list(soup.contents)
    for i, child in enumerate(children):
        name = getattr(child, "name", None)
        if name is None:
            if str(child).strip():
                buf.append(str(child))
            continue

        classes = child.get("class") or []
        is_block = name in BLOCK_TAGS or (name == "a" and "lightbox" in classes)

        if name == "br":
            if skip_br:
                skip_br = False
                continue
            j = i + 1
            while j < len(children) and getattr(children[j], "name", None) is None and not str(children[j]).strip():
                j += 1
            if j < len(children) and getattr(children[j], "name", None) == "br":
                flush()
                skip_br = True
            else:
                buf.append("<br>")
            continue

        if is_block:
            flush()
            if name == "a" and "lightbox" in classes:
                caption = child.get("data-caption", "")
                cap = f"<figcaption>{html_escape(caption)}</figcaption>" if caption else ""
                out.append(f'<figure class="article-figure">{child}{cap}</figure>')
            else:
                out.append(str(child))
        else:
            buf.append(str(child))

    flush()
    return "\n".join(out)


def clean_body(body: BeautifulSoup | None, article: dict, video_posters: set[str],
               article_map: dict[str, str]) -> str:
    if body is None:
        return ""
    # copia isolata
    body_soup = BeautifulSoup(str(body), "lxml")
    node = body_soup.select_one('[itemprop="articleBody"]')
    if node is None:
        return ""

    title = article["title"]

    # Rimuove script, style e media non più gestiti
    for tag in node.find_all(["script", "style", "noscript", "embed", "object", "param",
                              "form", "input", "select", "textarea"]):
        tag.decompose()

    # Sostituisce gli iframe YouTube con un placeholder cliccabile
    for iframe in list(node.find_all("iframe")):
        src = iframe.get("src", "")
        m = YT_RE.search(src)
        if not m:
            iframe.decompose()
            continue
        vid = m.group(1)
        div = body_soup.new_tag("div")
        div["class"] = "video-embed"
        div["data-video-id"] = vid
        div["data-video-title"] = title
        button = body_soup.new_tag("button")
        button["class"] = "video-play"
        button["type"] = "button"
        button["aria-label"] = f"Riproduci il video: {title}"
        if vid in video_posters:
            img = body_soup.new_tag("img")
            img["src"] = f"/assets/img/video/{vid}.jpg"
            img["alt"] = f"Video: {title}"
            img["loading"] = "lazy"
            button.append(img)
        else:
            span = body_soup.new_tag("span")
            span["class"] = "video-unavailable"
            span.string = "Video non più disponibile"
            button.append(span)
        div.append(button)
        iframe.replace_with(div)

    # Pulisce gli attributi, conservando quelli necessari
    keep = {"href", "src", "alt", "title", "colspan", "rowspan", "data-original"}
    for tag in node.find_all(True):
        for attr in list(tag.attrs):
            if attr not in keep:
                del tag.attrs[attr]

    # Unwrap di span/font
    for tag in node.find_all(["span", "font"]):
        tag.unwrap()

    # Gestione link
    for a in list(node.find_all("a")):
        href = a.get("href", "")
        if not href or href.startswith("javascript") or href == "#":
            a.unwrap()
            continue
        if href.startswith("/?q="):
            q = urllib.parse.unquote(href.split("=", 1)[1])
            a["href"] = "/eventi.html?q=" + urllib.parse.quote_plus(q)
        elif href.startswith("https://www.progettorebecca.org/"):
            href = href.replace("https://www.progettorebecca.org", "")
            a["href"] = href
        if href in STATIC_OLD_URLS:
            a["href"] = STATIC_OLD_URLS[href]
        elif href.startswith("/n"):
            if href in article_map:
                a["href"] = article_map[href]
            else:
                a["href"] = "/eventi.html"
        elif href.startswith("/i") or href.startswith("/s"):
            a["href"] = STATIC_OLD_URLS.get(href, "/contatti.html")
        if a.get("href", "").startswith("http"):
            a["target"] = "_blank"
            a["rel"] = "noopener"

    # Rimuove anchor vuoti
    for a in list(node.find_all("a")):
        if not a.get_text(strip=True) and not a.find("img"):
            a.decompose()

    # Immagini: percorsi locali, alt, lazy, link lightbox
    for img in list(node.find_all("img")):
        src = img.get("data-original") or img.get("src") or ""
        m = IMAGE_RE.search(src)
        if not m:
            img.decompose()
            continue
        aid = m.group(1)
        num = m.group(2) or "1"
        local = f"/assets/img/news/{aid}/{num}.webp"
        alt = clean_text(img.get("alt", ""))
        if not alt or alt.lower().startswith("immagine"):
            alt = f"{title} — fotografia {num}"
        img["src"] = local
        img["alt"] = alt
        img["loading"] = "lazy"
        img["decoding"] = "async"
        img.attrs.pop("data-original", None)
        parent = img.parent
        if parent and parent.name == "a":
            parent["href"] = local
            parent["class"] = "lightbox"
            parent["data-caption"] = alt
            parent.attrs.pop("target", None)
            parent.attrs.pop("rel", None)
        else:
            a = body_soup.new_tag("a")
            a["href"] = local
            a["class"] = "lightbox"
            a["data-caption"] = alt
            img.wrap(a)

    # Normalizza spazi vuoti nel markup
    html = "".join(str(c) for c in node.contents)
    html = html.replace("&nbsp;", " ")
    html = re.sub(r"(<br\s*/?>\s*){3,}", "<br><br>", html)
    return paragraphize(html)


def video_embed_html(vid: str, title: str) -> str:
    poster = VIDEO_IMG / f"{vid}.jpg"
    safe_title = html_escape(title)
    if poster.exists():
        media = f'<img src="/assets/img/video/{vid}.jpg" alt="Video: {safe_title}" loading="lazy" decoding="async">'
        button = f'<button class="video-play" type="button" aria-label="Riproduci il video: {safe_title}">{media}</button>'
    else:
        button = (
            '<button class="video-play is-unavailable" type="button" disabled>'
            '<span class="video-unavailable">Video non più disponibile</span></button>'
        )
    return (
        f'<div class="video-embed" data-video-id="{vid}" data-video-title="{safe_title}">'
        f"{button}</div>"
    )


def parse_article(path: Path, article_map: dict[str, str], video_posters: set[str]) -> dict:
    soup = read_soup(path)
    aid, slug = article_parts(path.name)

    title_el = soup.select_one("h1.articletitle")
    title = clean_text(title_el.get_text(" ", strip=True)) if title_el else slug.replace("-", " ").title()

    sub_el = soup.select_one('h2[property="alternativeHeadline"]')
    subtitle = clean_text(sub_el.get_text(" ", strip=True)) if sub_el else ""
    if len(subtitle) < 2:
        subtitle = ""

    date_el = soup.select_one('[itemprop="datePublished"]')
    date_iso = date_el.get("content", "") if date_el else ""
    dt = parse_date(date_iso)

    author_el = soup.select_one('[itemprop="author"]')
    author = clean_text(author_el.get_text(" ", strip=True)) if author_el else ""
    if author.lower() in ("webmaster", "redazione"):
        author = "Redazione"

    body_el = soup.select_one('[itemprop="articleBody"].articlecontent')
    images = extract_images(body_el)
    videos = extract_videos(soup)
    tags = extract_tags(soup)

    body_text = clean_text(body_el.get_text(" ", strip=True)) if body_el else ""
    body_html = clean_body(body_el, {"title": title}, video_posters, article_map)
    for vid in videos:
        if f'data-video-id="{vid}"' not in body_html:
            body_html += video_embed_html(vid, title)
    if not body_html.strip():
        body_html = (
            '<p class="article-empty">Questa storia è conservata nel diario, '
            "ma il contenuto originale non è più disponibile.</p>"
        )

    excerpt = excerpt_from(body_text or subtitle or title)

    cover = f"/assets/img/news/{aid}/cover.webp"
    cover_id = aid

    art = {
        "id": aid,
        "slug": slug,
        "filename": path.name,
        "title": title,
        "subtitle": subtitle,
        "date_iso": date_iso,
        "date_label": date_label(dt),
        "year": dt.year if dt else None,
        "author": author,
        "tags": tags,
        "images": images,
        "videos": videos,
        "body_html": body_html,
        "text": body_text,
        "excerpt": excerpt,
        "cover": cover,
        "cover_id": cover_id,
        "url": f"/eventi/{aid}-{slug}.html",
        "old_url": f"/{path.name}",
    }
    return art


def rebuild_body(a: dict, video_posters: set[str], article_map: dict[str, str]) -> str:
    body_el = read_soup(SRC / a["filename"]).select_one('[itemprop="articleBody"].articlecontent')
    body_html = clean_body(body_el, {"title": a["title"]}, video_posters, article_map)
    for vid in a["videos"]:
        if f'data-video-id="{vid}"' not in body_html:
            body_html += video_embed_html(vid, a["title"])
    if not body_html.strip():
        body_html = (
            '<p class="article-empty">Questa storia è conservata nel diario, '
            "ma il contenuto originale non è più disponibile.</p>"
        )
    return body_html


def parse_all() -> list[dict]:
    files = sorted(SRC.glob("*.htm"))
    article_files = [p for p in files if p.name not in STATIC_FILES and p.name != GALLERY_FILE]

    # mappa vecchi URL -> nuovi URL
    article_map: dict[str, str] = {}
    for p in article_files:
        aid, slug = article_parts(p.name)
        article_map[f"/{p.name}"] = f"/eventi/{aid}-{slug}.html"

    # poster video disponibili (verranno scaricati dopo; per ora placeholder vuoto)
    video_posters: set[str] = set()

    articles = []
    for p in article_files:
        articles.append(parse_article(p, article_map, video_posters))
    articles.sort(key=lambda a: (a["date_iso"] or "", a["id"]), reverse=True)

    # secondo passaggio: body con poster noti
    video_ids = sorted({vid for a in articles for vid in a["videos"]})
    posters = {vid for vid in video_ids if (VIDEO_IMG / f"{vid}.jpg").exists()}
    for a in articles:
        a["body_html"] = rebuild_body(a, posters, article_map)

    return articles


def parse_static(filename: str) -> dict:
    path = SRC / filename
    soup = read_soup(path)
    title_el = soup.select_one("h1.articletitle")
    title = clean_text(title_el.get_text(" ", strip=True)) if title_el else ""
    body_el = soup.select_one('[itemprop="articleBody"].articlecontent')
    text = clean_text(body_el.get_text(" ", strip=True)) if body_el else ""
    body_html = clean_body(body_el, {"title": title}, set(), {})
    return {"title": title, "text": text, "body_html": body_html}


# ---------------------------------------------------------------------------
# Media
# ---------------------------------------------------------------------------
def ensure_dirs() -> None:
    for d in (ASSETS, IMG, NEWS_IMG, THUMBS, VIDEO_IMG, DATA, OUT_ARTICLES, CONTENT):
        d.mkdir(parents=True, exist_ok=True)


def request_bytes(url: str, timeout: int = 45) -> bytes | None:
    for attempt in range(3):
        try:
            r = requests.get(url, headers=UA, timeout=timeout)
            if r.status_code == 200 and r.content:
                return r.content
        except requests.RequestException:
            pass
        time.sleep(1 + attempt)
    return None


def save_image_variants(data: bytes, folder: Path, stem: str, max_full: int = 1500,
                        max_thumb: int = 620) -> tuple[int, int]:
    folder.mkdir(parents=True, exist_ok=True)
    img = Image.open(BytesIO(data))
    img = ImageOps.exif_transpose(img)
    if img.mode in ("RGBA", "LA", "P"):
        img = img.convert("RGBA")
        bg = Image.new("RGB", img.size, (255, 255, 255))
        bg.paste(img, mask=img.split()[-1])
        img = bg
    else:
        img = img.convert("RGB")

    w, h = img.size
    if w > max_full:
        nh = round(h * max_full / w)
        full = img.resize((max_full, nh), Image.LANCZOS)
    else:
        full = img

    jpg_path = folder / f"{stem}.jpg"
    webp_path = folder / f"{stem}.webp"
    thumb_path = THUMBS / f"{folder.name}-{stem}.webp"

    full.save(jpg_path, "JPEG", quality=85, optimize=True, progressive=True)
    full.save(webp_path, "WEBP", quality=82, method=6)

    tw, th = full.size
    if tw > max_thumb:
        th = round(th * max_thumb / tw)
        thumb = full.resize((max_thumb, th), Image.LANCZOS)
    else:
        thumb = full
    thumb.save(thumb_path, "WEBP", quality=78, method=6)

    return full.size


def download_article_image(aid: str, num: str) -> tuple[str, str, tuple[int, int] | None]:
    folder = NEWS_IMG / aid
    webp = folder / f"{num}.webp"
    thumb = THUMBS / f"{aid}-{num}.webp"
    jpg = folder / f"{num}.jpg"
    if webp.exists() and thumb.exists() and jpg.exists():
        try:
            with Image.open(webp) as im:
                return aid, num, im.size
        except Exception:
            pass
    url = f"{BASE_URL}/image.php?id={aid}&num={num}"
    data = request_bytes(url)
    if not data:
        return aid, num, None
    try:
        size = save_image_variants(data, folder, num)
        return aid, num, size
    except Exception as exc:
        print(f"  ! immagine {aid}/{num}: {exc}")
        return aid, num, None


def download_cover(aid: str) -> bool:
    folder = NEWS_IMG / aid
    webp = folder / "cover.webp"
    thumb = THUMBS / f"{aid}-cover.webp"
    jpg = folder / "cover.jpg"
    if webp.exists() and thumb.exists() and jpg.exists():
        return True
    data = request_bytes(f"{BASE_URL}/image-{aid}-640-480.jpg")
    if not data:
        return False
    try:
        save_image_variants(data, folder, "cover")
        return True
    except Exception as exc:
        print(f"  ! cover {aid}: {exc}")
        return False


def download_video_poster(vid: str) -> bool:
    dest = VIDEO_IMG / f"{vid}.jpg"
    if dest.exists() and dest.stat().st_size > 0:
        return True
    data = request_bytes(f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg")
    if not data:
        return False
    dest.write_bytes(data)
    return True


def fetch_video_meta(vids: list[str]) -> dict:
    meta = {}
    for vid in vids:
        try:
            r = requests.get(
                "https://www.youtube.com/oembed",
                params={"url": f"https://www.youtube.com/watch?v={vid}", "format": "json"},
                headers=UA,
                timeout=25,
            )
            if r.status_code == 200:
                j = r.json()
                meta[vid] = {
                    "id": vid,
                    "title": j.get("title", ""),
                    "author": j.get("author_name", ""),
                    "available": True,
                }
                continue
        except Exception:
            pass
        meta[vid] = {"id": vid, "title": "", "author": "", "available": False}
    return meta


def download_all_media(articles: list[dict]) -> None:
    ensure_dirs()

    # Logo e derivati
    logo_src = TMP / "logo.png"
    if logo_src.exists():
        shutil.copy2(logo_src, IMG / "logo.png")
        make_logo_derivatives(logo_src)

    # Cover
    covers = [a["id"] for a in articles]
    print(f"Cover: {len(covers)}")
    with ThreadPoolExecutor(max_workers=8) as ex:
        futs = {ex.submit(download_cover, aid): aid for aid in covers}
        for i, fut in enumerate(as_completed(futs), 1):
            fut.result()
            if i % 25 == 0:
                print(f"  cover {i}/{len(covers)}")

    # Immagini inline
    refs = []
    for a in articles:
        for im in a["images"]:
            refs.append((im["id"], im["num"]))
    # immagini delle pagine statiche (U.L.M.I. e gallery)
    for filename in list(STATIC_FILES) + [GALLERY_FILE]:
        path = SRC / filename
        if path.exists():
            body_el = read_soup(path).select_one('[itemprop="articleBody"].articlecontent')
            for im in extract_images(body_el):
                refs.append((im["id"], im["num"]))
    refs = list(dict.fromkeys(refs))
    print(f"Immagini inline: {len(refs)}")
    with ThreadPoolExecutor(max_workers=8) as ex:
        futs = {ex.submit(download_article_image, aid, num): (aid, num) for aid, num in refs}
        for i, fut in enumerate(as_completed(futs), 1):
            fut.result()
            if i % 40 == 0:
                print(f"  immagini {i}/{len(refs)}")

    # Video
    vids = sorted({vid for a in articles for vid in a["videos"]})
    # aggiunge i video della pagina Video
    video_page = SRC / "n53407-video.htm"
    if video_page.exists():
        vids += extract_videos(read_soup(video_page))
    vids = sorted(set(vids))
    print(f"Video: {len(vids)}")
    with ThreadPoolExecutor(max_workers=6) as ex:
        futs = {ex.submit(download_video_poster, vid): vid for vid in vids}
        for i, fut in enumerate(as_completed(futs), 1):
            fut.result()

    meta = fetch_video_meta(vids)
    (DATA / "videos.json").write_text(
        json.dumps(list(meta.values()), ensure_ascii=False, indent=2), encoding="utf-8"
    )


def make_logo_derivatives(logo_src: Path) -> None:
    logo = Image.open(logo_src).convert("RGBA")
    # ritaglio del ritratto circolare dal logo (960x327)
    crop = logo.crop((632, 8, 946, 322)).resize((420, 420), Image.LANCZOS)
    mask = Image.new("L", crop.size, 0)
    d = ImageDraw.Draw(mask)
    d.ellipse((4, 4, crop.width - 4, crop.height - 4), fill=255)
    crop.putalpha(mask)
    crop.save(IMG / "rebecca.png")

    # pulcino estratto dal logo (header + favicon)
    bg = (241, 186, 205)
    chick_crop = logo.convert("RGB").crop((576, 208, 618, 260))
    chick = Image.new("RGBA", chick_crop.size)
    src_px = chick_crop.load()
    out_px = chick.load()
    for yy in range(chick_crop.height):
        for xx in range(chick_crop.width):
            r, g, b = src_px[xx, yy]
            dist = ((r - bg[0]) ** 2 + (g - bg[1]) ** 2 + (b - bg[2]) ** 2) ** 0.5
            alpha = max(0.0, min(1.0, (dist - 12) / 26))
            out_px[xx, yy] = (r, g, b, int(round(alpha * 255)))
    chick.save(IMG / "brand-chick.png")

    # icone: pulcino su rosa
    pink = (214, 65, 122, 255)

    def make_icon(size: int, ratio: float = 0.72) -> Image.Image:
        canvas = Image.new("RGBA", (size, size), pink)
        h = max(1, int(round(size * ratio)))
        w = max(1, int(round(chick.width * h / chick.height)))
        resized = chick.resize((w, h), Image.LANCZOS)
        canvas.alpha_composite(resized, ((size - w) // 2, (size - h) // 2))
        return canvas

    make_icon(32).save(IMG / "favicon-32.png")
    make_icon(180).save(IMG / "apple-touch-icon.png")
    make_icon(192).save(IMG / "favicon-192.png")

    h = 48
    w = int(round(chick.width * h / chick.height))
    buf = BytesIO()
    chick.save(buf, format="PNG")
    b64 = base64.b64encode(buf.getvalue()).decode("ascii")
    fav = (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">\n'
        '<rect width="64" height="64" rx="14" fill="#D6417A"/>\n'
        f'<image x="{(64 - w) / 2:g}" y="{(64 - h) / 2:g}" width="{w}" height="{h}" '
        f'href="data:image/png;base64,{b64}"/>\n'
        "</svg>\n"
    )
    (IMG / "favicon.svg").write_text(fav, encoding="utf-8")

    # pattern decorativo leggerissimo
    pattern = '''<svg xmlns="http://www.w3.org/2000/svg" width="180" height="180" viewBox="0 0 180 180">
<g fill="#D6417A" opacity="0.045">
<path d="M40 52S28 45 28 36c0-4 3-7 7-7 2 0 4 1 5 3 1-2 3-3 5-3 4 0 7 3 7 7 0 9-12 16-12 16Z"/>
<path d="M130 142s-12-7-12-16c0-4 3-7 7-7 2 0 4 1 5 3 1-2 3-3 5-3 4 0 7 3 7 7 0 9-12 16-12 16Z"/>
<path d="M150 40s-8-5-8-11c0-3 2-5 5-5 1 0 3 1 3 2 0-1 2-2 3-2 3 0 5 2 5 5 0 6-8 11-8 11Z"/>
</g></svg>'''
    (IMG / "pattern.svg").write_text(pattern, encoding="utf-8")


# ---------------------------------------------------------------------------
# HTML helpers
# ---------------------------------------------------------------------------
HEART_SVG = '''<svg viewBox="0 0 24 24" width="22" height="22" fill="currentColor" aria-hidden="true"><path d="M12 21.35 10.55 20C5.4 15.36 2 12.28 2 8.5 2 5.42 4.42 3 7.5 3c1.74 0 3.41.81 4.5 2.09C13.09 3.81 14.76 3 16.5 3 19.58 3 22 5.42 22 8.5c0 3.78-3.4 6.86-8.55 11.54L12 21.35Z"/></svg>'''
CHICK_IMG = '''<img src="/assets/img/brand-chick.png" alt="" width="19" height="24">'''
ARROW_UP_SVG = '''<svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 19V5"/><path d="m5 12 7-7 7 7"/></svg>'''
ARROW_LEFT_SVG = '''<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M19 12H5"/><path d="m12 19-7-7 7-7"/></svg>'''
PLAY_SVG = '''<svg viewBox="0 0 24 24" width="26" height="26" fill="currentColor" aria-hidden="true"><path d="M8 5.14v13.72c0 .8.87 1.3 1.55.9l10.8-6.86a1.05 1.05 0 0 0 0-1.8L9.55 4.24A1.05 1.05 0 0 0 8 5.14Z"/></svg>'''

NAV_ITEMS = [
    ("/", "Home", "home"),
    ("/chi-siamo.html", "Chi siamo", "chi-siamo"),
    ("/progetti.html", "Progetti", "progetti"),
    ("/eventi.html", "Diario", "eventi"),
    ("/gallery.html", "Gallery", "gallery"),
    ("/contatti.html", "Contatti", "contatti"),
]


def nav_html(active: str) -> str:
    parts = []
    for href, label, key in NAV_ITEMS:
        cls = "nav-link is-active" if key == active else "nav-link"
        aria = ' aria-current="page"' if key == active else ""
        parts.append(f'<a class="{cls}" href="{href}"{aria}>{label}</a>')
    cls = "btn btn--primary btn--sm nav-donate" if active == "donare" else "btn btn--primary btn--sm nav-donate"
    parts.append(f'<a class="{cls}" href="/donare.html">Dona</a>')
    return "\n        ".join(parts)


def base_page(*, title: str, description: str, canonical: str, content: str,
              active: str = "", body_class: str = "", og_type: str = "website",
              og_image: str = "", og_title: str = "", scripts: str = "") -> str:
    tpl = Template((TEMPLATES / "base.html").read_text(encoding="utf-8"))
    return tpl.substitute(
        title=title,
        description=description,
        canonical=canonical,
        og_type=og_type,
        og_title=og_title or title,
        og_image=og_image or f"{SITE_URL}/assets/img/logo.png",
        body_class=body_class,
        content=content,
        nav=nav_html(active),
        heart_svg=HEART_SVG,
        chick_img=CHICK_IMG,
        arrow_up_svg=ARROW_UP_SVG,
        year=datetime.now().year,
        head_extra="",
        scripts=scripts,
    )


def card_html(a: dict, *, show_excerpt: bool = True, extra_class: str = "") -> str:
    cover = a.get("cover") or ""
    if cover.endswith(".webp"):
        thumb = cover.replace("/assets/img/news/", "/assets/img/thumbs/").replace(".webp", ".webp")
        thumb = f"/assets/img/thumbs/{Path(cover).parent.name}-{Path(cover).stem}.webp"
    else:
        thumb = cover
    img = ""
    if thumb:
        img = f'<img src="{thumb}" alt="{html_escape(a["title"])}" loading="lazy" decoding="async">'
    else:
        img = f'<span class="card-placeholder" aria-hidden="true">{HEART_SVG}</span>'
    tags = "".join(f'<span class="tag">{html_escape(t)}</span>' for t in a["tags"][:2])
    excerpt = f'<p class="card-excerpt">{html_escape(a["excerpt"])}</p>' if show_excerpt else ""
    return f'''<article class="news-card {extra_class}" data-year="{a["year"] or ""}" data-text="{html_escape((a["title"] + " " + a["excerpt"] + " " + " ".join(a["tags"])).lower())}">
  <a class="card-link" href="{a["url"]}" aria-label="{html_escape(a["title"])}">
    <div class="card-media">{img}</div>
    <div class="card-body">
      <p class="card-date">{html_escape(a["date_label"])}</p>
      <h3 class="card-title">{html_escape(a["title"])}</h3>
      {excerpt}
      <span class="card-more">Leggi la storia <span aria-hidden="true">→</span></span>
    </div>
  </a>
</article>'''


def html_escape(text: str) -> str:
    return (text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace('"', "&quot;"))


def photo_html(img: dict, article: dict) -> str:
    aid = img["id"]
    num = img["num"]
    thumb = f"/assets/img/thumbs/{aid}-{num}.webp"
    full = f"/assets/img/news/{aid}/{num}.webp"
    alt = img.get("alt") or f"{article['title']} — fotografia {num}"
    if alt.lower().startswith("immagine"):
        alt = f"{article['title']} — fotografia {num}"
    return f'''<a class="gallery-item lightbox" href="{full}" data-caption="{html_escape(alt)}" aria-label="Ingrandisci: {html_escape(alt)}">
  <img src="{thumb}" alt="{html_escape(alt)}" loading="lazy" decoding="async">
</a>'''


def video_card_html(v: dict, poster: bool) -> str:
    vid = v["id"]
    title = v.get("title") or "Video del Progetto Rebecca"
    author = v.get("author") or ""
    available = v.get("available", True) and poster
    if available:
        media = f'<img src="/assets/img/video/{vid}.jpg" alt="{html_escape(title)}" loading="lazy" decoding="async">'
        cls = "video-card"
        action = f'<span class="video-card-play" aria-hidden="true">{PLAY_SVG}</span>'
        attrs = f'data-video-id="{vid}" data-video-title="{html_escape(title)}"'
    else:
        media = '<span class="video-card-empty">Video non più disponibile</span>'
        cls = "video-card is-unavailable"
        action = ""
        attrs = ""
    meta = f'<span class="video-card-author">{html_escape(author)}</span>' if author else ""
    return f'''<button class="{cls}" type="button" {attrs} aria-label="{html_escape(title)}">
  <span class="video-card-media">{media}{action}</span>
  <span class="video-card-body">
    <span class="video-card-title">{html_escape(title)}</span>
    {meta}
  </span>
</button>'''


# ---------------------------------------------------------------------------
# Generazione pagine
# ---------------------------------------------------------------------------
def build_article_page(a: dict, all_articles: list[dict], video_meta: dict) -> str:
    tpl = Template((TEMPLATES / "article.html").read_text(encoding="utf-8"))

    related = [x for x in all_articles if x["id"] != a["id"]]
    # stessa anno + tag in comune, poi data vicina
    def score(x):
        s = 0
        if x["year"] == a["year"]:
            s += 3
        s += len(set(x["tags"]) & set(a["tags"])) * 2
        return s
    related.sort(key=lambda x: (score(x), x["date_iso"] or ""), reverse=True)
    related_html = "".join(
        f'<li><a href="{x["url"]}"><span>{html_escape(x["date_label"])}</span>{html_escape(x["title"])}</a></li>'
        for x in related[:4]
    )

    subtitle_html = f'<p class="article-subtitle">{html_escape(a["subtitle"])}</p>' if a["subtitle"] else ""
    author_html = f'<p class="article-byline">di {html_escape(a["author"])}</p>' if a["author"] else ""
    init = a["title"][:48].rstrip() + ("…" if len(a["title"]) > 48 else "")
    tags_html = ""
    if a["tags"]:
        tags_html = '<div class="container article-tags"><span>Argomenti:</span>' + "".join(
            f'<a href="/eventi.html?q={urllib.parse.quote_plus(t)}">{html_escape(t)}</a>' for t in a["tags"]
        ) + "</div>"

    encoded_url = urllib.parse.quote(SITE_URL + a["url"], safe="")
    encoded_title = urllib.parse.quote(a["title"], safe="")

    content = tpl.substitute(
        short_title=html_escape(init),
        date_label=html_escape(a["date_label"]),
        title=html_escape(a["title"]),
        subtitle_html=subtitle_html,
        author_html=author_html,
        body=a["body_html"],
        related=related_html,
        tags_html=tags_html,
        heart_svg=HEART_SVG,
        arrow_left_svg=ARROW_LEFT_SVG,
        encoded_url=encoded_url,
        encoded_title=encoded_title,
    )

    description = a["excerpt"] or f"{a['title']} — archivio del Progetto Rebecca."
    og_image = f"{SITE_URL}{a['cover']}" if a["cover"] else f"{SITE_URL}/assets/img/logo.png"

    jsonld = {
        "@context": "https://schema.org",
        "@type": "Article",
        "headline": a["title"],
        "datePublished": a["date_iso"],
        "author": {"@type": "Person", "name": a["author"] or "Progetto Rebecca"},
        "publisher": {"@type": "Organization", "name": "Progetto Rebecca"},
        "image": og_image,
        "mainEntityOfPage": SITE_URL + a["url"],
        "description": description,
    }
    scripts = f'<script type="application/ld+json">{json.dumps(jsonld, ensure_ascii=False)}</script>'

    return base_page(
        title=f"{a['title']} | Progetto Rebecca",
        description=description,
        canonical=SITE_URL + a["url"],
        content=content,
        active="eventi",
        body_class="page-article",
        og_type="article",
        og_image=og_image,
        og_title=a["title"],
        scripts=scripts,
    )


def build_archive_page(articles: list[dict]) -> str:
    tpl = Template((TEMPLATES / "archive.html").read_text(encoding="utf-8"))
    years = sorted({a["year"] for a in articles if a["year"]}, reverse=True)
    buttons = ['<button class="filter-btn is-active" type="button" data-year="all">Tutti</button>']
    for y in years:
        buttons.append(f'<button class="filter-btn" type="button" data-year="{y}">{y}</button>')
    cards = "\n".join(card_html(a) for a in articles)
    content = tpl.substitute(
        year_buttons="\n        ".join(buttons),
        count_label=f"{len(articles)} storie dal 2013 al 2018",
        cards=cards,
    )
    return base_page(
        title="Il diario | Progetto Rebecca",
        description="Tutte le storie, gli eventi e le iniziative del Progetto Rebecca dal 2013 al 2018.",
        canonical=f"{SITE_URL}/eventi.html",
        content=content,
        active="eventi",
        body_class="page-archive",
        scripts='<script src="/assets/js/archive.js" defer></script>',
    )


def evenly_sample(items: list[dict], limit: int) -> list[dict]:
    if len(items) <= limit:
        return items
    if limit <= 1:
        return items[:limit]
    step = (len(items) - 1) / (limit - 1)
    picked = []
    for i in range(limit):
        item = items[round(i * step)]
        if not picked or item is not picked[-1]:
            picked.append(item)
    return picked


def collect_photos(articles: list[dict], limit: int = GALLERY_LIMIT) -> list[dict]:
    """Selezione curata: una foto rappresentativa per articolo.

    Tutte le immagini restano nelle pagine dei singoli articoli; la gallery
    mostra una scelta distribuita nel tempo per non diventare interminabile.
    """
    photos = []
    seen = set()

    def add(img: dict, article: dict) -> None:
        key = (img["id"], img["num"])
        if key in seen:
            return
        if not (NEWS_IMG / img["id"] / f"{img['num']}.webp").exists():
            return
        seen.add(key)
        photos.append({
            "id": img["id"],
            "num": img["num"],
            "alt": img.get("alt", ""),
            "article": article,
        })

    for a in articles:
        candidates = [
            im for im in a["images"]
            if (NEWS_IMG / im["id"] / f"{im['num']}.webp").exists()
        ]
        if not candidates:
            continue
        chosen = next(
            (im for im in candidates
             if image_is_landscape(NEWS_IMG / im["id"] / f"{im['num']}.webp")),
            candidates[0],
        )
        add(chosen, a)

    if len(photos) < limit:
        for a in articles:
            if a["images"] or not a["cover_id"]:
                continue
            add({"id": a["cover_id"], "num": "cover", "alt": a["title"]}, a)

    return evenly_sample(photos, limit)


def build_gallery_page(articles: list[dict], video_meta: list[dict]) -> str:
    tpl = Template((TEMPLATES / "gallery.html").read_text(encoding="utf-8"))

    photos_html = [photo_html(p, p["article"]) for p in collect_photos(articles)]
    posters = {p.stem for p in VIDEO_IMG.glob("*.jpg")}
    videos_html = "\n".join(video_card_html(v, v["id"] in posters) for v in video_meta)

    content = tpl.substitute(photos="\n".join(photos_html), videos=videos_html)
    return base_page(
        title="Gallery | Progetto Rebecca",
        description="Fotografie e video storici del Progetto Rebecca: gli anni del progetto, il Kindergarten in India, il Parco Giochi e le iniziative nel VCO.",
        canonical=f"{SITE_URL}/gallery.html",
        content=content,
        active="gallery",
        body_class="page-gallery",
    )


# ---------------------------------------------------------------------------
# Pagine istituzionali
# ---------------------------------------------------------------------------
def page_hero(eyebrow: str, title: str, lead: str, extra: str = "", modifier: str = "") -> str:
    return f'''<header class="page-hero {modifier}">
  <div class="container page-hero-inner">
    <p class="eyebrow">{eyebrow}</p>
    <h1>{title}</h1>
    <p class="page-lead">{lead}</p>
    {extra}
  </div>
</header>'''


def project_card(eyebrow: str, title: str, text: str, image: str, href: str) -> str:
    return f'''<article class="project-card">
  <a class="project-card-media" href="{href}" aria-label="{html_escape(title)}">
    <img src="{image}" alt="{html_escape(title)}" loading="lazy" decoding="async">
  </a>
  <div class="project-card-body">
    <p class="eyebrow">{eyebrow}</p>
    <h3><a href="{href}">{title}</a></h3>
    <p>{text}</p>
    <a class="project-link" href="{href}">Scopri il progetto <span aria-hidden="true">→</span></a>
  </div>
</article>'''


def build_home_page(articles: list[dict], video_meta: list[dict]) -> str:
    latest = articles[:6]
    latest_html = "\n".join(card_html(a) for a in latest)

    preview = []
    seen_articles = set()
    for p in collect_photos(articles):
        if p["article"]["id"] in seen_articles:
            continue
        seen_articles.add(p["article"]["id"])
        preview.append(p)
        if len(preview) == 6:
            break
    preview_html = "\n".join(photo_html(p, p["article"]) for p in preview)

    projects = "\n".join([
        project_card(
            "India · 2013–2014", "Kindergarten Rebecca",
            "Una scuola materna a Ranchi, in India, intitolata a Rebecca. Un sogno iniziato con le Suore Marianiste e realizzato grazie a una raccolta fondi lunga un anno.",
            "/assets/img/news/127107/cover.webp", "/progetti.html#kindergarten",
        ),
        project_card(
            "VCO · 2015–2016", "Le scuole del territorio",
            "Materiali didattici, sussidi, corsi di inglese e piccoli interventi concreti per le scuole dell'infanzia e primarie del Verbano Cusio Ossola.",
            "/assets/img/news/566128/cover.webp", "/progetti.html#scuole",
        ),
        project_card(
            "VCO · 2015–2016", "Progetto Rebecca per lo Sport",
            "Un defibrillatore con piastre pediatriche, sostegno alle società sportive e attenzione alla sicurezza dei piccoli atleti.",
            "/assets/img/news/801020/cover.webp", "/progetti.html#sport",
        ),
        project_card(
            "Verbania · 2017–2018", "Il Parco Giochi di Rebecca",
            "Un parco inclusivo a Villa Olimpia, voluto dal Corso di Corsa Verbania e scelto dai cittadini nel Bilancio Partecipato 2017.",
            "/assets/img/news/1300852/cover.webp", "/progetti.html#parco",
        ),
    ])

    content = f'''<header class="home-hero">
  <div class="container hero-grid">
    <div class="hero-copy">
      <p class="eyebrow">Dal 2 ottobre 2012 · con il sorriso di Rebecca</p>
      <h1>Il progetto dei bimbi</h1>
      <p class="hero-lead">Una storia nata da un grande dolore e diventata aiuto concreto: una scuola in India, le scuole del VCO, lo sport e un parco giochi inclusivo. Perché il sorriso di Rebecca possa rivivere nel sorriso degli altri bambini.</p>
      <div class="hero-actions">
        <a class="btn btn--primary" href="/chi-siamo.html">Scopri la storia</a>
        <a class="btn btn--ghost" href="/eventi.html">Leggi il diario <span aria-hidden="true">→</span></a>
      </div>
      <dl class="hero-stats">
        <div><dt class="stat-number">{len(articles)}</dt><dd class="stat-label">storie nel diario</dd></div>
        <div><dt class="stat-number">{len(video_meta)}</dt><dd class="stat-label">video conservati</dd></div>
        <div><dt class="stat-number">37.365 €</dt><dd class="stat-label">per il Kindergarten</dd></div>
        <div><dt class="stat-number">1</dt><dd class="stat-label">parco inclusivo</dd></div>
      </dl>
    </div>
    <div class="hero-visual" aria-hidden="true">
      <span class="hero-ring hero-ring--one"></span>
      <span class="hero-ring hero-ring--two"></span>
      <span class="hero-photo">
        <img src="/assets/img/rebecca.png" alt="Ritratto di Rebecca" width="420" height="420">
      </span>
      <p class="hero-note">…GRAZIE CON REBECCA<br>NEL CUORE…</p>
      <span class="hero-spark hero-spark--one"></span>
      <span class="hero-spark hero-spark--two"></span>
    </div>
  </div>
</header>

<section class="section intro-section">
  <div class="container intro-grid">
    <div class="intro-copy">
      <p class="eyebrow">Chi siamo</p>
      <h2>Una storia nata dall'amore, diventata aiuto concreto</h2>
      <p>Rebecca diventa un angelo il 2 ottobre 2012, giorno degli Angeli Custodi. Dal dolore e dall'amore del papà Giovanni, di mamma Fabrizia e del fratello Remigio nasce il “Progetto Rebecca il progetto dei bimbi”, per ricordare Rebecca e dare un senso a una tragedia senza un perché.</p>
      <p>Dall'incontro con le Suore Marianiste nasce il Kindergarten Rebecca a Ranchi, in India. Poi l'associazione, costituita il 30 dicembre 2012 e iscritta ufficialmente il 25 marzo 2014, ha continuato a occuparsi del benessere dei bambini in Italia, in India e nel VCO.</p>
      <a class="link-arrow" href="/chi-siamo.html">Leggi tutta la storia <span aria-hidden="true">→</span></a>
    </div>
    <ol class="timeline-mini">
      <li><span class="time">2 ottobre 2012</span><span>Rebecca diventa un angelo.</span></li>
      <li><span class="time">30 dicembre 2012</span><span>Nasce il Progetto Rebecca.</span></li>
      <li><span class="time">24 giugno 2013</span><span>Nasce il Fondo Memoriale.</span></li>
      <li><span class="time">31 marzo 2014</span><span>Il Kindergarten è realtà a Ranchi.</span></li>
      <li><span class="time">28 luglio 2018</span><span>Inaugurato il Parco Giochi.</span></li>
    </ol>
  </div>
</section>

<section class="section section--blush" id="progetti">
  <div class="container">
    <div class="section-heading section-heading--split">
      <div>
        <p class="eyebrow">I progetti</p>
        <h2>Quattro modi per continuare ad aiutare</h2>
      </div>
      <a class="link-arrow" href="/progetti.html">Tutti i progetti <span aria-hidden="true">→</span></a>
    </div>
    <div class="project-grid">
      {projects}
    </div>
  </div>
</section>

<section class="section" id="diario">
  <div class="container">
    <div class="section-heading section-heading--split">
      <div>
        <p class="eyebrow">Il diario</p>
        <h2>Le ultime storie</h2>
      </div>
      <a class="link-arrow" href="/eventi.html">Tutte le storie <span aria-hidden="true">→</span></a>
    </div>
    <div class="news-grid">
      {latest_html}
    </div>
  </div>
</section>

<section class="quote-band">
  <div class="container quote-inner">
    <span class="quote-mark" aria-hidden="true">“</span>
    <blockquote>
      <p>Quando le persone si mettono insieme qualcosa accade; quando persone buone si mettono insieme, qualcosa di buono accade; quando persone come voi e noi si mettono insieme qualcosa di meraviglioso può accadere.</p>
      <footer>Suor Colomba Yu · India District Superior</footer>
    </blockquote>
  </div>
</section>

<section class="section" id="gallery-preview">
  <div class="container">
    <div class="section-heading section-heading--split">
      <div>
        <p class="eyebrow">La memoria in immagini</p>
        <h2>Gli anni del progetto</h2>
      </div>
      <a class="link-arrow" href="/gallery.html">Vai alla gallery <span aria-hidden="true">→</span></a>
    </div>
    <div class="gallery-grid gallery-grid--preview">
      {preview_html}
    </div>
  </div>
</section>

<section class="donate-band" id="dona">
  <div class="container donate-band-inner">
    <div class="donate-band-copy">
      <p class="eyebrow">Aiutaci a continuare</p>
      <h2>Il modo più bello di ricordare Rebecca è continuare ad aiutare i bambini.</h2>
      <p>Una donazione, un progetto, un po' del tuo tempo: ogni contributo diventa un sorriso in più.</p>
    </div>
    <div class="donate-band-actions">
      <a class="btn btn--light" href="/donare.html">Come donare</a>
      <a class="btn btn--outline-light" href="/contatti.html">Contattaci</a>
    </div>
  </div>
</section>'''

    return base_page(
        title="Progetto Rebecca — il progetto dei bimbi",
        description="Dal 2012 il Progetto Rebecca aiuta i bambini in India, nel VCO e in Italia: il Kindergarten a Ranchi, le scuole del territorio, lo sport e il Parco Giochi di Rebecca.",
        canonical=f"{SITE_URL}/",
        content=content,
        active="home",
        body_class="page-home",
    )


def build_chi_siamo_page() -> str:
    content = page_hero(
        "La nostra storia",
        "Un sorriso che continua",
        "Rebecca Bonaffini è diventata un angelo il 2 ottobre 2012. Da quel giorno la sua famiglia ha scelto di trasformare il dolore in aiuto concreto per i bambini.",
    )

    content += '''
<section class="section">
  <div class="container story-grid">
    <div class="story-copy">
      <p class="eyebrow">Le origini</p>
      <h2>Dal dolore nasce il progetto</h2>
      <p>Rebecca diventa un Angelo il 2 ottobre 2012, giorno degli Angeli Custodi, a causa di un virus ancora oggi sconosciuto. Da questo forte dolore e dall'amore del papà Giovanni, di mamma Fabrizia e del fratello Remigio nasce il “PROGETTO REBECCA il progetto dei bimbi”, al fine di ricordare e dare un senso a questa tragedia avvenuta senza un motivo e un perché.</p>
      <p>Tutto è cominciato dall'incontro della famiglia di Rebecca con le Suore Marianiste. Si voleva mantenere vivo il ricordo della piccola attraverso opere di aiuto ai bambini più poveri. Sono nate così una serie di iniziative per raccogliere fondi a favore della struttura di accoglienza per bambini a Ranchi, in India: la struttura si chiamerà “KINDERGARTEN REBECCA”.</p>
      <p>Terminato questo progetto, l'Associazione — nata il 30 dicembre 2012, il giorno del compleanno di Rebecca, e iscritta ufficialmente il 25 marzo 2014 depositando statuto e atto costitutivo — ha come obiettivo il benessere dei bambini in Italia, in India e nel VCO.</p>
      <p>Come diceva una famosa canzone, “siamo piccoli ma cresceremo”: una volta finito il progetto in India, l'Associazione si occuperà di progetti nel VCO a favore di bimbi da 1 a 10 anni, attraverso il sostegno ad asili nido, scuole materne, scuole elementari e parchi gioco, perché il sorriso di Rebecca possa rivivere nel sorriso degli altri bimbi, anche nei meno fortunati.</p>
      <p>Il 24 giugno 2013 è stato costituito presso la Fondazione Comunitaria il Fondo Memoriale “Progetto Rebecca”, con l'obiettivo di essere sempre informati sulle richieste di enti e strutture legate ai bisogni infantili, al fine di poter partecipare, in maniera efficace, al soddisfacimento delle suddette richieste.</p>
      <p>Molti sono i focolai di solidarietà che da questa iniziativa stanno sorgendo e rispondendo positivamente.</p>
      <p class="signature">Mamma Fabrizia, Papà Giovanni, sr. M. Michela<br><span>Fondatori del Fondo Progetto Rebecca il progetto dei bimbi</span></p>
    </div>
    <aside class="story-aside">
      <div class="founder-card">
        <span class="founder-heart" aria-hidden="true">''' + HEART_SVG + '''</span>
        <p class="eyebrow">Il progetto dei bimbi</p>
        <h3>“…GRAZIE CON REBECCA NEL CUORE…”</h3>
        <p>Una promessa nata in famiglia e diventata una rete di solidarietà che coinvolge l'India, il VCO e tante persone che non hanno mai conosciuto Rebecca.</p>
        <a class="btn btn--primary btn--sm" href="/progetti.html">I progetti</a>
      </div>
      <div class="story-facts">
        <div><strong>2 ottobre 2012</strong><span>Rebecca diventa un angelo</span></div>
        <div><strong>30 dicembre 2012</strong><span>Nasce l'Associazione</span></div>
        <div><strong>25 marzo 2014</strong><span>Iscrizione ufficiale</span></div>
        <div><strong>24 giugno 2013</strong><span>Fondo Memoriale</span></div>
      </div>
    </aside>
  </div>
</section>

<section class="section section--blush" id="storia">
  <div class="container">
    <div class="section-heading section-heading--left">
      <p class="eyebrow">Le tappe</p>
      <h2>La storia, anno per anno</h2>
    </div>
    <ol class="timeline">
      <li class="timeline-item">
        <span class="timeline-date">2 ottobre 2012</span>
        <div class="timeline-content"><h3>Rebecca diventa un angelo</h3><p>Nel giorno degli Angeli Custodi, Rebecca si spegne a causa di un virus ancora oggi sconosciuto.</p></div>
      </li>
      <li class="timeline-item">
        <span class="timeline-date">30 dicembre 2012</span>
        <div class="timeline-content"><h3>Nasce il Progetto Rebecca</h3><p>Il giorno del compleanno di Rebecca nasce l'associazione “il progetto dei bimbi”.</p></div>
      </li>
      <li class="timeline-item">
        <span class="timeline-date">24 giugno 2013</span>
        <div class="timeline-content"><h3>Il Fondo Memoriale</h3><p>Presso la Fondazione Comunitaria nasce il Fondo Memoriale “Progetto Rebecca”.</p></div>
      </li>
      <li class="timeline-item">
        <span class="timeline-date">25 marzo 2014</span>
        <div class="timeline-content"><h3>L'iscrizione ufficiale</h3><p>L'associazione deposita statuto e atto costitutivo e viene iscritta ufficialmente.</p></div>
      </li>
      <li class="timeline-item">
        <span class="timeline-date">31 marzo 2014</span>
        <div class="timeline-content"><h3>Il Kindergarten è realtà</h3><p>Da Ranchi arriva la foto dei bambini che escono dal Kindergarten Rebecca: il sogno è diventato realtà.</p></div>
      </li>
      <li class="timeline-item">
        <span class="timeline-date">2015–2016</span>
        <div class="timeline-content"><h3>Le scuole del VCO e lo sport</h3><p>Materiali didattici, corsi di inglese, sostegno alle scuole e un defibrillatore per i piccoli calciatori.</p></div>
      </li>
      <li class="timeline-item">
        <span class="timeline-date">28 luglio 2018</span>
        <div class="timeline-content"><h3>Il Parco Giochi di Rebecca</h3><p>A Villa Olimpia viene inaugurato un parco giochi inclusivo dedicato a Rebecca.</p></div>
      </li>
    </ol>
  </div>
</section>

<section class="section">
  <div class="container">
    <div class="section-heading section-heading--left">
      <p class="eyebrow">I valori</p>
      <h2>Quello che ci guida</h2>
    </div>
    <div class="values-grid">
      <article class="value-card"><span class="value-number">01</span><h3>Ricordo</h3><p>Tenere vivo il sorriso di Rebecca attraverso opere concrete, non parole.</p></article>
      <article class="value-card"><span class="value-number">02</span><h3>Educazione</h3><p>Dare ai bambini gli strumenti per crescere: scuola, materiali, cure, sicurezza.</p></article>
      <article class="value-card"><span class="value-number">03</span><h3>Comunità</h3><p>Mettere insieme famiglie, scuole, associazioni e persone che credono nel progetto.</p></article>
      <article class="value-card"><span class="value-number">04</span><h3>Trasparenza</h3><p>Raccontare ogni passo, ogni donazione e ogni risultato raggiunto.</p></article>
    </div>
  </div>
</section>'''

    return base_page(
        title="Chi siamo | Progetto Rebecca",
        description="La storia del Progetto Rebecca: Rebecca Bonaffini, la famiglia, le Suore Marianiste e i progetti per i bambini in India, nel VCO e in Italia.",
        canonical=f"{SITE_URL}/chi-siamo.html",
        content=content,
        active="chi-siamo",
        body_class="page-chi-siamo",
    )


def build_progetti_page(articles: list[dict]) -> str:
    content = page_hero(
        "I progetti",
        "Quattro modi per continuare ad aiutare",
        "Dal Kindergarten in India alle scuole del VCO, dallo sport al Parco Giochi di Rebecca: ogni progetto è nato da un bisogno concreto e da una rete di persone che si sono messe insieme.",
    )

    content += '''
<section class="section">
  <div class="container">
    <div class="project-feature" id="kindergarten">
      <div class="project-feature-media">
        <img src="/assets/img/news/127107/cover.webp" alt="Il Kindergarten Rebecca a Ranchi, India" loading="lazy" decoding="async">
      </div>
      <div class="project-feature-body">
        <p class="eyebrow">India · 2013–2014</p>
        <h2>Kindergarten Rebecca</h2>
        <p>Il primo grande progetto: una scuola materna a Ranchi, in India, intitolata a Rebecca. Nasce dall'incontro della famiglia con le Suore Marianiste e dalla volontà di mantenere vivo il ricordo della piccola attraverso opere di aiuto ai bambini più poveri.</p>
        <p>La raccolta fondi ha permesso di finanziare il Kindergarten in tre tranche da 10.000 euro. All'8 aprile 2014 il progetto risultava realizzato con 37.365 euro.</p>
        <div class="fact-row">
          <div class="fact"><strong>37.365 €</strong><span>raccolti per il Kindergarten</span></div>
          <div class="fact"><strong>3 × 10.000 €</strong><span>le tranche del finanziamento</span></div>
          <div class="fact"><strong>31 marzo 2014</strong><span>la foto dei primi bambini</span></div>
        </div>
        <ul class="project-links">
          <li><a href="/eventi/127107-ecco-il-kindergarten-rebecca.html">“Ecco il Kindergarten Rebecca” <span>31 marzo 2014</span></a></li>
          <li><a href="/eventi/70068-effettuato-bonifico-di-diecimila-euro-come-seconda-trance-per-il-kindergarten-rebecca-in-india.html">La seconda tranche <span>8 settembre 2013</span></a></li>
          <li><a href="/eventi/115795-terza-trance-di-diecimila-euro-per-il-finanziamento-al-kindergarten-rebecca.html">La terza tranche <span>27 febbraio 2014</span></a></li>
        </ul>
      </div>
    </div>

    <div class="project-feature project-feature--reverse" id="scuole">
      <div class="project-feature-media">
        <img src="/assets/img/news/566128/cover.webp" alt="Donazione alla scuola di Feriolo" loading="lazy" decoding="async">
      </div>
      <div class="project-feature-body">
        <p class="eyebrow">VCO · 2015–2016</p>
        <h2>Le scuole del territorio</h2>
        <p>Una scuola alla volta: materiali didattici, sussidi, corsi di inglese, attrezzature e piccoli interventi che migliorano la giornata dei bambini. Le scuole dell'infanzia e primarie del Verbano Cusio Ossola sono state protagoniste di decine di storie del diario.</p>
        <p>Da Feriolo a Cannero, da Villadossola a Omegna, da Mergozzo a Pogno: ogni donazione è nata da un evento, da una famiglia o da un'associazione che ha scelto di esserci.</p>
        <ul class="project-links">
          <li><a href="/eventi/566128-donazione-alla-scuola-elementare-e-materna-di-feriolo-grazie-all-evento-a-pesca-con-rebecca.html">Feriolo: stampante, materassini e corso di inglese <span>26 febbraio 2016</span></a></li>
          <li><a href="/eventi/591031-donazione-di-materiale-didattico-alla-scuola-materna-di-cannero-grazie-a-fabio-e-sabrina.html">Cannero: materiale didattico <span>22 marzo 2016</span></a></li>
          <li><a href="/eventi/561915-il-sorriso-di-rebecca-rivive-anche-nei-bimbi-della-scuola-materna-don-bosco-di-villadossola.html">Villadossola: il sorriso di Rebecca <span>22 febbraio 2016</span></a></li>
        </ul>
      </div>
    </div>

    <div class="project-feature" id="sport">
      <div class="project-feature-media">
        <img src="/assets/img/news/801020/cover.webp" alt="Progetto Rebecca per lo Sport" loading="lazy" decoding="async">
      </div>
      <div class="project-feature-body">
        <p class="eyebrow">VCO · 2015–2016</p>
        <h2>Progetto Rebecca per lo Sport</h2>
        <p>Lo sport come spazio di crescita, sicurezza e comunità. Il progetto ha sostenuto le società sportive del territorio e ha donato un defibrillatore completo di piastre pediatriche al settore giovanile dell'Asd San Pietro Gravellona.</p>
        <p>Un gesto concreto, nato dalla collaborazione con il cantante della Solidarietà Salvatore Ranieri e con Interfarmaci Italia.</p>
        <ul class="project-links">
          <li><a href="/eventi/801020-progetto-rebecca-per-lo-sport-donato-un-defibrillatore-ai-piccoli-calciatori.html">Un defibrillatore per i piccoli calciatori <span>7 ottobre 2016</span></a></li>
          <li><a href="/eventi/660479-progetto-rebecca-per-lo-sport-sostiene-voluntas-calcio-suna.html">Voluntas Calcio Suna <span>30 maggio 2016</span></a></li>
          <li><a href="/eventi/601463-goal-sport-dona-2000-euro-a-progetto-rebecca-per-lo-sport.html">Goal Sport dona 2.000 euro <span>2 aprile 2016</span></a></li>
        </ul>
      </div>
    </div>

    <div class="project-feature project-feature--reverse" id="parco">
      <div class="project-feature-media">
        <img src="/assets/img/news/1300852/cover.webp" alt="Il Parco Giochi di Rebecca a Villa Olimpia" loading="lazy" decoding="async">
      </div>
      <div class="project-feature-body">
        <p class="eyebrow">Verbania · 2017–2018</p>
        <h2>Il Parco Giochi di Rebecca</h2>
        <p>Un parco giochi inclusivo nel giardino di Villa Olimpia a Pallanza, dedicato a Rebecca. Il progetto, promosso dal Corso di Corsa Verbania, è stato il più votato dai cittadini nel Bilancio Partecipato 2017.</p>
        <p>Il costo previsto era di circa 79.700 euro, di cui 39.500 euro coperti dal contributo del Comune di Verbania. Il parco è stato inaugurato sabato 28 luglio 2018 alle ore 10.00.</p>
        <div class="fact-row">
          <div class="fact"><strong>79.700 €</strong><span>costo previsto</span></div>
          <div class="fact"><strong>39.500 €</strong><span>contributo del Comune</span></div>
          <div class="fact"><strong>28 luglio 2018</strong><span>inaugurazione</span></div>
        </div>
        <ul class="project-links">
          <li><a href="/eventi/1219524-il-parco-giochi-di-rebecca-diventa-realta.html">Il progetto definitivo <span>14 dicembre 2017</span></a></li>
          <li><a href="/eventi/1293432-e-nato-il-parco-il-giochi-di-rebecca.html">“È nato il Parco Giochi di Rebecca” <span>14 luglio 2018</span></a></li>
          <li><a href="/eventi/1300852-il-parco-giochi-di-rebecca-e-realta.html">L'inaugurazione <span>7 agosto 2018</span></a></li>
        </ul>
      </div>
    </div>
  </div>
</section>

<section class="section section--blush">
  <div class="container ulmi-partner">
    <div>
      <p class="eyebrow">La partnership</p>
      <h2>U.L.M.I. — Una Luce Marinista in Italia e in India</h2>
      <p>Il Progetto Rebecca ha collaborato con U.L.M.I. Onlus e con le Suore Marianiste per il sostegno a distanza dei bambini di Ranchi. Un legame che continua nel tempo.</p>
    </div>
    <a class="btn btn--primary" href="/ulmi.html">Conosci U.L.M.I.</a>
  </div>
</section>

<section class="donate-band">
  <div class="container donate-band-inner">
    <div class="donate-band-copy">
      <p class="eyebrow">Sostieni un progetto</p>
      <h2>Ogni progetto ha bisogno di qualcuno che ci creda.</h2>
      <p>Puoi sostenere il Kindergarten, le scuole del VCO, lo sport o il Parco Giochi con una donazione o con il tuo tempo.</p>
    </div>
    <div class="donate-band-actions">
      <a class="btn btn--light" href="/donare.html">Dona ora</a>
      <a class="btn btn--outline-light" href="/contatti.html">Parliamone</a>
    </div>
  </div>
</section>'''

    return base_page(
        title="I progetti | Progetto Rebecca",
        description="I progetti del Progetto Rebecca: Kindergarten Rebecca a Ranchi, scuole del VCO, Progetto Rebecca per lo Sport e Parco Giochi di Rebecca a Villa Olimpia.",
        canonical=f"{SITE_URL}/progetti.html",
        content=content,
        active="progetti",
        body_class="page-progetti",
    )


def build_donare_page() -> str:
    content = page_hero(
        "Dona",
        "Dare è il modo più semplice per ricordare",
        "Ogni donazione diventa un progetto concreto: una scuola, un materiale didattico, un defibrillatore, un parco giochi. Grazie con Rebecca nel cuore.",
    )

    content += '''
<section class="section">
  <div class="container">
    <div class="donate-grid">
      <article class="donate-card donate-card--wide">
        <p class="eyebrow">Bonifico bancario</p>
        <h2>Donazione diretta</h2>
        <p>Puoi sostenere il Progetto Rebecca con un bonifico bancario. Ogni importo, anche piccolo, contribuisce a un progetto concreto.</p>
        <dl class="donation-details">
          <div><dt>Intestazione</dt><dd>PROGETTO REBECCA “il progetto dei bimbi”</dd></div>
          <div><dt>Banca</dt><dd>Banca Prossima per le Imprese Sociali e le Comunità<br>(Gruppo Intesa Sanpaolo)</dd></div>
          <div><dt>IBAN</dt><dd class="iban-box"><code>IT47 S 03359 67684 51070 0157904</code><button class="copy-btn" type="button" data-copy="IT47S0335967684510700157904">Copia</button></dd></div>
        </dl>
      </article>

      <article class="donate-card">
        <p class="eyebrow">5 per mille</p>
        <h2>Un gesto che non costa nulla</h2>
        <p>Nella dichiarazione dei redditi puoi destinare il 5 per mille al Progetto Rebecca. Per farlo serve il codice fiscale dell'associazione: scrivici e ti invieremo i dati aggiornati.</p>
        <p class="donate-note">All'8 aprile 2014 il progetto Kindergarten era realizzato con 37.365 euro grazie anche al 5 per mille.</p>
        <a class="btn btn--outline btn--sm" href="mailto:info@progettorebecca.org?subject=Informazioni%205%20per%20mille">Chiedi i dati fiscali</a>
      </article>

      <article class="donate-card">
        <p class="eyebrow">Sostegno a distanza</p>
        <h2>25 euro al mese</h2>
        <p>Non è difficile togliere un bambino dalla strada e dalla miseria, offrirgli un'istruzione, la sicurezza di mangiare, dei vestiti. Bastano 25 euro al mese. Per quanto tempo? Lo decidi tu.</p>
        <a class="btn btn--outline btn--sm" href="mailto:info@ulmi.it?subject=Sostegno%20a%20distanza%20-%20Progetto%20Rebecca">Informazioni U.L.M.I.</a>
      </article>

      <article class="donate-card">
        <p class="eyebrow">Dona il tuo tempo</p>
        <h2>Partecipa</h2>
        <p>Puoi aiutarci con un evento, una raccolta fondi, un'idea o un semplice passaparola. Il Progetto Rebecca è fatto di persone che si mettono insieme.</p>
        <a class="btn btn--outline btn--sm" href="/contatti.html">Scrivici</a>
      </article>
    </div>
  </div>
</section>

<section class="section section--blush" id="cinque-x-mille">
  <div class="container narrow">
    <div class="section-heading section-heading--left">
      <p class="eyebrow">Il 5 per mille</p>
      <h2>Il Kindergarten è stato anche questo</h2>
    </div>
    <blockquote class="pullquote">
      <p>Ringraziamo tutti i sostenitori del PROGETTO REBECCA che hanno aiutato a realizzare questo sogno. All'8 aprile 2014 progetto realizzato con 37.365 euro.</p>
      <footer>…Grazie con Rebecca nel cuore…</footer>
    </blockquote>
    <p>Il 5 per mille è un modo semplice per sostenere i progetti dell'associazione senza costi aggiuntivi. Nella prossima dichiarazione dei redditi puoi scegliere di destinarlo al Progetto Rebecca.</p>
  </div>
</section>

<section class="section" id="sostieni">
  <div class="container narrow legal">
    <h2>Come aiutarci</h2>
    <p>Il Progetto Rebecca “il progetto dei bimbi” opera attraverso donazioni, eventi e raccolte fondi. I contributi vengono utilizzati per i progetti approvati dall'associazione e raccontati nel diario.</p>
    <h3>Dati per il bonifico</h3>
    <p><strong>Intestazione:</strong> PROGETTO REBECCA “il progetto dei bimbi”<br>
    <strong>Banca:</strong> Banca Prossima per le Imprese Sociali e le Comunità (Gruppo Intesa Sanpaolo)<br>
    <strong>IBAN:</strong> IT47 S 03359 67684 51070 0157904</p>
    <p>Per qualsiasi dubbio o per ricevere informazioni puoi scrivere a <a href="mailto:info@progettorebecca.org">info@progettorebecca.org</a> o chiamare il <a href="tel:+393475024855">347 5024855</a> (Giovanni Bonaffini).</p>
  </div>
</section>'''

    return base_page(
        title="Donare | Progetto Rebecca",
        description="Come sostenere il Progetto Rebecca: bonifico bancario, 5 per mille, sostegno a distanza e partecipazione agli eventi.",
        canonical=f"{SITE_URL}/donare.html",
        content=content,
        active="donare",
        body_class="page-donare",
    )


def build_contatti_page() -> str:
    map_url = (
        "https://www.google.com/maps/embed?pb=!1m18!1m12!1m3!1d3315.8086920280416!"
        "2d8.557033675819222!3d45.92477492062893!2m3!1f0!2f0!3f0!3m2!1i1024!2i768!4f13.1!"
        "3m3!1m2!1s0x4785df1f8310e939%3A0x6af7bdd3c9b17647!2sVia+San+Remigio!5e1!3m2!1sit!2sit!4v1395776693501"
    )
    content = page_hero(
        "Contatti",
        "Siamo qui, con il cuore aperto",
        "Per informazioni, per sostenere un progetto o semplicemente per conoscerci: scrivici, chiamaci o vieni a trovarci.",
    )

    content += f'''
<section class="section">
  <div class="container contact-grid">
    <article class="contact-card">
      <p class="eyebrow">Email</p>
      <h2><a href="mailto:info@progettorebecca.org">info@progettorebecca.org</a></h2>
      <p>Il modo più diretto per scriverci.</p>
    </article>
    <article class="contact-card">
      <p class="eyebrow">Telefono</p>
      <h2><a href="tel:+393475024855">347 5024855</a></h2>
      <p>Giovanni Bonaffini, papà di Rebecca.</p>
    </article>
    <article class="contact-card">
      <p class="eyebrow">Sede legale</p>
      <h2>Via Indipendenza 16</h2>
      <p>Oltrefiume di Baveno (VB)<br>Presso la famiglia Bonaffini.</p>
    </article>
    <article class="contact-card">
      <p class="eyebrow">Sede operativa</p>
      <h2>Via San Remigio 20/B</h2>
      <p>Casa Nazareth, Villa Chaminade<br>Verbania.</p>
    </article>
  </div>
</section>

<section class="section section--blush">
  <div class="container contact-layout">
    <div class="contact-copy">
      <p class="eyebrow">Scrivici</p>
      <h2>Un messaggio, una domanda, un'idea</h2>
      <p>Il modulo apre il tuo programma di posta: nessun dato viene inviato a server esterni. Puoi anche scriverci direttamente a <a href="mailto:info@progettorebecca.org">info@progettorebecca.org</a>.</p>
      <form class="contact-form" id="contact-form">
        <div class="form-row">
          <div class="form-field">
            <label for="contact-name">Nome</label>
            <input type="text" id="contact-name" name="name" autocomplete="name" required>
          </div>
          <div class="form-field">
            <label for="contact-email">Email</label>
            <input type="email" id="contact-email" name="email" autocomplete="email" required>
          </div>
        </div>
        <div class="form-field">
          <label for="contact-subject">Oggetto</label>
          <input type="text" id="contact-subject" name="subject" required>
        </div>
        <div class="form-field">
          <label for="contact-message">Messaggio</label>
          <textarea id="contact-message" name="message" rows="6" required></textarea>
        </div>
        <button class="btn btn--primary" type="submit">Apri il messaggio</button>
        <p class="form-note" id="form-note" role="status"></p>
      </form>
    </div>
    <div class="map-card">
      <iframe src="{map_url}" title="Mappa della sede operativa in Via San Remigio 20/B, Verbania" loading="lazy" referrerpolicy="no-referrer-when-downgrade" allowfullscreen></iframe>
      <p>Via San Remigio 20/B · Casa Nazareth, Villa Chaminade · Verbania</p>
    </div>
  </div>
</section>

<section class="section">
  <div class="container narrow social-band">
    <div>
      <p class="eyebrow">Facebook</p>
      <h2>Seguici per gli aggiornamenti</h2>
      <p>Le iniziative, gli eventi e le storie del Progetto Rebecca raccontate giorno per giorno.</p>
    </div>
    <a class="btn btn--primary" href="https://www.facebook.com/ProgettoRebecca" target="_blank" rel="noopener">Vai alla pagina Facebook</a>
  </div>
</section>'''

    return base_page(
        title="Contatti | Progetto Rebecca",
        description="Contatta il Progetto Rebecca: email, telefono, sede legale a Baveno e sede operativa a Verbania.",
        canonical=f"{SITE_URL}/contatti.html",
        content=content,
        active="contatti",
        body_class="page-contatti",
        scripts='<script>document.addEventListener("DOMContentLoaded",function(){var f=document.getElementById("contact-form");if(!f)return;f.addEventListener("submit",function(e){e.preventDefault();var n=document.getElementById("contact-name").value.trim(),m=document.getElementById("contact-email").value.trim(),s=document.getElementById("contact-subject").value.trim(),t=document.getElementById("contact-message").value.trim();var body="Nome: "+n+"\\nEmail: "+m+"\\n\\n"+t;window.location.href="mailto:info@progettorebecca.org?subject="+encodeURIComponent(s)+"&body="+encodeURIComponent(body);document.getElementById("form-note").textContent="Si aprirà il tuo programma di posta.";});});</script>',
    )


def build_privacy_page() -> str:
    content = page_hero(
        "Privacy",
        "Informazioni sul trattamento dei dati",
        "Questo sito è statico, non usa cookie di profilazione e non raccoglie dati personali in modo automatico.",
    )

    content += '''
<section class="section">
  <div class="container narrow legal">
    <p>Ultimo aggiornamento: ottobre 2026.</p>

    <h2>Titolare del trattamento</h2>
    <p>Progetto Rebecca “il progetto dei bimbi”<br>Via Indipendenza 16, Oltrefiume di Baveno (VB)<br>Email: <a href="mailto:info@progettorebecca.org">info@progettorebecca.org</a></p>

    <h2>Quali dati trattiamo</h2>
    <p>Il sito non utilizza moduli che inviano dati a un server: il modulo di contatto apre il programma di posta del visitatore. Se ci scrivi via email, trattiamo i dati che scegli di condividere (nome, indirizzo email, contenuto del messaggio) al solo fine di rispondere.</p>
    <p>Non raccogliamo dati tramite cookie di profilazione, non usiamo analytics e non creiamo profili degli utenti.</p>

    <h2>Cookie e servizi esterni</h2>
    <p>Il sito non installa cookie propri. I video YouTube sono incorporati tramite il dominio <code>youtube-nocookie.com</code> e vengono caricati solo dopo un clic sul poster: fino a quel momento non viene contattato YouTube. Dopo il clic si applicano le informative di YouTube/Google.</p>
    <p>La mappa dei contatti è un iframe di Google Maps: viene caricata solo quando si apre la pagina Contatti e può comportare il contatto con i server di Google. La pagina Facebook è raggiungibile tramite un link esterno.</p>
    <p>Il sito carica i font da Google Fonts: la richiesta comporta il contatto con i server di Google. Se preferisci evitarlo, puoi bloccare le richieste esterne dal tuo browser.</p>

    <h2>Conservazione</h2>
    <p>I messaggi email vengono conservati per il tempo necessario a rispondere e a gestire l'eventuale richiesta. Non vengono usati per finalità di marketing senza consenso.</p>

    <h2>I tuoi diritti</h2>
    <p>Puoi chiedere accesso, rettifica, cancellazione o limitazione dei tuoi dati scrivendo a <a href="mailto:info@progettorebecca.org">info@progettorebecca.org</a>. Puoi inoltre proporre reclamo al Garante per la protezione dei dati personali.</p>

    <h2>Archivio storico</h2>
    <p>Le pagine del diario conservano testi, fotografie e video pubblicati sul sito originale del Progetto Rebecca. I commenti e i dati di navigazione del vecchio sito non sono stati trasferiti. Se un contenuto ti riguarda e desideri segnalarlo, scrivici.</p>
  </div>
</section>'''

    return base_page(
        title="Privacy | Progetto Rebecca",
        description="Informativa privacy del sito statico del Progetto Rebecca: nessun cookie di profilazione, video YouTube solo su clic, dati trattati solo via email.",
        canonical=f"{SITE_URL}/privacy.html",
        content=content,
        body_class="page-privacy",
    )


def build_ulmi_page() -> str:
    ulmi = parse_static("n53397-u-l-m-i.htm")
    body = ulmi["body_html"] or "<p>Il testo storico della pagina U.L.M.I. non è disponibile.</p>"
    content = page_hero(
        "La partnership",
        "U.L.M.I. — Una Luce Marinista in Italia e in India",
        "La Congregazione delle Figlie di Maria Immacolata, le Suore Marianiste e il sostegno ai bambini di Ranchi: la storia di una collaborazione nata nel nome di Rebecca.",
    )

    content += f'''
<section class="section">
  <div class="container ulmi-layout">
    <div class="ulmi-copy">
      {body}
    </div>
    <aside class="ulmi-aside">
      <div class="sidebar-card sidebar-card--donate">
        <span class="sidebar-card-icon" aria-hidden="true">{HEART_SVG}</span>
        <h2>Sostegno a distanza</h2>
        <p>Bastano 25 euro al mese per offrire istruzione, cibo, vestiti e medicine a un bambino di Ranchi.</p>
        <a class="btn btn--primary btn--sm" href="/donare.html#sostieni">Scopri come</a>
      </div>
      <div class="sidebar-card">
        <h2>Contatti U.L.M.I.</h2>
        <p>Per informazioni sul sostegno a distanza:</p>
        <p><a href="mailto:info@ulmi.it">info@ulmi.it</a><br>Tel. +039 0424 858248</p>
        <p class="muted">I riferimenti sono quelli pubblicati sul sito storico del Progetto Rebecca.</p>
      </div>
      <div class="sidebar-card">
        <h2>Nel diario</h2>
        <ul class="sidebar-list">
          <li><a href="/eventi/127107-ecco-il-kindergarten-rebecca.html"><span>31 marzo 2014</span>Ecco il Kindergarten Rebecca</a></li>
          <li><a href="/eventi/689254-dall-india-per-la-presentazione-del-kindergarten-rebecca.html"><span>26 giugno 2016</span>Dall'India per la presentazione</a></li>
          <li><a href="/eventi/1221397-dal-kindergarten-rebecca-in-india-ranchi-arrivano-gli-auguri-di-buone-feste.html"><span>19 dicembre 2017</span>Gli auguri da Ranchi</a></li>
        </ul>
      </div>
    </aside>
  </div>
</section>'''

    return base_page(
        title="U.L.M.I. | Progetto Rebecca",
        description="U.L.M.I. — Una Luce Marinista in Italia e in India: la storia della Congregazione delle Figlie di Maria Immacolata e il sostegno ai bambini di Ranchi.",
        canonical=f"{SITE_URL}/ulmi.html",
        content=content,
        body_class="page-ulmi",
    )


def build_404_page() -> str:
    content = page_hero(
        "Pagina non trovata",
        "Questa pagina non c'è",
        "Forse il link è vecchio o la pagina è stata spostata. Puoi tornare alla home o cercare nel diario.",
    )
    content += '''<section class="section"><div class="container narrow text-center">
  <a class="btn btn--primary" href="/">Torna alla home</a>
  <a class="btn btn--ghost" href="/eventi.html">Cerca nel diario</a>
</div></section>'''
    return base_page(
        title="Pagina non trovata | Progetto Rebecca",
        description="Pagina non trovata.",
        canonical=f"{SITE_URL}/404.html",
        content=content,
        body_class="page-404",
    )


def build_static_pages(articles: list[dict], video_meta: list[dict]) -> None:
    pages = {
        "index.html": build_home_page(articles, video_meta),
        "chi-siamo.html": build_chi_siamo_page(),
        "progetti.html": build_progetti_page(articles),
        "donare.html": build_donare_page(),
        "contatti.html": build_contatti_page(),
        "privacy.html": build_privacy_page(),
        "ulmi.html": build_ulmi_page(),
        "404.html": build_404_page(),
    }
    for name, html in pages.items():
        (ROOT / name).write_text(html, encoding="utf-8")

    write_redirects(articles)
    write_sitemap(articles)
    write_robots()
    write_netlify()
    write_readme()


def write_redirects(articles: list[dict]) -> None:
    lines = [
        "# Pagine statiche del vecchio sito",
        "/s90-eventi.htm /eventi.html 301",
        "/s91-donare.htm /donare.html 301",
        "/s92-dove-siamo.htm /contatti.html 301",
        "/s93-chi-siamo.htm /chi-siamo.html 301",
        "/s94-gallery.htm /gallery.html 301",
        "/s95-contatti.htm /contatti.html 301",
        "/i1 /contatti.html 301",
        "/i15 /privacy.html 301",
        "/n124711-dove-siamo.htm /contatti.html 301",
        "/n138208-progetto-rebecca-il-progetto-dei-bimbi.htm /chi-siamo.html 301",
        "/n53397-u-l-m-i.htm /ulmi.html 301",
        "/n53399-contatti.htm /contatti.html 301",
        "/n53401-sostieni-un-bimbo-a-distanza.htm /donare.html#sostieni 301",
        "/n53405-5-per-mille-kindergarten.htm /donare.html#cinque-x-mille 301",
        "/n53406-come-aiutarci.htm /donare.html#sostieni 301",
        "/n53407-video.htm /gallery.html#video 301",
        "/n54624-foto-rebecca.htm /gallery.html 301",
        "",
        "# Articoli: vecchi URL /n{id}-{slug}.htm",
    ]
    for a in articles:
        lines.append(f"{a['old_url']} {a['url']} 301")
    (ROOT / "_redirects").write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_sitemap(articles: list[dict]) -> None:
    static_pages = [
        ("/", "1.0"),
        ("/chi-siamo.html", "0.9"),
        ("/progetti.html", "0.9"),
        ("/eventi.html", "0.9"),
        ("/gallery.html", "0.8"),
        ("/donare.html", "0.8"),
        ("/contatti.html", "0.7"),
        ("/ulmi.html", "0.6"),
        ("/privacy.html", "0.3"),
    ]
    today = datetime.now().strftime("%Y-%m-%d")
    rows = []
    for path, priority in static_pages:
        rows.append(
            f"  <url><loc>{SITE_URL}{path}</loc><lastmod>{today}</lastmod>"
            f"<changefreq>monthly</changefreq><priority>{priority}</priority></url>"
        )
    for a in articles:
        lastmod = (a["date_iso"] or "")[:10] or today
        rows.append(
            f"  <url><loc>{SITE_URL}{a['url']}</loc><lastmod>{lastmod}</lastmod>"
            f"<changefreq>yearly</changefreq><priority>0.6</priority></url>"
        )
    xml = '<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n' + "\n".join(rows) + "\n</urlset>\n"
    (ROOT / "sitemap.xml").write_text(xml, encoding="utf-8")


def write_robots() -> None:
    (ROOT / "robots.txt").write_text(
        "User-agent: *\nAllow: /\n\nSitemap: https://www.progettorebecca.org/sitemap.xml\n",
        encoding="utf-8",
    )


def write_netlify() -> None:
    (ROOT / "netlify.toml").write_text(
        '''[build]
  publish = "."

[[headers]]
  for = "/*"
  [headers.values]
    X-Content-Type-Options = "nosniff"
    Referrer-Policy = "strict-origin-when-cross-origin"
    X-Frame-Options = "SAMEORIGIN"

[[headers]]
  for = "/assets/img/*"
  [headers.values]
    Cache-Control = "public, max-age=31536000, immutable"

[[headers]]
  for = "/assets/css/*"
  [headers.values]
    Cache-Control = "public, max-age=604800"

[[headers]]
  for = "/assets/js/*"
  [headers.values]
    Cache-Control = "public, max-age=604800"
''',
        encoding="utf-8",
    )


def write_readme() -> None:
    (ROOT / "README-MANUTENZIONE.md").write_text(
        '''# Manutenzione del sito

Il sito è statico: non c'è un database e non serve un CMS. Le pagine sono file HTML nella cartella principale e in `eventi/`.

## Rigenerare le pagine

I contenuti storici vengono letti dagli HTML originali e rigenerati con:

```powershell
python tools/build.py
```

Per rigenerare solo le pagine senza riscaricare le immagini:

```powershell
python tools/build.py --no-media
```

## Aggiungere una nuova storia

1. Crea una cartella `assets/img/news/NUOVO-ID/` e inserisci le immagini in WebP (versione grande) e una copertina.
2. Crea la miniatura in `assets/img/thumbs/NUOVO-ID-1.webp`.
3. Copia un file esistente in `eventi/` e aggiorna titolo, data, testo e immagini.
4. Aggiungi una scheda in `eventi.html` copiando un blocco `<article class="news-card">` e aggiornando link, data, titolo e testo.
5. Aggiungi la nuova URL in `sitemap.xml`.

## Deploy

Il sito può essere pubblicato su Netlify o Cloudflare Pages collegando il repository Git. Non serve un comando di build: la cartella pubblicata è la radice del progetto. I redirect del vecchio sito sono in `_redirects`.

## Contatti e dati

- Email: info@progettorebecca.org
- Telefono: 347 5024855 (Giovanni Bonaffini)
- IBAN: IT47 S 03359 67684 51070 0157904
''',
        encoding="utf-8",
    )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def save_content(articles: list[dict]) -> None:
    CONTENT.mkdir(exist_ok=True)
    (CONTENT / "articles.json").write_text(
        json.dumps(articles, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    # indice leggero per ricerca/filtri
    index = [
        {
            "id": a["id"],
            "title": a["title"],
            "date": a["date_iso"],
            "year": a["year"],
            "url": a["url"],
            "excerpt": a["excerpt"],
            "tags": a["tags"],
            "cover": a["cover"],
        }
        for a in articles
    ]
    (DATA / "news.json").write_text(json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8")


def apply_cover_fallback(a: dict, posters: set[str]) -> str:
    cover = NEWS_IMG / a["id"] / "cover.webp"
    if cover.exists() and image_stddev(cover) >= MIN_COVER_STDDEV:
        a["cover"] = f"/assets/img/news/{a['id']}/cover.webp"
        a["cover_id"] = a["id"]
        return "cover"

    for im in a["images"]:
        path = NEWS_IMG / im["id"] / f"{im['num']}.webp"
        if path.exists() and image_stddev(path) >= MIN_COVER_STDDEV:
            a["cover"] = f"/assets/img/news/{im['id']}/{im['num']}.webp"
            a["cover_id"] = ""
            return "image"

    for vid in a["videos"]:
        if vid in posters:
            a["cover"] = f"/assets/img/video/{vid}.jpg"
            a["cover_id"] = ""
            return "video"

    a["cover"] = ""
    a["cover_id"] = ""
    return "none"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--parse-only", action="store_true")
    parser.add_argument("--no-media", action="store_true")
    args = parser.parse_args()

    ensure_dirs()
    print("Parsing articoli…")
    articles = parse_all()
    print(f"  {len(articles)} articoli")

    if not args.no_media and not args.parse_only:
        print("Download media…")
        download_all_media(articles)

    # aggiorna i poster noti e rigenera i body con i poster
    posters = {p.stem for p in VIDEO_IMG.glob("*.jpg")}
    article_map = {a["old_url"]: a["url"] for a in articles}
    fallbacks = {"image": 0, "video": 0, "none": 0}
    for a in articles:
        a["body_html"] = rebuild_body(a, posters, article_map)
        source = apply_cover_fallback(a, posters)
        if source in fallbacks:
            fallbacks[source] += 1
    if any(fallbacks.values()):
        print(
            "Cover sostitutive: "
            f"{fallbacks['image']} da immagine, {fallbacks['video']} da video, "
            f"{fallbacks['none']} senza immagine"
        )

    save_content(articles)
    print("Contenuti salvati in content/articles.json")

    if args.parse_only:
        return

    print("Generazione pagine articolo…")
    OUT_ARTICLES.mkdir(exist_ok=True)
    for a in articles:
        html = build_article_page(a, articles, {})
        out = OUT_ARTICLES / f"{a['id']}-{a['slug']}.html"
        out.write_text(html, encoding="utf-8")
    print(f"  {len(articles)} pagine")

    print("Generazione archivio…")
    (ROOT / "eventi.html").write_text(build_archive_page(articles), encoding="utf-8")

    video_meta = json.loads((DATA / "videos.json").read_text(encoding="utf-8")) if (DATA / "videos.json").exists() else []
    print("Generazione gallery…")
    (ROOT / "gallery.html").write_text(build_gallery_page(articles, video_meta), encoding="utf-8")

    print("Generazione pagine istituzionali…")
    build_static_pages(articles, video_meta)
    print("Fatto.")


if __name__ == "__main__":
    main()


