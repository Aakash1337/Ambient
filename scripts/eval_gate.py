"""Labelled evaluation of the question gate.

Run this after touching heuristics or the Stage B prompts:

    .venv\\Scripts\\python.exe scripts\\eval_gate.py [--mode balanced]

Each case is (context_lines, utterance, should_answer). Context matters: some
questions are only resolvable against what was said before, and some fragments
are only tempting because the context nearby is interesting.
"""
from __future__ import annotations

import argparse
import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ambientqa.bus import Transcript  # noqa: E402
from ambientqa.config import load_config  # noqa: E402
from ambientqa.gate import QuestionGate  # noqa: E402
from ambientqa.profile import load_profile  # noqa: E402

TECH = ["I was refactoring the retry logic in the client", "it wraps fetch with a backoff"]
MEET = ["so the migration is scheduled for Thursday", "we still need signoff from platform"]

CASES: list[tuple[list[str], str, bool]] = [
    # --- explicit questions -------------------------------------------------
    ([], "what's the default timeout for fetch in node?", True),
    ([], "how do I flush a socket in python?", True),
    ([], "why does postgres pick a sequential scan there?", True),
    (TECH, "what's the difference between exponential and linear backoff?", True),
    # --- implicit information needs (balanced mode should catch these) -------
    ([], "hmm I have no idea how python decorators handle arguments", True),
    ([], "I wonder how much memory a python dict actually uses", True),
    ([], "remind me how git rebase interactive works", True),
    ([], "I can never remember the syntax for a bash case statement", True),
    # --- referential: need context to resolve --------------------------------
    (TECH, "wait, how does that interact with keep-alive?", True),
    (MEET, "who owns that service again?", True),
    # --- rhetorical / tag questions ------------------------------------------
    (TECH, "and then the retry logic kicks in, right?", False),
    ([], "I mean it works, you know?", False),
    ([], "that's wild, isn't it?", False),
    ([], "we shipped it already, okay?", False),
    # --- filler and trailing-off fragments -----------------------------------
    (TECH, "uh, um, so, the thing is", False),
    (TECH, "and then it was like, you know, just", False),
    ([], "uh huh yeah okay", False),
    (MEET, "so I mean, it was, well", False),
    # --- directed at another human -------------------------------------------
    ([], "hey Sarah, can you pass me the charger", False),
    ([], "Mike, could you review that PR today", False),
    ([], "can you grab me a coffee while you're up", False),
    # --- plain self-narration / statements -----------------------------------
    ([], "so anyway I was heading to the store later", False),
    (TECH, "I'm going to bump the timeout to thirty seconds", False),
    (MEET, "we still need signoff from platform before Thursday", False),
    ([], "this test suite takes forever to run", False),
    ([], "okay that fixed it, moving on", False),
]


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", default=None, help="strict | balanced | eager")
    ap.add_argument("--profile", default="", help="optional Markdown profile path")
    args = ap.parse_args()

    cfg = load_config()
    if args.mode:
        cfg.gate.mode = args.mode
    gate = QuestionGate(cfg.gate)
    if args.profile:
        gate.set_profile(load_profile(args.profile, print))
    print(f"mode = {cfg.gate.mode}")
    print(f"profile = {args.profile or '(none)'}")
    print(f"ollama warmup = {await gate.ollama.warmup()}\n")

    tp = fp = tn = fn = 0
    latencies: list[float] = []
    failures: list[str] = []

    for context, text, expected in CASES:
        t0 = time.perf_counter()
        res = await gate.evaluate(Transcript("mic", text, time.time(), "e"), list(context))
        latencies.append((time.perf_counter() - t0) * 1000)
        got = res.accepted
        if got and expected:
            tp += 1
        elif got and not expected:
            fp += 1
            failures.append(f"  FALSE POSITIVE  {text!r}\n        -> {res.reason} / {res.query!r}")
        elif not got and expected:
            fn += 1
            failures.append(f"  FALSE NEGATIVE  {text!r}\n        -> {res.reason}")
        else:
            tn += 1

    total = len(CASES)
    correct = tp + tn
    latencies.sort()
    print(f"accuracy      {correct}/{total}  ({100*correct/total:.1f}%)")
    print(f"true pos {tp}   true neg {tn}   FALSE POS {fp}   FALSE NEG {fn}")
    print(f"latency       median {latencies[len(latencies)//2]:.0f}ms   "
          f"p95 {latencies[int(len(latencies)*0.95)-1]:.0f}ms")
    if failures:
        print("\nfailures:")
        print("\n".join(failures))
    # False positives are the worst failure: they interrupt with an unasked answer.
    return 1 if fp else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
