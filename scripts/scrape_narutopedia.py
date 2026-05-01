"""Scraper exhaustif Narutopedia via la MediaWiki API.

Strategie :
- Liste tous les articles (namespace 0) via list=allpages avec pagination apcontinue.
- Recupere wikitext + categories en batch via prop=revisions|categories (jusqu'a 50 pageids par requete).
- Cache local agressif sous data/raw/narutopedia/pages/<pageid>_<safe_title>.wikitext.
- Chaque page produit aussi data/raw/narutopedia/meta/<pageid>.json avec ses categories.
- Trace globale data/raw/narutopedia/_trace.jsonl (URL, timestamp, status, sha256, size).
- Reprise sur erreur : si la cache existe, on ne refetch pas.

Usage :
  python scripts/scrape_narutopedia.py
  python scripts/scrape_narutopedia.py --limit 1000
  python scripts/scrape_narutopedia.py --force --namespaces 0,4
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import httpx
import typer

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from shinobi.config import settings  # noqa: E402
from shinobi.logging_setup import configure_logging, get_logger  # noqa: E402

configure_logging()
logger = get_logger("scrape_narutopedia")

API_URL = "https://naruto.fandom.com/api.php"
DEFAULT_HEADERS = {
    "User-Agent": settings.scraper_user_agent,
    "Accept": "application/json",
}

# Limites prudentes vs spec MediaWiki (50 par defaut pour non-bots, 500 pour bots).
PAGEIDS_BATCH_SIZE = 50
ALLPAGES_BATCH_SIZE = 500
DEFAULT_DELAY_BETWEEN_REQUESTS = 1.5  # secondes
MAX_PARALLEL_REQUESTS = 1  # politesse stricte, file unique

INVALID_FILENAME = re.compile(r"[^a-zA-Z0-9._\-]+")


@dataclass
class ScrapeStats:
    discovered: int = 0
    fetched: int = 0
    skipped: int = 0
    errors: int = 0


def safe_filename(title: str, max_len: int = 80) -> str:
    """Convertit un titre en nom de fichier sur."""
    safe = INVALID_FILENAME.sub("_", title)
    return safe[:max_len].strip("_") or "page"


class NarutopediaScraper:
    """Scraper API politely paced."""

    def __init__(
        self,
        out_dir: Path,
        *,
        delay_seconds: float,
        force: bool,
        namespaces: tuple[int, ...],
        limit: int | None,
    ) -> None:
        self.out_dir = out_dir
        self.pages_dir = out_dir / "pages"
        self.meta_dir = out_dir / "meta"
        self.trace_path = out_dir / "_trace.jsonl"
        self.delay = delay_seconds
        self.force = force
        self.namespaces = namespaces
        self.limit = limit
        self.stats = ScrapeStats()
        self.last_request_at: float = 0.0
        self.lock = asyncio.Lock()

        for d in (self.pages_dir, self.meta_dir):
            d.mkdir(parents=True, exist_ok=True)

    async def run(self) -> ScrapeStats:
        async with httpx.AsyncClient(headers=DEFAULT_HEADERS, timeout=60.0) as client:
            page_titles = await self._discover_pages(client)
            logger.info("scrape_discovered", total=len(page_titles))
            self.stats.discovered = len(page_titles)
            if self.limit:
                page_titles = page_titles[: self.limit]

            for batch_start in range(0, len(page_titles), PAGEIDS_BATCH_SIZE):
                batch = page_titles[batch_start : batch_start + PAGEIDS_BATCH_SIZE]
                pending = self._select_pending(batch)
                if not pending:
                    self.stats.skipped += len(batch)
                    continue
                try:
                    await self._fetch_batch(client, pending)
                except httpx.HTTPError as exc:
                    logger.warning("scrape_batch_error", error=str(exc), size=len(pending))
                    self.stats.errors += len(pending)
                if (batch_start // PAGEIDS_BATCH_SIZE) % 5 == 0:
                    logger.info(
                        "scrape_progress",
                        fetched=self.stats.fetched,
                        skipped=self.stats.skipped,
                        errors=self.stats.errors,
                        total=self.stats.discovered,
                    )
        return self.stats

    async def _discover_pages(self, client: httpx.AsyncClient) -> list[dict[str, object]]:
        """Liste tous les pageids dans les namespaces cibles."""
        pages: list[dict[str, object]] = []
        for ns in self.namespaces:
            ap_continue: str | None = None
            while True:
                params: dict[str, str | int] = {
                    "action": "query",
                    "list": "allpages",
                    "aplimit": ALLPAGES_BATCH_SIZE,
                    "apnamespace": ns,
                    "format": "json",
                    "formatversion": "2",
                }
                if ap_continue:
                    params["apcontinue"] = ap_continue
                await self._respect_delay()
                r = await client.get(API_URL, params=params)
                r.raise_for_status()
                data = r.json()
                for p in data.get("query", {}).get("allpages", []):
                    pages.append({"pageid": p["pageid"], "title": p["title"], "ns": ns})
                cont = data.get("continue", {})
                ap_continue = cont.get("apcontinue")
                if not ap_continue:
                    break
        return pages

    def _select_pending(self, batch: list[dict[str, object]]) -> list[dict[str, object]]:
        """Filtre les pages deja en cache."""
        if self.force:
            return batch
        pending = []
        for p in batch:
            wikitext_path = self._wikitext_path(int(p["pageid"]), str(p["title"]))
            meta_path = self.meta_dir / f"{p['pageid']}.json"
            if wikitext_path.exists() and meta_path.exists():
                self.stats.skipped += 1
                continue
            pending.append(p)
        return pending

    async def _fetch_batch(
        self,
        client: httpx.AsyncClient,
        batch: list[dict[str, object]],
    ) -> None:
        """Fetch wikitext + categories pour un batch de pageids."""
        pageids = "|".join(str(p["pageid"]) for p in batch)
        params = {
            "action": "query",
            "pageids": pageids,
            "prop": "revisions|categories",
            "rvprop": "content|timestamp",
            "rvslots": "main",
            "cllimit": "500",
            "format": "json",
            "formatversion": "2",
        }
        await self._respect_delay()
        r = await client.get(API_URL, params=params)
        r.raise_for_status()
        data = r.json()
        pages_resp = data.get("query", {}).get("pages", [])
        title_lookup = {int(p["pageid"]): str(p["title"]) for p in batch}

        for page in pages_resp:
            pageid = page.get("pageid")
            if pageid is None:
                continue
            title = page.get("title") or title_lookup.get(int(pageid), "unknown")
            revs = page.get("revisions") or []
            if not revs:
                continue
            wikitext = (revs[0].get("slots") or {}).get("main", {}).get("content", "")
            if not wikitext:
                continue
            cats = [c.get("title", "") for c in (page.get("categories") or [])]
            self._write_outputs(int(pageid), title, wikitext, cats)
            self._append_trace(
                url=f"{API_URL}?action=query&pageids={pageid}",
                pageid=int(pageid),
                title=title,
                size=len(wikitext),
                sha=hashlib.sha256(wikitext.encode("utf-8")).hexdigest(),
            )
            self.stats.fetched += 1

    def _write_outputs(
        self,
        pageid: int,
        title: str,
        wikitext: str,
        categories: list[str],
    ) -> None:
        wikitext_path = self._wikitext_path(pageid, title)
        meta_path = self.meta_dir / f"{pageid}.json"
        wikitext_path.parent.mkdir(parents=True, exist_ok=True)
        wikitext_path.write_text(wikitext, encoding="utf-8")
        meta = {
            "pageid": pageid,
            "title": title,
            "categories": categories,
            "wikitext_size": len(wikitext),
            "wikitext_sha256": hashlib.sha256(wikitext.encode("utf-8")).hexdigest(),
        }
        meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

    def _wikitext_path(self, pageid: int, title: str) -> Path:
        return self.pages_dir / f"{pageid}_{safe_filename(title)}.wikitext"

    def _append_trace(
        self,
        *,
        url: str,
        pageid: int,
        title: str,
        size: int,
        sha: str,
    ) -> None:
        with self.trace_path.open("a", encoding="utf-8") as f:
            f.write(
                json.dumps(
                    {
                        "url": url,
                        "pageid": pageid,
                        "title": title,
                        "fetched_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                        "size_bytes": size,
                        "sha256": sha,
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )

    async def _respect_delay(self) -> None:
        async with self.lock:
            now = time.monotonic()
            wait = self.delay - (now - self.last_request_at)
            if wait > 0:
                await asyncio.sleep(wait)
            self.last_request_at = time.monotonic()


cli = typer.Typer(add_completion=False, no_args_is_help=False)


@cli.command()
def scrape(
    limit: int | None = typer.Option(None, help="Limite le nombre de pages (debug)."),
    force: bool = typer.Option(False, help="Refetch meme si la cache existe."),
    namespaces: str = typer.Option(
        "0", help="Namespaces a parcourir (CSV ; 0=articles principaux)."
    ),
    delay: float = typer.Option(
        DEFAULT_DELAY_BETWEEN_REQUESTS, help="Delai minimum entre requetes en secondes."
    ),
    out: str = typer.Option(
        "data/raw/narutopedia",
        help="Repertoire de sortie relatif au projet.",
    ),
) -> None:
    """Scrape Narutopedia exhaustivement via la MediaWiki API."""
    out_dir = (
        (settings.canonical_data_dir.parent / "raw" / "narutopedia")
        if out == "data/raw/narutopedia"
        else (
            Path(out)
            if Path(out).is_absolute()
            else (settings.canonical_data_dir.parent.parent / out)
        )
    )
    out_dir.mkdir(parents=True, exist_ok=True)
    ns_tuple = tuple(int(x.strip()) for x in namespaces.split(",") if x.strip())
    scraper = NarutopediaScraper(
        out_dir=out_dir,
        delay_seconds=delay,
        force=force,
        namespaces=ns_tuple,
        limit=limit,
    )
    logger.info(
        "scrape_start",
        out=str(out_dir),
        namespaces=ns_tuple,
        delay=delay,
        force=force,
        limit=limit,
    )
    stats = asyncio.run(scraper.run())
    logger.info(
        "scrape_done",
        discovered=stats.discovered,
        fetched=stats.fetched,
        skipped=stats.skipped,
        errors=stats.errors,
    )
    print(
        f"Discovered: {stats.discovered}, fetched: {stats.fetched}, "
        f"skipped: {stats.skipped}, errors: {stats.errors}"
    )


if __name__ == "__main__":
    cli()
