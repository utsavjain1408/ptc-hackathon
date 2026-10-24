"""Parse the GitHub Universe 2026 session catalog into talks and speakers.

The catalog was rendered with the Bright Data Browser API (`bdata browser open` + "Show more"
until exhausted + `bdata browser get text`) and saved to data/universe26_catalog.txt.
"""
import re
from pathlib import Path

CATALOG = Path(__file__).resolve().parent.parent / "data" / "universe26_catalog.txt"

TYPES = {
    "Keynote", "Breakout Session", "Product Demo", "Workshop", "Sandbox Session", "Ship & Tell",
    "Main Stage", "Session After Party", "Special Event", "Social", "Panel Discussion",
}
TAGS = {"In-person", "Recorded", "Livestream", "AI summary"}
DAY = re.compile(r"^(Mon|Tues|Wednes|Thurs|Fri)day, Oct \d+$")
TIME = re.compile(r"^(\d{1,2}:\d{2} [AP]M) - (\d{1,2}:\d{2} [AP]M) \w+$")

# Keyword fallback for topics; app.tagging (LLM) is the primary tagger. The catalog page has no topic tags.
TOPICS = {
    "AI Agents": ["agent", "agentic", "autonomous", "coding agent"],
    "Copilot": ["copilot"],
    "MCP": ["mcp", "model context protocol"],
    "Security": ["security", "secure", "vulnerab", "supply chain", "secret", "codeql", "dependabot"],
    "Open Source": ["open source", "maintainer", "oss "],
    "Platform Engineering": ["actions", "ci/cd", "devops", "pipeline", "platform", "kubernetes", "infrastructure"],
    "Developer Experience": ["developer experience", "devex", "workflow", "productivity", "vs code", "github cli", "command line"],
    "Enterprise Adoption": ["enterprise", "rollout", "adoption", "measur", "metrics", "roi", "governance"],
    "AI Models & LLMs": ["llm", "language model", "gpt", "claude", "gemini", "inference", "fine-tun", "token"],
}


def topics(text: str) -> list[str]:
    text = text.lower()
    return [t for t, words in TOPICS.items() if any(re.search(rf"\b{re.escape(w)}", text) for w in words)]


def _speaker(name: str, role: str) -> dict:
    # role looks like "/Staff Developer Advocate/GitHub" or "/Executive Vice President/CoreAI/Microsoft"
    parts = [p.strip() for p in role.strip("/").split("/") if p.strip()]
    company = parts[-1] if len(parts) > 1 else None
    title = ", ".join(parts[:-1]) if len(parts) > 1 else (parts[0] if parts else None)
    return {"name": name.strip(), "title": title, "company": company}


def parse(path: Path = CATALOG) -> list[dict]:
    lines = [l.strip() for l in path.read_text().splitlines()]
    lines = [l for l in lines if l]
    ends = [i for i, l in enumerate(lines) if l == "See details"]

    talks, start = [], None
    # The first entry starts right after the "Thu, Oct 29 (63)" day filter.
    start = next(i for i, l in enumerate(lines) if l.startswith("Thu, Oct 29 (")) + 1
    for end in ends:
        body = lines[start:end]
        j = end + 1
        if lines[j] == "Add to agenda":
            j += 1
        day, time = lines[j], TIME.match(lines[j + 1])
        assert DAY.match(day) and time, f"unexpected entry tail near line {end}: {lines[j:j + 3]}"
        j += 2
        room = None
        if j < len(lines) and ("@" in lines[j] or (j + 1 < len(lines) and lines[j + 1] not in TYPES)):
            room = lines[j]
            j += 1
        start = j

        title, kind, rest = body[0], body[1], body[2:]
        speakers, desc = [], []
        for k, line in enumerate(rest):
            if line.startswith("/") and k > 0:
                speakers.append(_speaker(rest[k - 1], line))
        first_speaker = next((k - 1 for k, l in enumerate(rest) if l.startswith("/") and k > 0), len(rest))
        desc = [l for l in rest[:first_speaker] if l not in TAGS and l != "Add to agenda"]
        description = " ".join(desc)

        talks.append({
            "title": title,
            "type": kind,
            "day": day,
            "start": time.group(1),
            "end": time.group(2),
            "room": room,
            "description": description,
            "speakers": speakers,
            "topics": topics(f"{title} {description}"),
        })
    return talks


if __name__ == "__main__":
    talks = parse()
    people = {s["name"] for t in talks for s in t["speakers"]}
    print(f"{len(talks)} talks, {len(people)} speakers")
    for t in talks[:3]:
        print(t["title"], "|", t["type"], "|", t["day"], t["start"], "|", t["room"], "|", t["topics"],
              "|", [(s["name"], s["company"]) for s in t["speakers"]])
