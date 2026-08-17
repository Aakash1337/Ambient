"""Render a session JSONL into a self-contained HTML replay of the live pane.

Usage:
    python scripts/render_session.py [logs/session-XXXX.jsonl] [-o out.html]

With no argument, renders the newest session in logs/. The output mimics the
live TUI: dim timestamped transcript lines interleaved with Q&A cards, plus a
summary header the live pane never had room for.
"""

from __future__ import annotations

import argparse
import html
import json
import re
from datetime import datetime
from pathlib import Path

_FENCE_RE = re.compile(r"```[^\n]*\n(.*?)(?:^\s*```\s*$|\Z)", re.DOTALL | re.MULTILINE)


def _text_block(text: str) -> str:
    """Escape prose but keep fenced code as real <pre> blocks."""
    parts: list[str] = []
    cursor = 0
    for match in _FENCE_RE.finditer(text):
        before = text[cursor : match.start()]
        if before.strip():
            parts.append(f"<div class='prose'>{html.escape(before.strip())}</div>")
        parts.append(f"<pre>{html.escape(match.group(1).rstrip())}</pre>")
        cursor = match.end()
    tail = text[cursor:]
    if tail.strip():
        parts.append(f"<div class='prose'>{html.escape(tail.strip())}</div>")
    return "".join(parts) or "<div class='prose'></div>"


def _clock(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp).strftime("%H:%M:%S")


def render(records: list[dict], source_name: str) -> str:
    answered = [r for r in records if r.get("gate")]
    ok = [r for r in answered if r.get("answer_status") == "ok"]
    latencies = sorted(
        r["latencies_ms"].get("answer", 0) / 1000
        for r in ok
        if r.get("latencies_ms", {}).get("answer")
    )
    median = latencies[len(latencies) // 2] if latencies else 0.0
    span = ""
    if records:
        span = f"{_clock(records[0]['timestamp'])} – {_clock(records[-1]['timestamp'])}"

    rows: list[str] = []
    for rec in sorted(records, key=lambda r: r.get("timestamp", 0)):
        stamp = _clock(rec.get("timestamp", 0))
        channel = rec.get("channel", "?")
        text = rec.get("text", "")
        if not rec.get("gate"):
            reason = rec.get("gate_reason", "")
            rows.append(
                f"<div class='line'><span class='t'>{stamp}</span>"
                f"<span class='ch'>[{channel}]</span>"
                f"<span class='tx'>{html.escape(text)}</span>"
                f"<span class='why'>{html.escape(reason)}</span></div>"
            )
            continue
        status = rec.get("answer_status", "ok")
        badges = []
        if rec.get("gate_reason") == "forced_by_user":
            badges.append("<span class='badge forced'>forced</span>")
        if rec.get("web_lookup"):
            badges.append("<span class='badge web'>web lookup</span>")
        if status != "ok":
            badges.append(f"<span class='badge err'>{html.escape(status)}</span>")
        lat = rec.get("latencies_ms", {})
        total = (lat.get("stt", 0) + lat.get("gate", 0) + lat.get("answer", 0)) / 1000
        rows.append(
            "<div class='card'>"
            f"<div class='q'>Q&nbsp;&nbsp;{html.escape(rec.get('query') or text)}"
            f"{''.join(badges)}<span class='lat'>{stamp} · {total:.1f}s</span></div>"
            f"<div class='a'>{_text_block(rec.get('answer') or '')}</div>"
            "</div>"
        )

    return f"""<!doctype html>
<meta charset="utf-8">
<title>{html.escape(source_name)}</title>
<style>
  body {{ background:#121212; color:#d6d6d6; font:14px/1.55 "Cascadia Mono","Consolas",monospace;
         max-width:1000px; margin:2rem auto; padding:0 1rem; }}
  header {{ color:#9a9a9a; border-bottom:1px solid #2c2c2c; padding-bottom:.8rem; margin-bottom:1.2rem; }}
  header b {{ color:#e8a33d; }}
  .line {{ color:#8a8a8a; padding:.06rem 0; }}
  .line .t {{ color:#5f5f5f; margin-right:.7em; }}
  .line .ch {{ color:#6f8f6f; margin-right:.7em; }}
  .line .why {{ float:right; color:#4d4d4d; font-size:.85em; }}
  .card {{ border:1px solid #e8a33d; border-radius:8px; margin:.9rem 0; padding:.7rem 1rem; }}
  .card .q {{ color:#e8a33d; font-weight:bold; }}
  .card .a {{ margin-top:.55rem; white-space:pre-wrap; }}
  .card pre {{ background:#1c1c1c; border:1px solid #2c2c2c; border-radius:6px;
               padding:.6rem .8rem; overflow-x:auto; white-space:pre; }}
  .badge {{ font-weight:normal; font-size:.78em; border-radius:4px; padding:.05rem .45rem; margin-left:.6em; }}
  .badge.forced {{ background:#3d3320; color:#e8a33d; }}
  .badge.web {{ background:#20303d; color:#6fb3e8; }}
  .badge.err {{ background:#3d2020; color:#e86f6f; }}
  .lat {{ float:right; color:#5f5f5f; font-weight:normal; font-size:.85em; }}
</style>
<header>
  <b>{html.escape(source_name)}</b> &nbsp; {span}<br>
  {len(records)} utterances · {len(answered)} answered
  ({len(ok)} ok) · median answer {median:.1f}s
</header>
{''.join(rows)}
"""


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("session", nargs="?", help="session .jsonl (default: newest)")
    parser.add_argument("-o", "--output", help="output .html path")
    args = parser.parse_args()

    if args.session:
        path = Path(args.session)
    else:
        candidates = sorted(Path("logs").glob("session-*.jsonl"))
        if not candidates:
            raise SystemExit("no session logs found in logs/")
        path = candidates[-1]

    records = [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    out = Path(args.output) if args.output else path.with_suffix(".html")
    out.write_text(render(records, path.stem), encoding="utf-8")
    print(f"wrote {out} ({len(records)} utterances)")


if __name__ == "__main__":
    main()
