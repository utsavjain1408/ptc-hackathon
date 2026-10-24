# Patch the Conference: graph agent starter

FalkorDB (graph DB) + an OpenAI tool-calling agent that answers questions by writing Cypher.

## Run it

```sh
docker compose up -d            # FalkorDB on :6379, browser UI on http://localhost:3001
cp .env.example .env            # add your OPENAI_API_KEY
uv sync
uv run python -m app.seed       # load sample conference data
uv run python -m app.agent      # chat
```

Try: *"Who should Utsav meet and why?"* or *"Plan my afternoon around AI agents."*

## Graph model

```
(:Person)-[:WORKS_AT]->(:Company)
(:Person)-[:ALUMNI_OF]->(:School)
(:Person)-[:HAS_SKILL]->(:Skill)
(:Person)-[:INTERESTED_IN]->(:Topic)
(:Person)-[:SPEAKS_AT]->(:Talk)-[:ABOUT]->(:Topic)
```

See it visually: open http://localhost:3001, connect to `localhost:6379`, select graph `conference`, run `MATCH (n)-[r]->(m) RETURN n, r, m`.

## Layout

- `app/db.py`: connection + schema introspection (fed to the LLM)
- `app/seed.py`: sample data; swap for real scraped data
- `app/agent.py`: chat loop, `run_cypher` tool (read-only queries)
