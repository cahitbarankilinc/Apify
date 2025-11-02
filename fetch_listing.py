#!/usr/bin/env python3
"""Download an eBay Kleinanzeigen listing HTML into the local `lisitings` folder."""
from __future__ import annotations

import datetime as _dt
from pathlib import Path
from typing import Optional
import os
from http.cookiejar import CookieJar
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import HTTPCookieProcessor, Request, build_opener


def _suggest_filename(url: str) -> str:
    """Derive a readable filename for the listing HTML."""
    parsed = urlparse(url)
    path = parsed.path.rstrip("/")
    slug: Optional[str] = None

    if path:
        # Take the last non-empty segment as a slug candidate.
        parts = [segment for segment in path.split("/") if segment]
        if parts:
            slug = parts[-1]

    if not slug:
        slug = "listing"

    # eBay Kleinanzeigen listing URLs usually end with a numeric ID separated by dots.
    # Retain alphanumeric, dash, underscore, and dot characters for the filename.
    sanitized = [ch for ch in slug if ch.isalnum() or ch in {"-", "_", "."}]
    filename = "".join(sanitized) or "listing"

    return f"{filename}.html"


def _unique_path(directory: Path, filename: str) -> Path:
    """Ensure that saving does not overwrite an existing file."""
    target = directory / filename
    if not target.exists():
        return target

    stem = target.stem
    suffix = target.suffix
    timestamp = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    return directory / f"{stem}-{timestamp}{suffix}"


def _build_request(url: str) -> Request:
    """Prepare a browser-like request for the Kleinanzeigen listing.

    If the site requires authenticated access, provide the session cookie via the
    ``KLEINANZEIGEN_COOKIE`` environment variable so that the request header
    includes it.
    """
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/125.0.0.0 Safari/537.36"
        ),
        "Accept": (
            "text/html,application/xhtml+xml,application/xml;q=0.9," "image/avif,image/webp,image/apng,*/*;q=0.8"
        ),
        "Accept-Language": "de-DE,de;q=0.9,en-US;q=0.8,en;q=0.7",
        "Accept-Encoding": "gzip, deflate",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
        "Referer": "https://www.kleinanzeigen.de/",
    }

    cookie = os.getenv("KLEINANZEIGEN_COOKIE")
    if cookie:
        headers["Cookie"] = cookie

    return Request(url, headers=headers)


def download_listing(url: str) -> Path:
    """Fetch the listing URL and persist the HTML under `lisitings/`.

    Returns the filesystem path of the saved HTML file.
    """
    if not url:
        raise ValueError("URL must not be empty")

    request = _build_request(url)

    try:
        opener = build_opener(HTTPCookieProcessor(CookieJar()))
        with opener.open(request) as response:  # type: ignore[call-arg]
            content = response.read()
    except HTTPError as exc:  # pragma: no cover - network errors depend on runtime
        hint = ""
        if exc.code == 403:
            hint = (
                ". Access denied – set the KLEINANZEIGEN_COOKIE environment "
                "variable with a valid session cookie if the listing "
                "requires authentication"
            )
        raise RuntimeError(f"HTTP error {exc.code} while fetching {url}{hint}") from exc
    except URLError as exc:  # pragma: no cover - network errors depend on runtime
        raise RuntimeError(f"Failed to reach {url}: {exc.reason}") from exc

    target_dir = Path("lisitings")
    target_dir.mkdir(parents=True, exist_ok=True)

    filename = _suggest_filename(url)
    target_path = _unique_path(target_dir, filename)

    with open(target_path, "wb") as handle:
        handle.write(content)

    return target_path


def main() -> None:
    url = input("Listing URL: ").strip()
    if not url:
        print("No URL provided. Aborting.")
        return

    try:
        saved_path = download_listing(url)
    except Exception as exc:  # pragma: no cover - communicates error to CLI user
        print(f"Error: {exc}")
        return

    print(f"Saved HTML to {saved_path}")


if __name__ == "__main__":
    main()
