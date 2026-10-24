# Patch the Conference

Paste your LinkedIn profile and a conference; get a personal briefing: who to meet and why, what to attend, and an agent you can ask anything about the event.

Bright Data scrapes the conference catalog (a JavaScript-only site, via the Browser API), finds and scrapes every speaker's LinkedIn profile and recent posts, and scrapes yours. Everything lands in a FalkorDB knowledge graph; OpenAI models tag topics, write the "why meet them" reasons and power a chat agent that answers by writing Cypher. The demo supports GitHub Universe 2026 (162 talks, 183 speakers, 144 LinkedIn profiles).

## Run it

```sh
cp .env.example .env            # add OPENAI_API_KEY and FalkorDB settings (below)
uv sync
uv run python -m app.linkedin   # find + scrape speakers' LinkedIn profiles (~400 Bright Data credits; skips what's cached)
uv run python -m app.tagging    # LLM topic tags for talks + posts (cached; only new items cost anything)
uv run python -m app.seed       # rebuild the graph from the catalog + scraped data
uv run python -m app.buzz       # LinkedIn posts about the conference + their authors (~130 credits; run after seed)
uv run uvicorn app.server:app   # web app: http://localhost:8000
uv run python -m app.agent      # or: chat with the agent in the terminal
```

Scraped LinkedIn data is gitignored, so a fresh clone needs the `app.linkedin` step; after that, `app.seed` works offline. `app.seed` wipes and rebuilds the graph, so people added through the web app disappear until they run their briefing again.

### The web app

1. Enter a LinkedIn profile URL and a conference name or URL. Anything other than GitHub Universe gets a "not supported in this demo" message.
2. The profile is scraped with Bright Data (about a minute; cached afterwards in `data/linkedin/attendees/`).
3. Interests are inferred from the person's own posts, reshares, reactions and "about" text (`app/profile.py`); no form. With no posts, they come from the role, certifications, projects and courses on the profile; with nothing at all, the schedule falls back to keynotes and headline sessions.
4. The person is added to the graph, then matched (`app/matching.py`):
   - **Speakers to meet**, scored: shared school or company +3 each, shared talk topic +2, shared post topic +1. `gpt-5.5` writes a 1-2 sentence reason per speaker from those facts only, ending with a conversation opener.
   - **Shared background**: speakers from the same school or company, whatever their topics.
   - **Schedule**: best-matching talks per day with no overlaps (5 minutes between sessions).
   - **Connection graph**: you, what you share, and your top speakers.
   - **Your network at Universe**: public LinkedIn posts about the conference from coworkers, fellow alumni and people whose posts you've liked or reshared (connections lists are private, so engagement is the closest public signal). Falls back to the latest posts when nobody in your circle has posted.
5. A chat agent (`gpt-5.5`) answers follow-up questions and shows the Cypher it ran.

Try asking: *"Who should I meet first and why?"*, *"Plan my Thursday, max 5 sessions"*, *"Who's posting about MCP or agent security?"*

### Deploy (spawnpoint)

The app is live at https://patch-the-conference.spwt.app (python runtime, `main.py` serves on `0.0.0.0:$PORT`, dependencies from `requirements.txt`). Set the same variables as `.env` in the project's environment, plus `BRIGHTDATA_API_KEY`: without the `bdata` CLI on the host, `app/linkedin.py` scrapes profiles through Bright Data's REST API instead. The bundle needs the code, `data/talk_topics.json` and optionally `data/linkedin/post_topics.json` and `data/linkedin/attendees/` (cached profiles load instantly); the rest of `data/linkedin/` is already in the graph. Profiles scraped on the server are cached under the app folder, so a redeploy clears them.

### Configuration

| Variable | Purpose |
|---|---|
| `OPENAI_API_KEY` | Agent, reasons and tagging |
| `OPENAI_MODEL` | Agent and "why meet them" reasons (default `gpt-5.5`) |
| `TAGGING_MODEL` | Topic tagging (default `gpt-5.4-mini`) |
| `FALKORDB_*`, `GRAPH_NAME` | Graph database connection (below) |
| `BRIGHTDATA_API_KEY` | Only on hosts without the `bdata` CLI (e.g. the deployed app) |

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

**Bright Data**: the code calls the `bdata` CLI, which keeps its own credentials. Install it and log in once:

```sh
npm install -g @brightdata/cli
bdata login --api-key <key>     # run in your own terminal so the key stays out of logs
```

Bright Data agent skills live in `.agents/skills/` (symlinked into `.claude/skills/` and other agents' folders).

## Graph model

```
(:Person)-[:WORKS_AT]->(:Company)
(:Person)-[:ALUMNI_OF]->(:School)
(:Person)-[:SPEAKS_AT]->(:Talk)-[:ABOUT]->(:Topic)
(:Person)-[:INTERESTED_IN {score}]->(:Topic)                      # attendees: inferred by the web app, or seed.INTERESTS
(:Person)-[:POSTED|SHARED|REACTED_TO]->(:Post)-[:ABOUT]->(:Topic)   # recent LinkedIn activity
(:Person {buzz: true})-[:POSTED]->(:Post {conference: true, date})  # posts about the conference (app.buzz)
```

`Person` has `title` (speakers), `linkedin`, `location`, and `attendee: true` for attendees. `Talk` has `type`, `day`, `start`, `end`, `room`, `description`. `Post` has `link` and `text`.

See it visually: open your FalkorDB Cloud instance's browser (or http://localhost:3001 and connect to `localhost:6379` for local docker), select graph `conference`, run `MATCH (n)-[r]->(m) RETURN n, r, m LIMIT 300`.

## Data

**Conference catalog.** The catalog site renders sessions with JavaScript, so a plain scrape returns only the menu. It was captured with the Browser API (uses the `cli_browser` zone; not covered by free credits):

```sh
bdata browser open "https://reg.githubuniverse.com/flow/github/universe26/attendee-portal/page/sessioncatalog"
bdata browser snapshot --interactive   # find the "Show more" button ref; click it until it disappears
bdata browser click <ref>
bdata browser get text -o data/universe26_catalog.txt
bdata browser close
```

**Speaker profiles** (`app/linkedin.py`). For each speaker, a SERP search `site:linkedin.com/in "<name>" <company>` (US results, retried) picks the first result whose title contains the speaker's first and last name. Profiles are then scraped in batches of 10, four at a time, and dropped if the scraped name doesn't match. Speakers whose profiles search can't surface go in `data/linkedin/overrides.json` (`{"Name": "url"}`); private profiles come back as `dead_page` and are skipped. `--retry` re-searches speakers that weren't found.

**Conference buzz** (`app/buzz.py`). SERP searches for `site:linkedin.com/posts "GitHub Universe"` variants, plus Universe mentions already in speakers' activity (5x what search finds). A post's activity ID encodes its timestamp, so only 2026 posts are kept. Each author's profile is scraped once for company and schools.

**Topics** (`app/tagging.py`). An LLM picks 0-2 topics per talk or post from a fixed list of 9 (`tagging.TOPICS`). Results are cached in `data/talk_topics.json` and `data/linkedin/post_topics.json`; keyword rules in `catalog.TOPICS` are only a fallback for untagged items.

## Layout

- `app/catalog.py`: parses the session catalog into talks and speakers (+ keyword topic fallback)
- `app/linkedin.py`: finds speakers' LinkedIn profiles and scrapes them in parallel batches
- `app/tagging.py`: LLM topic tagging for talks and posts, with caches
- `app/seed.py`: rebuilds the graph from the catalog, speaker profiles and attendees
- `app/buzz.py`: collects LinkedIn posts about the conference and scrapes their authors
- `app/profile.py`: adds a user from a LinkedIn URL: scrape (cached), infer interests, upsert into the graph
- `app/matching.py`: speaker ranking, LLM reasons, shared background, schedule, connection graph
- `app/server.py` + `app/static/index.html`: FastAPI web app; `main.py` starts it for hosts that run `python main.py`
- `app/agent.py`: chat agent, `run_cypher` tool (read-only queries)
- `app/db.py`: connection + schema introspection (fed to the LLM)
- `data/universe26_catalog.txt`: GitHub Universe 2026 session catalog (162 entries)
- `data/talk_topics.json`: LLM topic tags for talks
- `data/linkedin/` (gitignored): scraped profiles, URLs, post topics, overrides; `attendees/` holds one profile per attendee
