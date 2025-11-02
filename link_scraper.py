#!/usr/bin/env python3
"""Fetch a Kleinanzeigen search page and extract listing links."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Iterable, List, Set

from extract_listing import Node, find_by_id, parse_html
from fetch_listing import download_listing


def _iter_articles(node: Node) -> Iterable[Node]:
    """Yield every ``<article>`` descendant of ``node``."""
    if node.tag == "article":
        yield node
    for child in node.children:
        yield from _iter_articles(child)


def _collect_listing_links(root: Node) -> List[str]:
    """Return the ``data-href`` values for the result articles."""
    container = find_by_id(root, "srchrslt-adtable")
    if container is None:
        return []

    links: List[str] = []
    seen: Set[str] = set()
    for item in container.children:
        if item.tag != "li":
            continue
        for article in _iter_articles(item):
            href = article.attrs.get("data-href")
            if href and href not in seen:
                seen.add(href)
                links.append(href)
            # Only keep the first article per list item to avoid duplicates.
            break

    return links


def _with_page(url: str, page: int) -> str:
    """Return ``url`` with the first ``seite:`` occurrence replaced by ``page``."""

    def _repl(match: re.Match) -> str:
        return f"{match.group(1)}{page}"

    updated = re.sub(r"(seite:)(\d+)", _repl, url, count=1)
    return updated if updated != url or page == 1 else url


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
    seen_links: Set[str] = set()

    previous_url = None
    for page in range(1, 21):
        page_url = _with_page(url, page)

        if page > 1 and page_url == previous_url:
            print("No additional pages detected; stopping pagination.")
            break

        previous_url = page_url

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
            continue

        new_for_page = 0
        for href in page_links:
            absolute = _to_absolute(href)
            if absolute in seen_links:
                continue
            seen_links.add(absolute)
            collected_links.append(absolute)
            new_for_page += 1

        print(f"Processed page {page}: added {new_for_page} new link(s).")

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

    print(
        f"Added {len(new_links)} new link(s). {len(combined_links)} total entries saved to {output_path}."
    )


if __name__ == "__main__":
    main()
