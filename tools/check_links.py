#!/usr/bin/env python3
"""Controlla link interni e risorse locali del sito generato."""
from __future__ import annotations

import re
import sys
from pathlib import Path
from urllib.parse import unquote, urlparse

ROOT = Path(__file__).resolve().parents[1]
LINK_RE = re.compile(r'(?:href|src)="([^"]+)"')
CSS_URL_RE = re.compile(r'url\(([^)]+)\)')


def local_path(url: str) -> Path | None:
    if not url or url.startswith(("http://", "https://", "mailto:", "tel:", "data:", "#", "javascript:")):
        return None
    parsed = urlparse(url)
    if parsed.scheme or parsed.netloc:
        return None
    path = unquote(parsed.path)
    if not path.startswith("/"):
        return None
    if path == "/":
        return ROOT / "index.html"
    target = ROOT / path.lstrip("/")
    if path.endswith("/"):
        target = target / "index.html"
    return target


def main() -> int:
    errors = []
    files = list(ROOT.glob("*.html")) + list((ROOT / "eventi").glob("*.html"))
    for page in files:
        text = page.read_text(encoding="utf-8")
        for url in LINK_RE.findall(text):
            target = local_path(url)
            if target is not None and not target.exists():
                errors.append(f"{page.relative_to(ROOT)} -> {url}")
        for css in [p for p in ROOT.glob("assets/css/*.css")]:
            for url in CSS_URL_RE.findall(css.read_text(encoding="utf-8")):
                url = url.strip("'\" ")
                target = local_path(url)
                if target is not None and not target.exists():
                    errors.append(f"{css.relative_to(ROOT)} -> {url}")

    if errors:
        print(f"{len(errors)} link/risorse mancanti:")
        for e in errors[:200]:
            print(" ", e)
        return 1
    print(f"OK: controllati {len(files)} file HTML, nessun link locale mancante.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
