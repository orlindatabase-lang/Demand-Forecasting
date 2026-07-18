"""
Local-LLM cross-tracker delay analysis.

Pulls current data from every production tracker already served by the live
API (Job Work, Inhouse, FOB, Embroidery, Purchase Order, Bottleneck) and asks
a locally-run LLM (via Ollama) to explain WHERE production is delayed and WHY,
grounded in the real numbers. No cloud API calls, nothing leaves this machine.

Prereqs
    1. Install Ollama:  https://ollama.com/download
       (or, on Windows with winget:  winget install Ollama.Ollama)
    2. Pull a model:    ollama pull llama3.1:8b
    3. The FastAPI backend must be running on http://127.0.0.1:8000
       (it auto-starts at login per this project's setup — see api/serve_forever.ps1)

Run
    venv\\Scripts\\python.exe local_llm_delay_analysis.py
"""
from __future__ import annotations

import sys
from datetime import date

import requests

API_BASE   = "http://127.0.0.1:8000"
OLLAMA_URL = "http://localhost:11434/api/generate"
MODEL      = "llama3.1:8b"     # swap for any model you've `ollama pull`ed
TOP_N      = 15                # worst lots per tracker fed to the model

_RISK_SCORE = {"Delayed": 3, "At Risk": 2, "On Track": 1, "Completed": 0}


def _get(path: str) -> dict:
    r = requests.get(f"{API_BASE}{path}", timeout=30)
    r.raise_for_status()
    return r.json()


def _fmt_lot(r: dict) -> str:
    bits = [r.get("lotNo", "?")]
    if r.get("design"):  bits.append(f"design={r['design']}")
    if r.get("section"): bits.append(f"section={r['section']}")
    if r.get("vendor"):  bits.append(f"vendor={r['vendor']}")
    if r.get("process"): bits.append(f"process={r['process']}")
    bits.append(f"age={r.get('ageDays', '?')}d")
    if r.get("expectedDays") is not None:
        bits.append(f"expected={r['expectedDays']}d")
    bits.append(f"risk={r.get('riskLevel', '?')}")
    if r.get("delayProb") is not None:
        bits.append(f"AI_risk={round(r['delayProb'] * 100)}%({r.get('riskBand')})")
    return "  - " + " | ".join(bits)


def _tracker_section(title: str, data: dict) -> str:
    items     = data.get("items", [])
    total     = data.get("total", len(items))
    delayed   = data.get("delayed", 0)
    at_risk   = data.get("atRisk", 0)
    on_track  = data.get("onTrack", 0)
    completed = data.get("completed", 0)

    open_items = [r for r in items if r.get("lotStatus", "Open") == "Open"]
    worst = sorted(
        open_items,
        key=lambda r: (_RISK_SCORE.get(r.get("riskLevel"), 0), r.get("ageDays", 0)),
        reverse=True,
    )[:TOP_N]

    lines = [
        f"## {title}",
        f"Total: {total}  |  Delayed: {delayed}  At Risk: {at_risk}  "
        f"On Track: {on_track}  Completed: {completed}",
        f"Worst {len(worst)} open lots:",
    ]
    lines += [_fmt_lot(r) for r in worst] or ["  (none open)"]
    return "\n".join(lines)


def _bottleneck_section(data: dict) -> str:
    lines = ["## Bottleneck Detection (open lots issued in the last 90 days)"]
    for p in data.get("processes", []):
        lines.append(
            f"  - {p['process']}: severity={p['severity']} score={p['bottleneckScore']} "
            f"openLots={p['openLots']} delayed={p['delayedLots']} "
            f"avgAge={p['avgAgeDays']}d avgExpected={p['avgExpectedDays']}d "
            f"overrun={p['avgOverrunDays']}d"
        )
    lines.append(f"Worst process overall: {data.get('worstProcess')}")
    lines.append("Top 10 worst individual lots:")
    for r in data.get("lots", [])[:10]:
        lines.append(
            f"  - {r['lotNo']} design={r['design']} process={r['process']} "
            f"risk={r['riskLevel']} age={r['ageDays']}d overrun={r['overrunDays']}d "
            f"pending={r['pendingPieces']}pcs"
        )
    return "\n".join(lines)


def gather_context() -> str:
    print("Pulling live data from the API...", file=sys.stderr)
    try:
        job_work   = _get("/api/production/job-work")
        inhouse    = _get("/api/production/inhouse")
        fob        = _get("/api/production/fob")
        embroidery = _get("/api/production/embroidery")
        po         = _get("/api/production/purchase-order")
        bottleneck = _get("/api/production/bottlenecks")
    except requests.ConnectionError:
        sys.exit(
            f"Could not reach the API at {API_BASE} — is it running?\n"
            r"Check for the running process, or start it: cd api && ..\venv\Scripts\python.exe run.py"
        )

    sections = [
        _tracker_section("Job Work", job_work),
        _tracker_section("Inhouse", inhouse),
        _tracker_section("FOB", fob),
        _tracker_section("Embroidery", embroidery),
        _tracker_section("Purchase Order", po),
        _bottleneck_section(bottleneck),
    ]
    return "\n\n".join(sections)


PROMPT_TEMPLATE = """You are a production-planning analyst for a garment manufacturer. \
Below is live data pulled from every production tracker as of {today}. Some lots \
already carry an AI-predicted delay risk (AI_risk / riskBand) from a trained model; \
the rest carry a rule-based risk (age vs. the expected time for that stage).

{context}

Using ONLY the numbers above (do not invent figures), write a delay diagnosis with \
these three sections:

1. WHERE — which tracker(s), process(es), vendor(s), or section(s) have the most \
   delayed/at-risk lots right now. Name specific lot numbers where useful.
2. WHY — the likely root cause for each hotspot: one slow process stage, a specific \
   vendor, a specific section/design, or lots that are simply old. Point to the \
   specific numbers that support each claim.
3. PRIORITY ACTIONS — a short ranked list (max 5) of what to chase first, and why \
   that one first.

Be concrete and specific. Do not restate the raw data back verbatim — synthesize it.
"""


def ask_local_llm(context: str) -> str:
    prompt = PROMPT_TEMPLATE.format(today=date.today().isoformat(), context=context)
    print(f"Sending to local model '{MODEL}' via Ollama (this can take a minute on CPU)...",
          file=sys.stderr)
    try:
        r = requests.post(
            OLLAMA_URL,
            json={
                "model": MODEL,
                "prompt": prompt,
                "stream": False,
                "options": {"num_ctx": 8192, "temperature": 0.2},
            },
            timeout=600,
        )
    except requests.ConnectionError:
        sys.exit(
            "Could not reach Ollama at http://localhost:11434 — is it running?\n"
            "Install: https://ollama.com/download  |  then:  ollama serve\n"
            f"And make sure the model is pulled:  ollama pull {MODEL}"
        )
    if r.status_code == 404:
        sys.exit(f"Ollama doesn't have model '{MODEL}' yet. Run:  ollama pull {MODEL}")
    r.raise_for_status()
    data = r.json()
    if "error" in data:
        sys.exit(f"Ollama error: {data['error']}")
    return data["response"]


def main() -> None:
    context = gather_context()
    report = ask_local_llm(context)
    print("\n" + "=" * 70)
    print("WHERE & WHY PRODUCTION IS DELAYED — Local LLM Analysis")
    print(f"Generated {date.today().isoformat()} by '{MODEL}' (running locally via Ollama)")
    print("=" * 70 + "\n")
    print(report.strip())


if __name__ == "__main__":
    main()
