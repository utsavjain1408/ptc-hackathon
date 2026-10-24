"""Load the GitHub Universe 2026 graph from real data only.

- Talks and speakers: the session catalog (app/catalog.py).
- Speaker schools, LinkedIn links and recent activity (posts): data/linkedin/profiles.json (app/linkedin.py).
- Attendees: one scraped LinkedIn profile per file in data/linkedin/attendees/.

Run: uv run python -m app.seed
"""
import json
from pathlib import Path

from app import catalog, tagging
from app.db import get_graph

LINKEDIN = Path(__file__).resolve().parent.parent / "data" / "linkedin"

# Topics each attendee chose themselves (names must match tagging.TOPICS).
INTERESTS = {
    "Utsav Jain": ["AI Agents", "Platform Engineering", "Security", "MCP"],
    "Vishaka Datta Jambae Hebbar": ["AI Agents"],
}


def _engagement(interaction: str | None) -> str:
    """LinkedIn's activity label -> relationship type: own post, reshare, or reaction."""
    text = (interaction or "").lower()
    if "shared" in text:
        return "SHARED"
    if "liked" in text or "reacted" in text or "celebrat" in text or "support" in text:
        return "REACTED_TO"
    return "POSTED"


def _profile(p: dict, post_topics: dict) -> dict:
    """The fields the graph uses from a LinkedIn people-profile record."""
    company = p.get("current_company_name") or (p.get("current_company") or {}).get("name")
    posts = [
        {"link": a["link"], "text": (a.get("title") or "")[:1000], "rel": _engagement(a.get("interaction")),
         # LLM tags from app.tagging; keyword rules for posts not tagged yet
         "topics": post_topics.get(a["link"], catalog.topics(a.get("title") or ""))}
        for a in p.get("activity") or [] if a.get("link")
    ]
    return {
        "linkedin": p.get("url") or p.get("input_url"),
        "location": p.get("city") or p.get("location"),
        "company": company,
        "schools": [e["title"] for e in p.get("education") or [] if e.get("title")],
        "posts": posts,
    }


def _load_posts(g, people: list[dict]):
    """(:Person)-[:POSTED|SHARED|REACTED_TO]->(:Post)-[:ABOUT]->(:Topic) from LinkedIn activity."""
    for rel in ("POSTED", "SHARED", "REACTED_TO"):  # Cypher can't parameterize a relationship type
        g.query(
            f"""
            UNWIND $people AS p
            MATCH (person:Person {{name: p.name}})
            UNWIND [x IN p.posts WHERE x.rel = '{rel}'] AS post
            MERGE (ps:Post {{link: post.link}})
            SET ps.text = post.text
            MERGE (person)-[:{rel}]->(ps)
            FOREACH (t IN post.topics | MERGE (tp:Topic {{name: t}}) MERGE (ps)-[:ABOUT]->(tp))
            """,
            {"people": people},
        )


def add_speaker_profiles(g, profiles: dict):
    """Attach scraped LinkedIn profiles ({catalog name: record}) to existing speakers.

    Safe to call repeatedly: app.linkedin calls it after each scraped batch.
    """
    post_topics = tagging.post_topics()
    speakers = [{"name": name, **_profile(p, post_topics)} for name, p in profiles.items()]
    g.query(
        """
        UNWIND $people AS p
        MATCH (sp:Person {name: p.name})
        SET sp.linkedin = p.linkedin, sp.location = p.location
        FOREACH (s IN p.schools | MERGE (sc:School {name: s}) MERGE (sp)-[:ALUMNI_OF]->(sc))
        """,
        {"people": speakers},
    )
    _load_posts(g, speakers)


def _load_json(path: Path):
    return json.loads(path.read_text()) if path.exists() else None


def seed():
    g = get_graph()
    g.query("MATCH (n) DETACH DELETE n")

    talks = catalog.parse()
    talk_topics = tagging.talk_topics()  # LLM tags; keep the keyword topics for talks not tagged yet
    for t in talks:
        t["topics"] = talk_topics.get(tagging.talk_key(t), t["topics"])
    g.query(
        """
        UNWIND $talks AS t
        MERGE (talk:Talk {title: t.title, day: t.day, start: t.start})
        SET talk.type = t.type, talk.end = t.end, talk.room = t.room, talk.description = t.description
        FOREACH (x IN t.topics | MERGE (tp:Topic {name: x}) MERGE (talk)-[:ABOUT]->(tp))
        """,
        {"talks": talks},
    )
    g.query(
        """
        UNWIND $speakers AS s
        MERGE (sp:Person {name: s.name})
        SET sp.title = s.title
        WITH sp, s
        MATCH (talk:Talk {title: s.talk, day: s.day, start: s.start})
        MERGE (sp)-[:SPEAKS_AT]->(talk)
        FOREACH (_ IN CASE WHEN s.company IS NULL THEN [] ELSE [1] END |
            MERGE (c:Company {name: s.company}) MERGE (sp)-[:WORKS_AT]->(c))
        """,
        {"speakers": [
            {**s, "talk": t["title"], "day": t["day"], "start": t["start"]}
            for t in talks for s in t["speakers"]
        ]},
    )

    # Speaker LinkedIn data, keyed by the speaker's catalog name.
    profiles = _load_json(LINKEDIN / "profiles.json") or {}
    add_speaker_profiles(g, profiles)

    # Attendees: anyone with a scraped profile in data/linkedin/attendees/.
    attendees = []
    for path in sorted((LINKEDIN / "attendees").glob("*.json")):
        rec = _load_json(path)
        rec = rec[0] if isinstance(rec, list) else rec
        attendees.append({"name": rec["name"], "interests": INTERESTS.get(rec["name"], []),
                          **_profile(rec, tagging.post_topics())})
    g.query(
        """
        UNWIND $people AS p
        MERGE (a:Person {name: p.name})
        SET a.attendee = true, a.linkedin = p.linkedin, a.location = p.location
        FOREACH (_ IN CASE WHEN p.company IS NULL THEN [] ELSE [1] END |
            MERGE (c:Company {name: p.company}) MERGE (a)-[:WORKS_AT]->(c))
        FOREACH (s IN p.schools | MERGE (sc:School {name: s}) MERGE (a)-[:ALUMNI_OF]->(sc))
        FOREACH (t IN p.interests | MERGE (tp:Topic {name: t}) MERGE (a)-[:INTERESTED_IN]->(tp))
        """,
        {"people": attendees},
    )
    _load_posts(g, attendees)

    counts = g.query("MATCH (n) RETURN count(n)").result_set[0][0]
    rels = g.query("MATCH ()-[r]->() RETURN count(r)").result_set[0][0]
    print(f"Seeded {len(talks)} talks, {len(profiles)} speaker profiles, {len(attendees)} attendees: "
          f"{counts} nodes, {rels} relationships.")


if __name__ == "__main__":
    seed()
