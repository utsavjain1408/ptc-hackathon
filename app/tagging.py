"""Tag talks and LinkedIn posts with topics using an LLM.

Keyword rules (catalog.TOPICS) miss posts that don't use the exact words and over-tag ones that mention
many, so an LLM picks 0-2 topics per item from a fixed list. Results are cached, so each item is
classified once and app.seed stays fast and offline:

- data/talk_topics.json            talks, keyed by "title | day | start" (public data, committed)
- data/linkedin/post_topics.json   posts, keyed by post link (gitignored with the other LinkedIn data)

Run: uv run python -m app.tagging     (only new items are sent to the model)
"""
import json
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI

from app import catalog

load_dotenv()

DATA = Path(__file__).resolve().parent.parent / "data"
TALK_CACHE = DATA / "talk_topics.json"
POST_CACHE = DATA / "linkedin" / "post_topics.json"
MODEL = os.getenv("TAGGING_MODEL", "gpt-5.4-mini")
BATCH = 20

TOPICS = {
    "AI Agents": "autonomous or semi-autonomous coding/AI agents, agentic workflows, multi-agent systems",
    "AI Models & LLMs": "LLMs and models themselves: model choice, evals, cost, tokens, context, prompting, fine-tuning",
    "Copilot": "GitHub Copilot specifically (any surface: IDE, CLI, code review, coding agent)",
    "MCP": "Model Context Protocol servers, clients, tools",
    "Security": "application/supply-chain security, vulnerabilities, secrets, code scanning, compliance",
    "Open Source": "open source projects, maintainers, communities, licensing, funding",
    "Platform Engineering": "CI/CD, GitHub Actions, DevOps, infrastructure, internal platforms, reliability",
    "Developer Experience": "developer productivity, tooling, editors, workflows, inner loop, DX",
    "Enterprise Adoption": "rolling out tools in large orgs, governance, measuring impact/ROI, change management",
}

PROMPT = (
    "Tag each item with the topics it is substantively about, choosing ONLY from this list:\n"
    + "\n".join(f"- {name}: {desc}" for name, desc in TOPICS.items())
    + "\n\nRules: 0 to 2 topics per item, most relevant first. Use [] for chit-chat, congratulations, "
    "personal news or anything not clearly about a topic; a passing mention is not enough. "
    'Reply with JSON only: {"results": [{"id": "<id>", "topics": ["..."]}]} with one entry per item.'
)


def _load(path: Path) -> dict:
    return json.loads(path.read_text()) if path.exists() else {}


def talk_key(t: dict) -> str:
    return f"{t['title']} | {t['day']} | {t['start']}"


def talk_topics() -> dict:
    return _load(TALK_CACHE)


def post_topics() -> dict:
    return _load(POST_CACHE)


def _classify(client: OpenAI, items: dict[str, str]) -> dict[str, list[str]]:
    payload = json.dumps([{"id": k, "text": v} for k, v in items.items()], ensure_ascii=False)
    resp = client.chat.completions.create(
        model=MODEL,
        messages=[{"role": "system", "content": PROMPT}, {"role": "user", "content": payload}],
        response_format={"type": "json_object"},
    )
    out = {}
    for r in json.loads(resp.choices[0].message.content).get("results", []):
        if r.get("id") in items:
            out[r["id"]] = [t for t in r.get("topics", []) if t in TOPICS][:2]  # drop invented topics
    return out


def tag(items: dict[str, str], cache_path: Path, label: str):
    """Classify items missing from the cache, saving after each batch."""
    cache = _load(cache_path)
    todo = {k: v for k, v in items.items() if k not in cache and v.strip()}
    if not todo:
        print(f"{label}: all {len(items)} already tagged")
        return
    keys = list(todo)
    batches = [{k: todo[k] for k in keys[i:i + BATCH]} for i in range(0, len(keys), BATCH)]
    print(f"{label}: tagging {len(todo)} with {MODEL} in {len(batches)} batches...", flush=True)
    client = OpenAI()
    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = [pool.submit(_classify, client, b) for b in batches]
        for n, fut in enumerate(as_completed(futures), 1):
            try:
                cache.update(fut.result())
            except Exception as e:  # rerun the script to retry failed batches
                print(f"  batch failed: {e}")
                continue
            cache_path.write_text(json.dumps(cache, indent=1, ensure_ascii=False))
            print(f"  {n}/{len(batches)} batches done", flush=True)


def main():
    talks = catalog.parse()
    tag({talk_key(t): f"{t['title']}. {t['description']}" for t in talks}, TALK_CACHE, "Talks")

    posts = {}
    profiles = list(_load(DATA / "linkedin" / "profiles.json").values())
    for path in (DATA / "linkedin" / "attendees").glob("*.json"):
        rec = _load(path)
        profiles.append(rec[0] if isinstance(rec, list) else rec)
    for p in profiles:
        for a in p.get("activity") or []:
            if a.get("link"):
                posts[a["link"]] = (a.get("title") or "")[:1000]
    tag(posts, POST_CACHE, "Posts")


if __name__ == "__main__":
    main()
