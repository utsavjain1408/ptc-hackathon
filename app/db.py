import os

from dotenv import load_dotenv
from falkordb import FalkorDB

load_dotenv()


def get_graph():
    db = FalkorDB(
        host=os.getenv("FALKORDB_HOST", "localhost"),
        port=int(os.getenv("FALKORDB_PORT", "6379")),
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
            f"MATCH (n:{label}) WITH n LIMIT 1 RETURN keys(n)"
        ).result_set
        lines.append(f"  (:{label}) {keys[0][0] if keys else []}")
    lines.append("Relationships:")
    for a, rel, b in patterns:
        lines.append(f"  (:{a})-[:{rel}]->(:{b})")
    return "\n".join(lines)
