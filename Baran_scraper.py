#!/usr/bin/env python3
"""Orchestrate scraping Kleinanzeigen listings into JSON batches."""
from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path
from typing import List, Sequence, Tuple

from extract_listing import build_listing, parse_html
from fetch_listing import download_listing


def _run_link_scraper(url: str) -> str:
    """Execute ``link_scraper.py`` and return its final status line."""

    result = subprocess.run(
        [sys.executable, "link_scraper.py"],
        input=f"{url}\n",
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )

    output = (result.stdout or "").splitlines()

    if result.returncode != 0:  # pragma: no cover - CLI feedback only
        combined = "\n".join(output).strip()
        if combined:
            combined = f"\n{combined}"
        raise RuntimeError(
            "link_scraper.py başarısız oldu; çıktı kaydedilemedi" + combined
        )

    for line in reversed(output):
        stripped = line.strip()
        if stripped:
            return stripped

    return "link_scraper tamamlandı."


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


def _print_progress(prefix: str, current: int, total: int) -> None:
    """Render a single-line progress status like ``prefix: current/total``."""

    message = f"{prefix}: {current}/{total}"
    # Pad with spaces so shorter updates overwrite previous text.
    sys.stdout.write("\r" + message + " " * 10)
    sys.stdout.flush()


def _download_links(links: Sequence[str], prefix: str) -> Tuple[List[Path], List[Tuple[str, str]]]:
    total = len(links)
    saved: List[Path] = []
    failures: List[Tuple[str, str]] = []

    if total == 0:
        return saved, failures

    _print_progress(prefix, 0, total)

    for index, link in enumerate(links, 1):
        try:
            path = download_listing(link)
        except Exception as exc:  # pragma: no cover - network variability
            failures.append((link, str(exc)))
        else:
            saved.append(path)

        _print_progress(prefix, index, total)

    print()
    return saved, failures


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


def _process_listings(
    html_paths: Sequence[Path],
    output_dir: Path,
    prefix: str,
) -> Tuple[int, List[Tuple[str, str]], List[Path]]:
    total = len(html_paths)
    processed = 0
    failures: List[Tuple[str, str]] = []
    created_files: List[Path] = []

    if total == 0:
        return processed, failures, created_files

    batch: List[dict] = []
    batch_index = 1

    _print_progress(prefix, 0, total)

    for index, html_path in enumerate(html_paths, 1):
        try:
            listing = _extract_from_html(html_path)
        except Exception as exc:  # pragma: no cover - defensive
            failures.append((str(html_path), str(exc)))
        else:
            batch.append(listing)
            processed += 1

        _print_progress(prefix, index, total)

        if len(batch) == 27:
            print()
            output_path = _write_batch(batch, batch_index, output_dir)
            created_files.append(output_path)
            print(
                f"    -> Paket {batch_index:03d} kaydedildi: {output_path.name} "
                f"({processed} ilan işlendi)"
            )
            batch = []
            batch_index += 1

    print()

    if batch:
        output_path = _write_batch(batch, batch_index, output_dir)
        created_files.append(output_path)
        print(
            f"    -> Paket {batch_index:03d} kaydedildi: {output_path.name} "
            f"({len(batch)} ilan içeriyor)"
        )

    return processed, failures, created_files


def main() -> None:
    url = input("Kleinanzeigen arama linki: ").strip()
    if not url:
        print("Geçerli bir link girilmedi. Çıkılıyor.")
        return

    start_time = time.perf_counter()

    total_stages = 3

    print(f"[1/{total_stages}] Linkler toplanıyor...")

    try:
        scraper_summary = _run_link_scraper(url)
    except RuntimeError as exc:
        print(f"    Hata: {exc}")
        return

    if scraper_summary:
        print(f"    {scraper_summary}")

    links_file = Path("links.txt")
    try:
        links = _read_links(links_file)
    except FileNotFoundError as exc:
        print(exc)
        return

    if not links:
        print("links.txt içinde işlenecek link bulunamadı.")
        return

    download_prefix = f"[2/{total_stages}] HTML indiriliyor ({len(links)} link)"
    saved_html, download_failures = _download_links(links, download_prefix)

    if download_failures:
        print(f"    {len(download_failures)} link indirilemedi:")
        for link, message in download_failures[:3]:
            print(f"      - {link}: {message}")
        if len(download_failures) > 3:
            print(f"      ... {len(download_failures) - 3} ek hata")

    if not saved_html:
        print("Herhangi bir HTML indirilemedi.")
        return

    print(f"    {len(saved_html)} HTML dosyası indirildi.")

    output_dir = Path("Scraped_Daten")
    extraction_prefix = (
        f"[3/{total_stages}] JSON verileri oluşturuluyor ({len(saved_html)} dosya)"
    )
    processed, extraction_failures, created_files = _process_listings(
        saved_html, output_dir, extraction_prefix
    )

    print(f"    {processed} ilan başarıyla işlendi.")

    if extraction_failures:
        print(f"    {len(extraction_failures)} HTML işlenemedi:")
        for path, message in extraction_failures[:3]:
            print(f"      - {path}: {message}")
        if len(extraction_failures) > 3:
            print(f"      ... {len(extraction_failures) - 3} ek hata")

    if created_files:
        print("    Oluşturulan JSON dosyaları:")
        for json_path in created_files:
            print(f"      - {json_path}")

    elapsed_seconds = time.perf_counter() - start_time
    total_seconds = round(elapsed_seconds)
    minutes, seconds = divmod(total_seconds, 60)
    print(
        "Tüm linkler işlendi, görev tamamlandı. "
        f"Süre: {minutes} dakika {seconds} saniye."
    )


if __name__ == "__main__":
    main()
