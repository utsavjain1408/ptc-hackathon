# Patch the Conference: graph agent starter

FalkorDB (graph DB) + an OpenAI tool-calling agent that answers questions by writing Cypher.

## Run it

```sh
cp .env.example .env            # add your OPENAI_API_KEY and FalkorDB settings (below)
uv sync
uv run python -m app.tagging    # LLM topic tags for talks + posts (cached; only new items cost anything)
uv run python -m app.seed       # load GitHub Universe 2026 talks + speakers
uv run python -m app.agent      # chat
```

### FalkorDB: cloud or local

**FalkorDB Cloud** (default for this project): set these in `.env` from your instance's connection details.

```dotenv
FALKORDB_HOST=<host, without scheme or port>
FALKORDB_PORT=<port>
FALKORDB_USERNAME=<username>
FALKORDB_PASSWORD=<password>
FALKORDB_SSL=false              # set true if your tier requires TLS
```

**Local docker**: leave username and password blank, use `FALKORDB_HOST=localhost` and `FALKORDB_PORT=6379`, then:

```sh
docker compose up -d            # FalkorDB on :6379, browser UI on http://localhost:3001
```

### Bright Data (web data)

Used to scrape real conference and profile data. Install the CLI and log in once:

```sh
npm install -g @brightdata/cli
bdata login --api-key <key>     # run in your own terminal; also add BRIGHTDATA_API_KEY to .env
```

Profiles: `bdata pipelines run gd_l1viktl72bvl7bjuj0 collect_by_url "<linkedin profile url>"`. Agent skills for this live in `.agents/skills/` (symlinked into `.claude/skills/` and other agents' folders).

Try: *"Who should Utsav Jain meet and why?"* or *"Plan my afternoon around AI agents."*

## Graph model

```
(:Person)-[:WORKS_AT]->(:Company)
(:Person)-[:ALUMNI_OF]->(:School)
(:Person)-[:INTERESTED_IN]->(:Topic)     # attendees; topics they chose (seed.INTERESTS)
(:Person)-[:SPEAKS_AT]->(:Talk)-[:ABOUT]->(:Topic)
(:Person)-[:POSTED|SHARED|REACTED_TO]->(:Post)-[:ABOUT]->(:Topic)   # recent LinkedIn activity
```

`Person` has `title` (speakers), `linkedin`, `location`, and `attendee: true` for attendees. `Talk` has `type`, `day`, `start`, `end`, `room`, `description`.

See it visually: open your FalkorDB Cloud instance's browser (or http://localhost:3001 and connect to `localhost:6379` for local docker), select graph `conference`, run `MATCH (n)-[r]->(m) RETURN n, r, m`.

## Data

The catalog site renders sessions with JavaScript, so a plain scrape returns only the menu. It was captured with the Browser API (uses the `cli_browser` zone; not covered by free credits):

```sh
bdata browser open "https://reg.githubuniverse.com/flow/github/universe26/attendee-portal/page/sessioncatalog"
bdata browser snapshot --interactive   # find the "Show more" button ref; click it until it disappears
bdata browser click <ref>
bdata browser get text -o data/universe26_catalog.txt
bdata browser close
```

Topics are assigned by an LLM (`app/tagging.py`, `TAGGING_MODEL`, default `gpt-5.4-mini`): 0-2 topics per talk or post from a fixed list of 9. Results are cached in `data/talk_topics.json` and `data/linkedin/post_topics.json`; keyword rules in `catalog.TOPICS` are only a fallback for untagged items.

## Layout

- `app/db.py`: connection + schema introspection (fed to the LLM)
- `app/catalog.py`: parses the session catalog into talks, speakers and keyword-based topics
- `app/linkedin.py`: finds speakers' LinkedIn profiles (SERP search + name check) and scrapes them; hand-checked URLs go in `data/linkedin/overrides.json`
- `app/tagging.py`: LLM topic tagging for talks and posts, with caches
- `app/seed.py`: loads the catalog, speaker profiles and attendees into the graph
- `data/linkedin/` (gitignored): scraped profiles; `attendees/` holds one profile JSON per attendee
- `data/universe26_catalog.txt`: GitHub Universe 2026 session catalog (162 entries), rendered with the Bright Data Browser API
- `app/agent.py`: chat loop, `run_cypher` tool (read-only queries)
