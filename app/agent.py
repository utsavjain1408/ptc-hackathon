"""Chat agent that answers questions by writing Cypher against FalkorDB.

Run: uv run python -m app.agent
"""
import json
import os

from openai import OpenAI

from app.db import describe_schema, get_graph

MODEL = os.getenv("OPENAI_MODEL", "gpt-4.1")

TOOLS = [{
    "type": "function",
    "function": {
        "name": "run_cypher",
        "description": "Run a read-only openCypher query against the conference graph and return rows.",
        "parameters": {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
    },
}]


def run_cypher(graph, query: str) -> str:
    try:
        res = graph.ro_query(query)  # read-only: the agent can't mutate the graph
        return json.dumps({"header": [h[1] for h in res.header], "rows": res.result_set[:50]}, default=str)
    except Exception as e:
        return json.dumps({"error": str(e)})  # let the model see the error and retry


def system_prompt(graph) -> str:
    return (
        "You help conference attendees decide who to meet and which talks to attend. "
        "Answer by querying the graph with run_cypher; never invent people or talks. "
        "Explain *why* using the connections you found (shared school, skills, topics).\n\n"
        f"Graph schema:\n{describe_schema(graph)}"
    )


def chat():
    client = OpenAI()
    graph = get_graph()
    messages = [{"role": "system", "content": system_prompt(graph)}]
    print("Ask about the conference (Ctrl+C to quit).")
    while True:
        messages.append({"role": "user", "content": input("\n> ")})
        while True:
            msg = client.chat.completions.create(model=MODEL, messages=messages, tools=TOOLS).choices[0].message
            messages.append(msg)
            if not msg.tool_calls:
                print(msg.content)
                break
            for call in msg.tool_calls:
                query = json.loads(call.function.arguments)["query"]
                print(f"  [cypher] {query}")
                messages.append({"role": "tool", "tool_call_id": call.id, "content": run_cypher(graph, query)})


if __name__ == "__main__":
    try:
        chat()
    except (KeyboardInterrupt, EOFError):
        pass
