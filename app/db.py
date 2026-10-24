import os

from dotenv import load_dotenv
from falkordb import FalkorDB

load_dotenv()


def get_graph():
    db = FalkorDB(
        host=os.getenv("FALKORDB_HOST", "localhost"),
        port=int(os.getenv("FALKORDB_PORT", "6379")),
        username=os.getenv("FALKORDB_USERNAME") or None,
        password=os.getenv("FALKORDB_PASSWORD") or None,
        ssl=os.getenv("FALKORDB_SSL", "false").lower() == "true",
    )
    return db.select_graph(os.getenv("GRAPH_NAME", "conference"))


def describe_schema(graph) -> str:
    """Summarize labels, relationship patterns and property keys so the LLM can write Cypher."""
    labels = [r[0] for r in graph.ro_query("CALL db.labels()").result_set]
    patterns = graph.ro_query(
        "MATCH (a)-[r]->(b) "
        "RETURN DISTINCT labels(a)[0], type(r), labels(b)[0]"
    ).result_set
    lines = ["Node labels and properties:"]
    for label in labels:
        keys = graph.ro_query(
            f"MATCH (n:{label}) UNWIND keys(n) AS k RETURN collect(DISTINCT k)"
        ).result_set
        if keys and keys[0][0]:  # FalkorDB keeps labels whose nodes were all deleted; skip those
            lines.append(f"  (:{label}) {keys[0][0]}")
    lines.append("Relationships:")
    for a, rel, b in patterns:
        lines.append(f"  (:{a})-[:{rel}]->(:{b})")
    return "\n".join(lines)
