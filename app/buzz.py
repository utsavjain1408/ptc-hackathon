"""Collect public LinkedIn posts about the conference ("buzz") and who wrote them.

1. SERP searches for LinkedIn posts mentioning GitHub Universe (1 credit each). A post's activity ID encodes
   its timestamp, so only posts from this year's run-up are kept, not last year's event.
2. Scrape each author's profile once (1 credit each) for company and schools, so the web app can tell a user
   which coworkers, fellow alumni or people they engage with are posting about the conference.
3. Load authors and posts into the graph: (:Person {buzz: true})-[:POSTED]->(:Post {conference: true}).

Output: data/linkedin/conference_posts.json and conference_authors.json (gitignored).

Run: uv run python -m app.buzz
"""
import json
import re
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

from app import seed
from app.db import get_graph
from app.linkedin import scrape_profiles

OUT = Path(__file__).resolve().parent.parent / "data" / "linkedin"
POSTS_PATH = OUT / "conference_posts.json"
AUTHORS_PATH = OUT / "conference_authors.json"
SINCE = datetime(2026, 3, 1, tzinfo=timezone.utc)  # Universe 2026 run-up; Universe 2025 was Oct 2025
QUERIES = [
    'site:linkedin.com/posts "GitHub Universe" 2026',
    'site:linkedin.com/posts "GitHub Universe"',
    'site:linkedin.com/posts "#GitHubUniverse"',
    'site:linkedin.com/posts "Universe 2026" GitHub',
    'site:linkedin.com/posts "GitHub Universe" speaking',
    'site:linkedin.com/posts "GitHub Universe" San Francisco',
    'site:linkedin.com/posts "#GitHubUniverse2026"',
    'site:linkedin.com/posts "speaking at GitHub Universe"',
    'site:linkedin.com/posts "see you at GitHub Universe"',
    'site:linkedin.com/posts "GitHub Universe" Fort Mason',
]
PAGES = 3
POST_URL = re.compile(r"linkedin\.com/posts/([^_/?#]+)_[^?#]*?activity-(\d{19})")


def post_date(activity_id: str) -> datetime:
    """LinkedIn activity IDs carry their creation time in the top 41 bits (ms since epoch)."""
    return datetime.fromtimestamp((int(activity_id) >> 22) / 1000, timezone.utc)


def author_slug(link: str) -> str | None:
    """Profile slug of a post's author, e.g. /posts/jane-doe_topic-activity-123 -> jane-doe."""
    m = POST_URL.search(link or "")
    return m.group(1).lower() if m else None


def _search(query: str, page: int) -> list[dict]:
    for _ in range(3):
        proc = subprocess.run(["bdata", "search", query, "--country", "us", "--page", str(page), "--json"],
                              capture_output=True, text=True)
        if proc.returncode == 0:
            return json.loads(proc.stdout).get("organic", [])
    return []


def collect() -> dict:
    posts = json.loads(POSTS_PATH.read_text()) if POSTS_PATH.exists() else {}
    jobs = [(q, p) for q in QUERIES for p in range(PAGES)]
    with ThreadPoolExecutor(max_workers=8) as pool:
        for results in pool.map(lambda qp: _search(*qp), jobs):
            for r in results:
                m = POST_URL.search(r.get("link", ""))
                if not m or post_date(m.group(2)) < SINCE:
                    continue
                link = r["link"].split("?")[0]
                posts[link] = {
                    "link": link,
                    "author_slug": m.group(1).lower(),
                    "author_name": (r.get("source") or "").split("·")[-1].strip() or None,
                    "title": r.get("title"),
                    "text": r.get("description") or "",
                    "date": post_date(m.group(2)).date().isoformat(),
                }
    # Speakers' and attendees' activity already holds posts by people in their networks; the ones that mention
    # Universe are buzz too, at no extra cost (5x what search surfaces).
    profiles = list(json.loads((OUT / "profiles.json").read_text()).values())
    profiles += [json.loads(p.read_text()) for p in (OUT / "attendees").glob("*.json")]
    for prof in profiles:
        prof = prof[0] if isinstance(prof, list) else prof
        for a in prof.get("activity") or []:
            m, text = POST_URL.search(a.get("link", "")), a.get("title") or ""
            if m and re.search(r"universe", text, re.I) and post_date(m.group(2)) >= SINCE:
                link = a["link"].split("?")[0]
                posts.setdefault(link, {"link": link, "author_slug": m.group(1).lower(), "author_name": None,
                                        "title": None, "text": text[:1000], "date": post_date(m.group(2)).date().isoformat()})

    POSTS_PATH.write_text(json.dumps(posts, indent=1, ensure_ascii=False))
    print(f"{len(posts)} conference posts from this year by {len({p['author_slug'] for p in posts.values()})} authors")
    return posts


def scrape_authors(posts: dict, batch_size: int = 10) -> dict:
    authors = json.loads(AUTHORS_PATH.read_text()) if AUTHORS_PATH.exists() else {}
    todo = sorted({p["author_slug"] for p in posts.values()} - set(authors))
    batches = [todo[i:i + batch_size] for i in range(0, len(todo), batch_size)]
    print(f"Scraping {len(todo)} author profiles in {len(batches)} batches...", flush=True)

    def run(batch):
        try:
            return batch, scrape_profiles([f"https://www.linkedin.com/in/{s}" for s in batch])
        except Exception as e:
            print(f"  batch failed: {e}")
            return batch, []

    with ThreadPoolExecutor(max_workers=4) as pool:
        for n, fut in enumerate(as_completed([pool.submit(run, b) for b in batches]), 1):  # report as each finishes
            batch, records = fut.result()
            by_slug = {}
            for rec in records:
                url = (rec.get("input") or {}).get("url") or rec.get("input_url") or ""
                by_slug[url.rstrip("/").rsplit("/", 1)[-1].lower()] = rec
            for slug in batch:
                rec = by_slug.get(slug, {})
                authors[slug] = {"error": rec.get("error") or "no record"} if rec.get("error") or not rec.get("name") \
                    else {"name": rec["name"], **{k: v for k, v in seed._profile(rec, {}).items() if k != "posts"}}
            AUTHORS_PATH.write_text(json.dumps(authors, indent=1, ensure_ascii=False))
            print(f"  {n}/{len(batches)} batches done", flush=True)
    return authors


def load(g, posts: dict, authors: dict):
    """Authors and their conference posts into the graph (speakers who posted keep their speaker node)."""
    people = []
    for slug, a in authors.items():
        mine = [p for p in posts.values() if p["author_slug"] == slug]
        name = a.get("name") or (mine[0]["author_name"] if mine else None)
        if name and mine:
            people.append({"name": name, "slug": slug, "linkedin": a.get("linkedin") or f"https://www.linkedin.com/in/{slug}",
                           "company": a.get("company"), "schools": a.get("schools", []),
                           "posts": [{"link": p["link"], "text": f"{p['title'] or ''} {p['text']}".strip(),
                                      "date": p["date"]} for p in mine]})
    g.query(
        """
        UNWIND $people AS p
        MERGE (a:Person {name: p.name})
        SET a.buzz = true, a.linkedin_slug = p.slug, a.linkedin = coalesce(a.linkedin, p.linkedin)
        FOREACH (_ IN CASE WHEN p.company IS NULL THEN [] ELSE [1] END |
            MERGE (c:Company {name: p.company}) MERGE (a)-[:WORKS_AT]->(c))
        FOREACH (s IN p.schools | MERGE (sc:School {name: s}) MERGE (a)-[:ALUMNI_OF]->(sc))
        FOREACH (x IN p.posts |
            MERGE (ps:Post {link: x.link}) SET ps.text = x.text, ps.date = x.date, ps.conference = true
            MERGE (a)-[:POSTED]->(ps))
        """,
        {"people": people},
    )
    print(f"Loaded {len(people)} authors and {sum(len(p['posts']) for p in people)} conference posts into the graph")


def main():
    posts = collect()
    authors = scrape_authors(posts)
    load(get_graph(), posts, authors)


if __name__ == "__main__":
    main()
