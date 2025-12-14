#!/usr/bin/env python3
"""Orchestrate scraping Kleinanzeigen listings into JSON batches."""
from __future__ import annotations

import json
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Dict, List, Sequence, Tuple

from extract_listing import build_listing, parse_html
from fetch_listing import download_listing

WEBHOOK_URL = "https://cbarank0247.app.n8n.cloud/webhook-test/9b4a6bea-3f62-43b9-a031-742cbda93b0f"
BATCH_SIZE = 500


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


def _load_distances(file_path: Path) -> Dict[str, str]:
    if not file_path.exists():
        return {}

    try:
        raw = json.loads(file_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}

    distances: Dict[str, str] = {}
    if isinstance(raw, list):
        for entry in raw:
            if not isinstance(entry, dict):
                continue
            url = entry.get("url")
            distance = entry.get("Abstand")
            if isinstance(url, str) and isinstance(distance, str) and distance.strip():
                distances[url.strip()] = distance.strip()

    return distances


def _print_progress(prefix: str, current: int, total: int) -> None:
    """Render a single-line progress status like ``prefix: current/total``."""

    message = f"{prefix}: {current}/{total}"
    # Pad with spaces so shorter updates overwrite previous text.
    sys.stdout.write("\r" + message + " " * 10)
    sys.stdout.flush()


def _download_links(
    links: Sequence[str], prefix: str
) -> Tuple[List[Tuple[str, Path]], List[Tuple[str, str]]]:
    total = len(links)
    saved: List[Tuple[str, Path]] = []
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
            saved.append((link, path))

        _print_progress(prefix, index, total)

    print()
    return saved, failures


def _extract_from_html(html_path: Path, distance: str | None = None) -> dict:
    html = html_path.read_text(encoding="utf-8", errors="replace")
    root = parse_html(html)
    data = build_listing(root)
    if distance:
        data["Abstand"] = distance
    data["source_html"] = str(html_path)
    return data


def _send_batch(batch: List[dict], batch_index: int, webhook_url: str) -> Tuple[bool, str]:
    payload = {
        "batch_index": batch_index,
        "count": len(batch),
        "listings": batch,
    }
    data = json.dumps(payload).encode("utf-8")

    request = urllib.request.Request(
        webhook_url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            status = response.getcode()
            if 200 <= status < 300:
                return True, ""
            return False, f"HTTP durum kodu {status}"
    except urllib.error.HTTPError as exc:
        return False, f"HTTPError: {exc.code} - {exc.reason}"
    except urllib.error.URLError as exc:
        return False, f"URLError: {exc.reason}"
    except TimeoutError:
        return False, "Zaman aşımı"


def _process_listings(
    html_paths: Sequence[Tuple[str, Path]],
    distances: Dict[str, str],
    webhook_url: str,
    prefix: str,
) -> Tuple[int, List[Tuple[str, str]], List[int], List[Tuple[int, str]]]:
    total = len(html_paths)
    processed = 0
    failures: List[Tuple[str, str]] = []
    sent_batches: List[int] = []
    webhook_failures: List[Tuple[int, str]] = []

    if total == 0:
        return processed, failures, sent_batches, webhook_failures

    batch: List[dict] = []
    batch_index = 1

    _print_progress(prefix, 0, total)

    for index, (link, html_path) in enumerate(html_paths, 1):
        try:
            listing = _extract_from_html(html_path, distances.get(link))
        except Exception as exc:  # pragma: no cover - defensive
            failures.append((str(html_path), str(exc)))
        else:
            batch.append(listing)
            processed += 1

        _print_progress(prefix, index, total)

        if len(batch) == BATCH_SIZE:
            print()
            success, error_message = _send_batch(batch, batch_index, webhook_url)
            if success:
                sent_batches.append(batch_index)
                print(
                    f"    -> Paket {batch_index:03d} webhook'a gönderildi "
                    f"({processed} ilan işlendi)"
                )
            else:
                webhook_failures.append((batch_index, error_message))
                print(
                    f"    -> Paket {batch_index:03d} gönderilemedi: {error_message} "
                    f"({processed} ilan işlendi)"
                )
            batch = []
            batch_index += 1

            if index < total:
                print("    -> 500 kayıt gönderildi, 2 dakika bekleniyor...")
                time.sleep(120)

    print()

    if batch:
        success, error_message = _send_batch(batch, batch_index, webhook_url)
        if success:
            sent_batches.append(batch_index)
            print(
                f"    -> Paket {batch_index:03d} webhook'a gönderildi "
                f"({len(batch)} ilan içeriyor)"
            )
        else:
            webhook_failures.append((batch_index, error_message))
            print(
                f"    -> Paket {batch_index:03d} gönderilemedi: {error_message} "
                f"({len(batch)} ilan içeriyor)"
            )

    return processed, failures, sent_batches, webhook_failures


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

    distances = _load_distances(Path("links_metadata.json"))

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

    extraction_prefix = (
        f"[3/{total_stages}] JSON verileri oluşturuluyor ({len(saved_html)} dosya)"
    )
    processed, extraction_failures, sent_batches, webhook_failures = _process_listings(
        saved_html, distances, WEBHOOK_URL, extraction_prefix
    )

    print(f"    {processed} ilan başarıyla işlendi.")

    if extraction_failures:
        print(f"    {len(extraction_failures)} HTML işlenemedi:")
        for path, message in extraction_failures[:3]:
            print(f"      - {path}: {message}")
        if len(extraction_failures) > 3:
            print(f"      ... {len(extraction_failures) - 3} ek hata")

    if sent_batches:
        print("    Webhook'a gönderilen paketler:")
        for batch_index in sent_batches:
            print(f"      - Paket {batch_index:03d}")
    if webhook_failures:
        print("    Webhook'a iletilemeyen paketler:")
        for batch_index, message in webhook_failures:
            print(f"      - Paket {batch_index:03d}: {message}")

    elapsed_seconds = time.perf_counter() - start_time
    total_seconds = round(elapsed_seconds)
    minutes, seconds = divmod(total_seconds, 60)
    print(
        "Tüm linkler işlendi, görev tamamlandı. "
        f"Süre: {minutes} dakika {seconds} saniye."
    )


if __name__ == "__main__":
    main()
