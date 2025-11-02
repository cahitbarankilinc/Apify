#!/usr/bin/env python3
"""Orchestrate scraping Kleinanzeigen listings into JSON batches."""
from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Iterable, List

from extract_listing import build_listing, parse_html
from fetch_listing import download_listing


def _run_link_scraper(url: str) -> None:
    """Execute ``link_scraper.py`` and pass ``url`` to its prompt."""
    try:
        subprocess.run(
            [sys.executable, "link_scraper.py"],
            input=f"{url}\n",
            text=True,
            check=True,
        )
    except subprocess.CalledProcessError as exc:  # pragma: no cover - CLI feedback only
        raise RuntimeError(
            "link_scraper.py başarısız oldu; çıktı kaydedilemedi"
        ) from exc
    return None


def _read_links(file_path: Path) -> List[str]:
    if not file_path.exists():
        raise FileNotFoundError(f"{file_path} bulunamadı")
    links: List[str] = []
    for line in file_path.read_text(encoding="utf-8").splitlines():
        entry = line.strip()
        if not entry:
            continue
        if entry.endswith(","):
            entry = entry[:-1].strip()
        if entry:
            links.append(entry)
    return links


def _download_links(links: Iterable[str]) -> List[Path]:
    saved: List[Path] = []
    for link in links:
        try:
            path = download_listing(link)
        except Exception as exc:  # pragma: no cover - network variability
            print(f"{link} indirilemedi: {exc}")
            continue
        print(f"{link} indirildi -> {path}")
        saved.append(path)
    return saved


def _extract_from_html(html_path: Path) -> dict:
    html = html_path.read_text(encoding="utf-8", errors="replace")
    root = parse_html(html)
    data = build_listing(root)
    data["source_html"] = str(html_path)
    return data


def _write_batch(batch: List[dict], batch_index: int, output_dir: Path) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"batch_{batch_index:03d}.json"
    with output_path.open("w", encoding="utf-8") as handle:
        json.dump(batch, handle, ensure_ascii=False, indent=2)
    return output_path


def main() -> None:
    url = input("Kleinanzeigen arama linki: ").strip()
    if not url:
        print("Geçerli bir link girilmedi. Çıkılıyor.")
        return

    start_time = time.perf_counter()

    try:
        _run_link_scraper(url)
    except RuntimeError as exc:
        print(exc)
        return

    links_file = Path("links.txt")
    try:
        links = _read_links(links_file)
    except FileNotFoundError as exc:
        print(exc)
        return

    if not links:
        print("links.txt içinde işlenecek link bulunamadı.")
        return

    print(f"{len(links)} link bulundu. İndiriliyor...")
    saved_html = _download_links(links)
    if not saved_html:
        print("Herhangi bir HTML indirilemedi.")
        return

    output_dir = Path("Scraped_Daten")
    batch: List[dict] = []
    batch_index = 1
    processed = 0

    for html_path in saved_html:
        try:
            listing = _extract_from_html(html_path)
        except Exception as exc:  # pragma: no cover - defensive
            print(f"{html_path} işlenemedi: {exc}")
            continue

        batch.append(listing)
        processed += 1

        if len(batch) == 27:
            output_path = _write_batch(batch, batch_index, output_dir)
            print(
                f"{batch_index}. JSON dosyası oluşturuldu ({output_path}) - {processed} ilan işlendi."
            )
            batch = []
            batch_index += 1

    if batch:
        output_path = _write_batch(batch, batch_index, output_dir)
        print(
            f"{batch_index}. JSON dosyası oluşturuldu ({output_path}) - {len(batch)} ilan içeriyor."
        )

    elapsed_seconds = time.perf_counter() - start_time
    total_seconds = round(elapsed_seconds)
    minutes, seconds = divmod(total_seconds, 60)
    print(
        "Tüm linkler işlendi, görev tamamlandı. "
        f"Süre: {minutes} dakika {seconds} saniye."
    )


if __name__ == "__main__":
    main()
