"""Add a user to the conference graph from just their LinkedIn URL.

1. Scrape the profile with Bright Data (cached per profile in data/linkedin/attendees/).
2. Infer interests: tag their posts and about text with app.tagging, score topics by how they engaged.
3. Upsert them into the graph: company, schools, posts, (:Person)-[:INTERESTED_IN {score}]->(:Topic).
"""
import json
import re
from collections import defaultdict
from pathlib import Path

from openai import OpenAI

from app import seed, tagging
from app.linkedin import PROFILE_URL, scrape_profiles

ATTENDEES = Path(__file__).resolve().parent.parent / "data" / "linkedin" / "attendees"
# How much each kind of evidence says about someone's interests.
WEIGHTS = {"POSTED": 3, "SHARED": 2, "REACTED_TO": 1, "ABOUT": 4}


def profile_slug(url: str) -> str | None:
    m = PROFILE_URL.match(url.strip())
    return m.group(0).rstrip("/").rsplit("/", 1)[-1].lower() if m else None


def scrape(url: str) -> dict:
    """The user's LinkedIn record, from cache when we've scraped them before."""
    slug = profile_slug(url)
    for path in ATTENDEES.glob("*.json"):
        rec = json.loads(path.read_text())
        rec = rec[0] if isinstance(rec, list) else rec
        if slug in {profile_slug(rec.get("url") or ""), profile_slug(rec.get("input_url") or "")}:
            return rec
    records = scrape_profiles([f"https://www.linkedin.com/in/{slug}"])
    rec = records[0] if records else {}
    if rec.get("error") or not rec.get("name"):
        raise ValueError(rec.get("error") or "Bright Data returned no profile for that URL")
    ATTENDEES.mkdir(parents=True, exist_ok=True)
    (ATTENDEES / f"{slug}.json").write_text(json.dumps(rec, indent=2))
    return rec


def infer_interests(rec: dict, max_topics: int = 4) -> list[dict]:
    """[{topic, score, evidence: [post text...]}], strongest first."""
    posts = {a["link"]: (a.get("title") or "")[:1000] for a in rec.get("activity") or [] if a.get("link")}
    tagging.tag(posts, tagging.POST_CACHE, "Your posts")
    post_topics = tagging.post_topics()

    scores, evidence = defaultdict(int), defaultdict(list)
    for a in rec.get("activity") or []:
        rel = seed._engagement(a.get("interaction"))
        for t in post_topics.get(a.get("link"), []):
            scores[t] += WEIGHTS[rel]
            if rel != "REACTED_TO" and len(evidence[t]) < 2:
                evidence[t].append((a.get("title") or "")[:240])
    about = " ".join(filter(None, [rec.get("position"), rec.get("about")]))
    if about:
        for t in tagging._classify(OpenAI(), {"about": about}).get("about", []):
            scores[t] += WEIGHTS["ABOUT"]
            evidence[t].insert(0, f"Profile: {about[:240]}")

    ranked = sorted(scores.items(), key=lambda kv: -kv[1])
    if not ranked:
        return _interests_from_background(rec, max_topics)
    floor = max(2, ranked[0][1] * 0.25)  # drop topics with only a stray reaction behind them
    return [{"topic": t, "score": s, "evidence": evidence[t]} for t, s in ranked[:max_topics] if s >= floor]


def _interests_from_background(rec: dict, max_topics: int) -> list[dict]:
    """No posts to go on: tag what the profile itself lists (title, company, certifications, projects, courses)."""
    items = {}
    company = (rec.get("current_company") or {}).get("name") or rec.get("current_company_name")
    if rec.get("position") or company:
        items["role"] = ("Role", " at ".join(filter(None, [rec.get("position"), company])))
    for kind, key in (("Certification", "certifications"), ("Project", "projects"), ("Course", "courses")):
        for i, x in enumerate(rec.get(key) or []):
            text = " ".join(filter(None, [x.get("title"), x.get("subtitle"), (x.get("description") or "")[:300]]))
            if text:
                items[f"{key}{i}"] = (kind, text)
    if not items:
        return []
    tags = tagging._classify(OpenAI(), {k: text for k, (_, text) in items.items()})
    scores, evidence = defaultdict(int), defaultdict(list)
    for k, topics in tags.items():
        kind, text = items[k]
        for t in topics:
            scores[t] += 1
            if len(evidence[t]) < 2:
                evidence[t].append(f"From your profile ({kind.lower()}): {text[:200]}")
    ranked = sorted(scores.items(), key=lambda kv: -kv[1])[:max_topics]
    return [{"topic": t, "score": s, "evidence": evidence[t], "source": "profile"} for t, s in ranked]


def add_to_graph(g, rec: dict, interests: list[dict]) -> str:
    person = {"name": rec["name"], **seed._profile(rec, tagging.post_topics())}
    g.query(
        """
        MERGE (a:Person {name: $p.name})
        SET a.attendee = true, a.linkedin = $p.linkedin, a.location = $p.location
        WITH a
        OPTIONAL MATCH (a)-[old:INTERESTED_IN]->() DELETE old
        WITH DISTINCT a
        FOREACH (_ IN CASE WHEN $p.company IS NULL THEN [] ELSE [1] END |
            MERGE (c:Company {name: $p.company}) MERGE (a)-[:WORKS_AT]->(c))
        FOREACH (s IN $p.schools | MERGE (sc:School {name: s}) MERGE (a)-[:ALUMNI_OF]->(sc))
        FOREACH (i IN $interests |
            MERGE (tp:Topic {name: i.topic}) MERGE (a)-[r:INTERESTED_IN]->(tp) SET r.score = i.score)
        """,
        {"p": person, "interests": interests},
    )
    seed._load_posts(g, [person])
    return rec["name"]


def is_supported_conference(text: str) -> bool:
    t = text.strip().lower()
    return "githubuniverse.com" in t or bool(re.search(r"\b(github|gh)\s*universe\b", t)) or t == "universe"
