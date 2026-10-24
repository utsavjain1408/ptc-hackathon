"""Turn the graph into a personal briefing: speaker matches with LLM-written reasons, shared background,
a talk schedule and a connection graph."""
import json
import os
from datetime import datetime

from openai import OpenAI

REASON_MODEL = os.getenv("OPENAI_MODEL", "gpt-5.5")
REASON_PROMPT = (
    "You write the 'why meet them' line in a personal conference briefing. For each speaker, write 1-2 "
    "sentences addressed to the attendee as 'you': the most compelling concrete reason to meet them and a "
    "natural conversation opener. Use ONLY the facts given (shared school/company, shared topics, their talk, "
    "their recent post, the attendee's own posts); never invent facts. Be specific (name the talk, the post's "
    "point, the school), not generic. No hype words. Refer to the speaker by first name or 'they/them'; "
    "never guess gendered pronouns from a name. Keep each reason under 60 words. "
    'Reply with JSON only: {"reasons": {"<speaker name>": "<reason>"}}.'
)

# Event types that aren't talks worth scheduling around.
NOT_TALKS = ["Social", "Session After Party", "Special Event"]


def _rows(g, query: str, params: dict) -> list[dict]:
    res = g.ro_query(query, params)
    keys = [h[1] for h in res.header]
    return [dict(zip(keys, r)) for r in res.result_set]


def interests(g, name: str) -> list[dict]:
    return _rows(g, """
        MATCH (:Person {name: $name})-[i:INTERESTED_IN]->(t:Topic)
        RETURN t.name AS topic, coalesce(i.score, 1) AS score ORDER BY score DESC
    """, {"name": name})


def speakers(g, name: str, limit: int = 8) -> list[dict]:
    """Speakers ranked by shared school/company (3 each), talk topics (2 each), post topics (1 each)."""
    matches = _rows(g, """
        MATCH (u:Person {name: $name}), (sp:Person)-[:SPEAKS_AT]->(:Talk)
        WHERE sp <> u
        OPTIONAL MATCH (u)-[:INTERESTED_IN]->(t:Topic)<-[:ABOUT]-(:Talk)<-[:SPEAKS_AT]-(sp)
        WITH u, sp, collect(DISTINCT t.name) AS talk_topics
        OPTIONAL MATCH (u)-[:INTERESTED_IN]->(t:Topic)<-[:ABOUT]-(:Post)<-[:POSTED|SHARED]-(sp)
        WITH u, sp, talk_topics, collect(DISTINCT t.name) AS post_topics
        OPTIONAL MATCH (u)-[:ALUMNI_OF]->(s:School)<-[:ALUMNI_OF]-(sp)
        WITH u, sp, talk_topics, post_topics, collect(DISTINCT s.name) AS schools
        OPTIONAL MATCH (u)-[:WORKS_AT]->(c:Company)<-[:WORKS_AT]-(sp)
        WITH sp, talk_topics, post_topics, schools, collect(DISTINCT c.name) AS companies
        WITH sp, talk_topics, post_topics, schools, companies,
             3 * size(schools) + 3 * size(companies) + 2 * size(talk_topics) + size(post_topics) AS score
        WHERE score > 0
        OPTIONAL MATCH (sp)-[:WORKS_AT]->(co:Company)
        RETURN sp.name AS name, sp.title AS title, co.name AS company, sp.linkedin AS linkedin,
               score, talk_topics, post_topics, schools, companies
        ORDER BY score DESC, name LIMIT $limit
    """, {"name": name, "limit": limit})

    for m in matches:
        m["talks"] = _rows(g, """
            MATCH (:Person {name: $sp})-[:SPEAKS_AT]->(t:Talk)
            RETURN t.title AS title, t.day AS day, t.start AS start, t.end AS end, t.room AS room,
                   t.description AS description
            ORDER BY t.day, t.start
        """, {"sp": m["name"]})
        topics = sorted(set(m["talk_topics"]) | set(m["post_topics"]))
        posts = _rows(g, """
            MATCH (:Person {name: $sp})-[r:POSTED|SHARED]->(p:Post)-[:ABOUT]->(t:Topic)
            WHERE t.name IN $topics
            RETURN p.text AS text, p.link AS link, type(r) AS how
            ORDER BY CASE type(r) WHEN 'POSTED' THEN 0 ELSE 1 END LIMIT 1
        """, {"sp": m["name"], "topics": topics})
        m["post"] = posts[0] if posts else None
    return matches


def add_reasons(person: dict, interests_: list[dict], matches: list[dict]) -> None:
    """Fill m["reason"] for each match with one LLM call, grounded in the facts we matched on."""
    facts = {
        "attendee": {**person, "interests": [{"topic": i["topic"], "their_posts": i["evidence"]} for i in interests_]},
        "speakers": [{
            "name": m["name"], "title": m["title"], "company": m["company"],
            "shared_school": m["schools"], "shared_company": m["companies"],
            "shared_topics": sorted(set(m["talk_topics"]) | set(m["post_topics"])),
            "talks": [{"title": t["title"], "when": f"{t['day']} {t['start']}", "about": (t.get("description") or "")[:400]}
                      for t in m["talks"]],
            "recent_post": ((m["post"] or {}).get("text") or "")[:400],
        } for m in matches],
    }
    resp = OpenAI().chat.completions.create(
        model=REASON_MODEL,
        messages=[{"role": "system", "content": REASON_PROMPT},
                  {"role": "user", "content": json.dumps(facts, ensure_ascii=False)}],
        response_format={"type": "json_object"},
    )
    reasons = json.loads(resp.choices[0].message.content).get("reasons", {})
    for m in matches:
        m["reason"] = reasons.get(m["name"])


def network_buzz(g, name: str, engaged_slugs: list[str]) -> dict:
    """People in the user's circle posting about the conference, plus the latest posts from anyone.

    "Circle" = coworkers, fellow alumni, or authors whose posts the user liked or reshared (connections
    lists are private, so this is the best public signal).
    """
    circle = _rows(g, """
        MATCH (u:Person {name: $name}), (a:Person {buzz: true})-[:POSTED]->(p:Post {conference: true})
        WHERE a <> u
        OPTIONAL MATCH (u)-[:WORKS_AT]->(c:Company)<-[:WORKS_AT]-(a)
        OPTIONAL MATCH (u)-[:ALUMNI_OF]->(s:School)<-[:ALUMNI_OF]-(a)
        WITH a, collect(DISTINCT c.name) AS companies, collect(DISTINCT s.name) AS schools,
             collect(DISTINCT {text: p.text, link: p.link, date: p.date}) AS posts
        WITH a, companies, schools, posts, a.linkedin_slug IN $engaged AS engaged
        WHERE engaged OR size(companies) > 0 OR size(schools) > 0
        OPTIONAL MATCH (a)-[:SPEAKS_AT]->(t:Talk)
        RETURN a.name AS name, a.linkedin AS linkedin, companies, schools, engaged, posts,
               collect(DISTINCT t.title) AS talks
        ORDER BY CASE WHEN engaged THEN 3 ELSE 0 END + 2 * size(schools) + size(companies) DESC, name  // engagement ~ a real connection
        LIMIT 12
    """, {"name": name, "engaged": engaged_slugs})
    latest = _rows(g, """
        MATCH (a:Person {buzz: true})-[:POSTED]->(p:Post {conference: true})
        WHERE a.name <> $name
        OPTIONAL MATCH (a)-[:WORKS_AT]->(c:Company)
        RETURN a.name AS name, a.linkedin AS linkedin, c.name AS company, p.text AS text, p.link AS link, p.date AS date
        ORDER BY p.date DESC LIMIT 6
    """, {"name": name})
    total = g.ro_query("MATCH (:Person {buzz: true})-[:POSTED]->(p:Post {conference: true}) RETURN count(p)").result_set[0][0]
    return {"circle": circle, "latest": latest, "total_posts": total}


def shared_background(g, name: str) -> list[dict]:
    """Speakers who went to the same school or work at the same company, whatever their topics."""
    return _rows(g, """
        MATCH (u:Person {name: $name})-[:ALUMNI_OF|WORKS_AT]->(x)<-[:ALUMNI_OF|WORKS_AT]-(sp:Person)-[:SPEAKS_AT]->(t:Talk)
        WHERE sp <> u
        RETURN sp.name AS name, sp.title AS title, sp.linkedin AS linkedin, labels(x)[0] AS kind, x.name AS shared,
               collect(DISTINCT {title: t.title, day: t.day, start: t.start, room: t.room}) AS talks
        ORDER BY kind DESC, name
    """, {"name": name})


def _minutes(day: str, t: str) -> tuple[str, int]:
    tm = datetime.strptime(t, "%I:%M %p")
    return day, tm.hour * 60 + tm.minute


def schedule(g, name: str, per_day: int = 6) -> dict[str, list[dict]]:
    """Best-matching talks per day, greedily picked so none overlap (5 minutes to walk between rooms).

    With no interests, falls back to headline sessions; those talks carry `general: true`.
    """
    talks = _rows(g, """
        MATCH (:Person {name: $name})-[i:INTERESTED_IN]->(tp:Topic)<-[:ABOUT]-(t:Talk)
        WHERE NOT t.type IN $skip
        WITH t, sum(coalesce(i.score, 1)) AS score, collect(tp.name) AS topics
        OPTIONAL MATCH (sp:Person)-[:SPEAKS_AT]->(t)
        RETURN t.title AS title, t.type AS type, t.day AS day, t.start AS start, t.end AS end,
               t.room AS room, score, topics, collect(sp.name) AS speakers
        ORDER BY score DESC
    """, {"name": name, "skip": NOT_TALKS})

    if not talks:  # no interests to match: headline sessions first, then the most-staffed breakouts
        talks = _rows(g, """
            MATCH (t:Talk) WHERE NOT t.type IN $skip
            OPTIONAL MATCH (sp:Person)-[:SPEAKS_AT]->(t)
            OPTIONAL MATCH (t)-[:ABOUT]->(tp:Topic)
            WITH t, collect(DISTINCT sp.name) AS speakers, collect(DISTINCT tp.name) AS topics
            RETURN t.title AS title, t.type AS type, t.day AS day, t.start AS start, t.end AS end,
                   t.room AS room, 0 AS score, topics, speakers, true AS general
            ORDER BY CASE t.type WHEN 'Keynote' THEN 0 WHEN 'Main Stage' THEN 1 WHEN 'Panel Discussion' THEN 2
                     WHEN 'Breakout Session' THEN 3 ELSE 4 END, size(speakers) DESC
        """, {"skip": NOT_TALKS})

    days: dict[str, list[dict]] = {}
    for t in talks:
        picked = days.setdefault(t["day"], [])
        if len(picked) >= per_day:
            continue
        _, s = _minutes(t["day"], t["start"])
        _, e = _minutes(t["day"], t["end"])
        if all(e + 5 <= _minutes(p["day"], p["start"])[1] or s >= _minutes(p["day"], p["end"])[1] + 5
               for p in picked):
            picked.append(t)
    for picked in days.values():
        picked.sort(key=lambda t: _minutes(t["day"], t["start"])[1])
    order = ["Wednesday, Oct 28", "Thursday, Oct 29"]
    return {d: days[d] for d in order if d in days} | {d: v for d, v in days.items() if d not in order}


def connection_graph(name: str, matches: list[dict], interests_: list[dict], top: int = 6) -> dict:
    """Nodes/edges for the 'why you're connected' view: you -> shared school/company/topic -> speaker."""
    nodes, edges = {name: {"id": name, "label": name, "group": "you"}}, []
    for m in matches[:top]:
        nodes[m["name"]] = {"id": m["name"], "label": m["name"], "group": "speaker"}
        for kind, items in (("school", m["schools"]), ("company", m["companies"]),
                            ("topic", sorted(set(m["talk_topics"]) | set(m["post_topics"])))):
            for item in items:
                key = f"{kind}:{item}"
                nodes.setdefault(key, {"id": key, "label": item, "group": kind})
                edges.append({"from": key, "to": m["name"]})
    for key in [k for k in nodes if k.split(":", 1)[0] in ("school", "company", "topic")]:
        edges.append({"from": name, "to": key})
    return {"nodes": list(nodes.values()), "edges": edges}
