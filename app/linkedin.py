"""Find speakers' LinkedIn profiles via Bright Data and scrape them.

1. SERP search `site:linkedin.com/in "<name>" <company>` per speaker (1 credit per try, up to 3 tries), keep
   the first linkedin.com/in/ result whose title has the speaker's first and last name.
2. Scrape found profiles in small parallel batches with the LinkedIn people profiles scraper (1 credit per
   record); each finished batch is saved and added to the graph.

Output goes to data/linkedin/ (gitignored: personal data). Hand-checked URLs go in data/linkedin/overrides.json.

Run: uv run python -m app.linkedin [--limit 10] [--retry]
"""
import argparse
import json
import re
import subprocess
import threading
import unicodedata
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from app.db import get_graph
from app.seed import add_speaker_profiles

OUT = Path(__file__).resolve().parent.parent / "data" / "linkedin"
PROFILES_DATASET = "gd_l1viktl72bvl7bjuj0"
PROFILE_URL = re.compile(r"^https?://([a-z]{2,3}\.)?linkedin\.com/in/[^/?#]+")


def speakers(limit: int | None) -> list[dict]:
    """Speakers ordered by number of sessions (most first), then name."""
    rows = get_graph().ro_query(
        "MATCH (p:Person)-[:SPEAKS_AT]->(t:Talk) OPTIONAL MATCH (p)-[:WORKS_AT]->(c:Company) "
        "WITH p, c, count(DISTINCT t) AS n "
        "RETURN p.name, c.name, n ORDER BY n DESC, p.name"
        + (f" LIMIT {int(limit)}" if limit else "")
    ).result_set
    return [{"name": n, "company": c, "sessions": s} for n, c, s in rows]


def name_matches(speaker: str, text: str | None) -> bool:
    """True if the speaker's first and last name both appear in text (accents and case ignored)."""
    def norm(s):
        return unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower()
    tokens = norm(speaker).split()
    words = set(re.findall(r"[a-z]+", norm(text or "")))
    return tokens[0] in words and tokens[-1] in words


def find_profile_url(name: str, company: str | None) -> str | None:
    """First linkedin.com/in/ result whose title carries the speaker's name; None if there isn't one."""
    query = f'site:linkedin.com/in "{name}" {company or ""}'.strip()
    # SERP calls fail intermittently and results vary per call (some come back as google.com/goto
    # redirects), so try a few times before giving up.
    for _ in range(3):
        proc = subprocess.run(["bdata", "search", query, "--country", "us", "--json"], capture_output=True, text=True)
        if proc.returncode != 0:
            continue
        for result in json.loads(proc.stdout).get("organic", []):
            m = PROFILE_URL.match(result.get("link", ""))
            if m and name_matches(name, result.get("title")):
                return m.group(0)
    return None


def scrape_profiles(urls: list[str]) -> list[dict]:
    out = subprocess.run(
        ["bdata", "pipelines", "run", PROFILES_DATASET, "collect_by_url", *urls, "--json", "--timeout", "1800"],
        capture_output=True, text=True, check=True,
    ).stdout
    return json.loads(out[out.index("["):])  # skip the CLI's progress lines


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, help="only the top N speakers by session count")
    ap.add_argument("--retry", action="store_true", help="search again for speakers that were not found or mismatched")
    ap.add_argument("--workers", type=int, default=8, help="parallel searches")
    ap.add_argument("--batch-size", type=int, default=10, help="profiles per scrape job")
    ap.add_argument("--scrape-workers", type=int, default=4, help="scrape jobs running at once")
    args = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)

    urls_path = OUT / "urls.json"
    profiles_path = OUT / "profiles.json"
    found = json.loads(urls_path.read_text()) if urls_path.exists() else {}
    profiles = json.loads(profiles_path.read_text()) if profiles_path.exists() else {}

    # Drop scraped profiles that belong to someone else (from runs before name checks existed).
    for name in [n for n, p in profiles.items() if not name_matches(n, p.get("name"))]:
        print(f"Dropping mismatched profile for {name}: {profiles[name].get('name')}")
        del profiles[name]
        found[name]["status"] = "mismatch"

    # Hand-checked URLs for speakers whose profiles search engines don't surface: {"Name": "url"}.
    overrides_path = OUT / "overrides.json"
    overrides = json.loads(overrides_path.read_text()) if overrides_path.exists() else {}
    for name, url in overrides.items():
        if found.get(name, {}).get("url") != url:
            found[name] = {**found.get(name, {"name": name}), "url": url, "status": "ok", "source": "override"}
            profiles.pop(name, None)

    def needs_search(sp):
        prev = found.get(sp["name"])
        if sp["name"] in overrides or prev and not args.retry:
            return False
        return not (prev and prev.get("status", "ok") == "ok" and prev.get("url"))

    def lookup(sp):
        sp["url"] = find_profile_url(sp["name"], sp["company"])
        sp["status"] = "ok" if sp["url"] else "not_found"
        return sp

    # Searches are slow (~10s each, up to 3 tries), so run several at once; save after each.
    lock = threading.Lock()
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        for sp in pool.map(lookup, [sp for sp in speakers(args.limit) if needs_search(sp)]):
            with lock:
                found[sp["name"]] = sp
                print(f"{sp['name']:24} {sp['company'] or '':18} {sp['url'] or 'NOT FOUND'}", flush=True)
                urls_path.write_text(json.dumps(found, indent=2))

    todo = {sp["url"]: name for name, sp in found.items()
            if sp.get("url") and sp.get("status", "ok") == "ok" and name not in profiles}
    if not todo:
        print(f"{len(profiles)} profiles saved to {profiles_path}")
        return

    # Small batches in parallel: one huge batch gives no progress and loses everything if interrupted.
    # Each finished batch is saved and added to the graph right away.
    urls = list(todo)
    batches = [urls[i:i + args.batch_size] for i in range(0, len(urls), args.batch_size)]
    print(f"Scraping {len(urls)} profiles in {len(batches)} batches of up to {args.batch_size}...", flush=True)
    graph = get_graph()

    def handle(batch_urls, records):
        added = {}
        for rec in records:
            name = todo.get(rec.get("input", {}).get("url")) or todo.get(rec.get("input_url"))
            if not name:
                continue
            if rec.get("error"):  # e.g. dead_page: "The profile is hidden or private."
                print(f"  Failed for {name}: {rec['error']} ({rec.get('error_code')})")
                found[name]["status"] = rec.get("error_code") or "error"
            elif name_matches(name, rec.get("name")):
                profiles[name] = added[name] = rec
            else:
                print(f"  Mismatch for {name}: scraped {rec.get('name')}")
                found[name]["status"] = "mismatch"
        urls_path.write_text(json.dumps(found, indent=2))
        profiles_path.write_text(json.dumps(profiles, indent=2))
        add_speaker_profiles(graph, added)
        return len(added)

    with ThreadPoolExecutor(max_workers=args.scrape_workers) as pool:
        futures = {pool.submit(scrape_profiles, b): b for b in batches}
        for done, fut in enumerate(as_completed(futures), 1):
            try:
                records = fut.result()
            except Exception as e:  # a failed batch is retried by simply running the script again
                print(f"Batch {done}/{len(batches)} failed: {e}", flush=True)
                continue
            with lock:
                n = handle(futures[fut], records)
                print(f"Batch {done}/{len(batches)}: {n}/{len(futures[fut])} profiles added "
                      f"({len(profiles)} total)", flush=True)
    print(f"{len(profiles)} profiles saved to {profiles_path}")


if __name__ == "__main__":
    main()
