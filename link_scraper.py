#!/usr/bin/env python3
"""Fetch a Kleinanzeigen search page and extract listing links."""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Set
from urllib.parse import urljoin

from extract_listing import Node, find_by_id, parse_html
from fetch_listing import download_listing


def _iter_articles(node: Node) -> Iterable[Node]:
    """Yield every ``<article>`` descendant of ``node``."""
    if node.tag == "article":
        yield node
    for child in node.children:
        yield from _iter_articles(child)


def _extract_distance(article: Node) -> Optional[str]:
    """Return the distance text (e.g., "322 km") for a result article."""

    def _iter_nodes(node: Node) -> Iterable[Node]:
        yield node
        for child in node.children:
            yield from _iter_nodes(child)

    for node in _iter_nodes(article):
        classes = set(node.class_list())
        if "aditem-main--top--left" not in classes and "aditem-main" not in classes:
            continue

        text = " ".join(part.strip() for part in node.iter_text() if part.strip())
        match = re.search(r"\(([^)]*?km)\)", text, flags=re.IGNORECASE)
        if match:
            return match.group(1).strip()
    return None


def _collect_listing_links(root: Node) -> List[tuple[str, Optional[str]]]:
    """Return pairs of ``data-href`` values and their distances."""
    container = find_by_id(root, "srchrslt-adtable")
    if container is None:
        return []

    links: List[tuple[str, Optional[str]]] = []
    seen: Set[str] = set()
    for item in container.children:
        if item.tag != "li":
            continue
        for article in _iter_articles(item):
            href = article.attrs.get("data-href")
            if href and href not in seen:
                seen.add(href)
                links.append((href, _extract_distance(article)))
            # Only keep the first article per list item to avoid duplicates.
            break

    return links


def _with_page(url: str, page: int) -> str:
    """Return ``url`` with a usable pagination segment for ``page``."""

    def _repl(match: re.Match) -> str:
        return f"{match.group(1)}{page}"

    updated = re.sub(r"(seite:)(\d+)", _repl, url, count=1)
    if updated != url or page == 1:
        return updated

    # If the URL already carries query parameters, prefer the explicit ``page``
    # query knob because some Kleinanzeigen variations route pagination there.
    if "?" in url:
        separator = "&" if not url.endswith("?") else ""
        return f"{url}{separator}page={page}"

    # If the original URL lacks an explicit ``seite:`` segment, insert one
    # before the trailing path component. For Kleinanzeigen search URLs this
    # usually places the segment immediately before the filter block (e.g.,
    # ``.../bmw/seite:2/k0c216l7611r20``).
    if "/" not in url:
        return f"{url}/seite:{page}"

    head, tail = url.rsplit("/", 1)
    if not tail:
        return f"{url}seite:{page}"

    return f"{head}/seite:{page}/{tail}"


def _iter_nodes(node: Node) -> Iterable[Node]:
    """Yield ``node`` and all of its descendants."""

    yield node
    for child in node.children:
        yield from _iter_nodes(child)


def _find_next_page(root: Node, current_url: str) -> Optional[str]:
    """Return the next page URL if available.

    Kleinanzeigen encodes pagination in different ways (``rel="next"`` links,
    anchors with pagination-specific classes, and textual "Weiter" labels). The
    scraper examines common patterns and converts relative links to absolute
    URLs. ``None`` is returned when no next page is detected.
    """

    def _matches_pagination(anchor: Node, text: str) -> bool:
        aria = anchor.attrs.get("aria-label", "").lower()
        title = anchor.attrs.get("title", "").lower()
        classes = set(anchor.class_list())
        rel_attr = anchor.attrs.get("rel", "")
        rel_values = {value.strip().lower() for value in rel_attr.split()} if rel_attr else set()

        patterns = [aria, title, text]
        if any(keyword in value for value in patterns for keyword in ("weiter", "next")):
            return True

        pagination_classes = {cls for cls in classes if "pagination" in cls or cls.endswith("__next")}
        if pagination_classes:
            return True

        if "next" in rel_values:
            return True

        return False

    for node in _iter_nodes(root):
        if node.tag not in {"a", "link"}:
            continue

        href = node.attrs.get("href")
        if not href:
            continue

        if node.tag == "link":
            rel_attr = node.attrs.get("rel", "")
            rel_values = {value.strip().lower() for value in rel_attr.split()}
            if "next" in rel_values:
                return urljoin(current_url, href)
            continue

        text = " ".join(part.strip() for part in node.iter_text() if part.strip()).lower()
        if _matches_pagination(node, text):
            return urljoin(current_url, href)

    return None


def _to_absolute(href: str) -> str:
    """Ensure listing links use an absolute Kleinanzeigen URL."""

    if href.startswith("http://") or href.startswith("https://"):
        return href
    return f"https://www.kleinanzeigen.de{href}"


def main() -> None:
    url = input("Search URL: ").strip()
    if not url:
        print("No URL provided. Aborting.")
        return

    collected_links: List[str] = []
    collected_metadata: List[Dict[str, str]] = []
    seen_links: Set[str] = set()

    page = 1
    page_url = url
    visited_pages: Set[str] = set()
    max_pages = 50

    while page_url and page <= max_pages:
        if page_url in visited_pages:
            print("No additional pages detected; stopping pagination.")
            break
        visited_pages.add(page_url)

        try:
            saved_path = download_listing(page_url)
        except Exception as exc:  # pragma: no cover - CLI feedback only
            print(f"Error while fetching page {page}: {exc}")
            return

        html_text = Path(saved_path).read_text(encoding="utf-8", errors="replace")
        root = parse_html(html_text)
        page_links = _collect_listing_links(root)

        if not page_links:
            print(f"No listing links found on page {page}. Continuing.")
        else:
            new_for_page = 0
            for href, distance in page_links:
                absolute = _to_absolute(href)
                if absolute in seen_links:
                    continue
                seen_links.add(absolute)
                collected_links.append(absolute)
                entry: Dict[str, str] = {"url": absolute}
                if distance:
                    entry["Abstand"] = distance
                collected_metadata.append(entry)
                new_for_page += 1

            print(f"Processed page {page}: added {new_for_page} new link(s).")

        next_url = _find_next_page(root, page_url)
        if next_url is None:
            next_url = _with_page(url, page + 1)

        if next_url in visited_pages or next_url == page_url:
            print("No additional pages detected; stopping pagination.")
            break

        page += 1
        page_url = next_url

    if not collected_links:
        print("No listing links found in the provided pages.")
        return

    output_path = Path("links.txt")
    existing_links: List[str] = []
    if output_path.exists():
        for line in output_path.read_text(encoding="utf-8").splitlines():
            entry = line.strip()
            if entry.endswith(","):
                entry = entry[:-1].strip()
            if entry:
                existing_links.append(_to_absolute(entry))

    existing_set = set(existing_links)
    new_links = [href for href in collected_links if href not in existing_set]

    if not new_links:
        print("No new listing links found. Existing file left unchanged.")
        return

    combined_links = new_links + existing_links

    with output_path.open("w", encoding="utf-8") as handle:
        for href in combined_links:
            handle.write(f"{href},\n")

    metadata_path = Path("links_metadata.json")
    existing_metadata: List[Dict[str, str]] = []
    if metadata_path.exists():
        try:
            existing_data = json.loads(metadata_path.read_text(encoding="utf-8"))
            if isinstance(existing_data, list):
                for item in existing_data:
                    if isinstance(item, dict) and "url" in item:
                        existing_metadata.append({k: str(v) for k, v in item.items() if isinstance(k, str)})
        except json.JSONDecodeError:
            pass

    existing_index = {entry.get("url"): entry for entry in existing_metadata if "url" in entry}
    for entry in collected_metadata:
        url = entry["url"]
        if url not in existing_index:
            existing_metadata.append(entry)
            existing_index[url] = entry
        elif "Abstand" in entry and "Abstand" not in existing_index[url]:
            existing_index[url]["Abstand"] = entry["Abstand"]

    metadata_path.write_text(json.dumps(existing_metadata, ensure_ascii=False, indent=2), encoding="utf-8")

    print(
        f"Added {len(new_links)} new link(s). {len(combined_links)} total entries saved to {output_path}."
    )


if __name__ == "__main__":
    main()
