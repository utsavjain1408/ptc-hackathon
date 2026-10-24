"""Load a small sample conference graph. Replace with real data (scraped schedule, attendee list) later.

Run: uv run python -m app.seed
"""
from app.db import get_graph

PEOPLE = [
    # name, company, alumni_of, skills, interests
    ("Utsav", "Airspan Networks", "SJSU", ["AWS", "Kubernetes", "Kafka", "Python", "Go"], ["AI Agents", "Platform Engineering"]),
    ("Priya", "Stripe", "SJSU", ["Python", "Kafka"], ["AI Agents", "Data Infra"]),
    ("Marcus", "Vercel", "UC Berkeley", ["React", "Node.js"], ["Developer Experience", "AI Agents"]),
    ("Lena", "Datadog", "Stanford", ["Go", "Kubernetes"], ["Observability", "Platform Engineering"]),
    ("Diego", "GitHub", "SJSU", ["Ruby", "Go"], ["Developer Experience", "Security"]),
    ("Aisha", "OpenAI", "MIT", ["Python"], ["AI Agents", "Evaluation"]),
    ("Tom", "Bright Data", "UCLA", ["Node.js", "Python"], ["Web Data", "AI Agents"]),
]

TALKS = [
    # title, speaker, room, start, topics
    ("Agents that ship PRs", "Aisha", "Main Stage", "10:00", ["AI Agents", "Developer Experience"]),
    ("Kubernetes at 10k nodes", "Lena", "Room B", "11:00", ["Platform Engineering", "Observability"]),
    ("Securing the software supply chain", "Diego", "Room A", "13:00", ["Security"]),
    ("Streaming data for LLM apps", "Priya", "Room B", "14:00", ["Data Infra", "AI Agents"]),
    ("Scraping the web responsibly", "Tom", "Room A", "15:00", ["Web Data"]),
]


def seed():
    g = get_graph()
    g.query("MATCH (n) DETACH DELETE n")

    g.query(
        """
        UNWIND $people AS p
        MERGE (person:Person {name: p.name})
        MERGE (c:Company {name: p.company})
        MERGE (s:School {name: p.school})
        MERGE (person)-[:WORKS_AT]->(c)
        MERGE (person)-[:ALUMNI_OF]->(s)
        FOREACH (sk IN p.skills | MERGE (k:Skill {name: sk}) MERGE (person)-[:HAS_SKILL]->(k))
        FOREACH (t IN p.interests | MERGE (tp:Topic {name: t}) MERGE (person)-[:INTERESTED_IN]->(tp))
        """,
        {"people": [
            {"name": n, "company": c, "school": s, "skills": sk, "interests": i}
            for n, c, s, sk, i in PEOPLE
        ]},
    )

    g.query(
        """
        UNWIND $talks AS t
        MERGE (talk:Talk {title: t.title})
        SET talk.room = t.room, talk.start = t.start
        WITH talk, t
        MATCH (sp:Person {name: t.speaker})
        MERGE (sp)-[:SPEAKS_AT]->(talk)
        FOREACH (x IN t.topics | MERGE (tp:Topic {name: x}) MERGE (talk)-[:ABOUT]->(tp))
        """,
        {"talks": [
            {"title": ti, "speaker": sp, "room": r, "start": st, "topics": tp}
            for ti, sp, r, st, tp in TALKS
        ]},
    )

    counts = g.query("MATCH (n) RETURN count(n)").result_set[0][0]
    rels = g.query("MATCH ()-[r]->() RETURN count(r)").result_set[0][0]
    print(f"Seeded {counts} nodes, {rels} relationships.")


if __name__ == "__main__":
    seed()
