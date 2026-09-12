"""Live dry run of /compare: real FaceIT + Leetify APIs, no Discord. Prints the verdict, writes the card.

    python tools/compare_dry.py BRNWr szucsu [recent|all]

Uses the committed state.json read-only (scratch copy) plus LEETIFY_API_KEY from .env if set.
"""
from __future__ import annotations

import asyncio
import logging
import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
OUT = ROOT / "out" / "compare"

_scratch = Path(tempfile.mkdtemp(prefix="shamebot-compare-")) / "state.json"
if (ROOT / "state.json").is_file():
    shutil.copy(ROOT / "state.json", _scratch)
os.environ["STATE_FILE"] = str(_scratch)
os.environ.setdefault("DISCORD_TOKEN", "dry")
os.environ.setdefault("SHAME_CHANNEL_ID", "1")

from shamebot.app import App  # noqa: E402
from shamebot.config import Settings  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")


async def main() -> None:
    if len(sys.argv) < 3:
        sys.exit(__doc__)
    a, b = sys.argv[1], sys.argv[2]
    scope = sys.argv[3] if len(sys.argv) > 3 else "recent"
    OUT.mkdir(parents=True, exist_ok=True)
    app = App(Settings())
    app.state.load()
    await app.resolve_players()
    pa, pb = app.pid_for_nick(a), app.pid_for_nick(b)
    if not pa or not pb:
        sys.exit(f"unknown player(s); tracked: {list(app.tracked.values())}")
    # the enrich loop is not running here: upgrade the window records on the spot so v3 metrics exist
    for pid in (pa, pb):
        for rec in app.recent_records(pid):
            if rec.v < 3:
                fresh = await app.fetch_record(rec.match_id, finished_at=rec.finished_at, with_details=False)
                if fresh:
                    for p, r in fresh.players.items():
                        if p in rec.players:
                            r.elo_after, r.elo_delta = rec.players[p].elo_after, rec.players[p].elo_delta
                    app.detect(fresh)
                    app.state.put_match(fresh)
                await asyncio.sleep(0.3)
    res, png = await app.compare(pa, pb, scope=scope)
    path = OUT / f"{a}_vs_{b}_{scope}.png"
    path.write_bytes(png)
    print("\n=== VERDICT ===")
    print(res.verdict)
    print(res.jab)
    for r in res.reasons:
        print(" -", r)
    for n in res.notes:
        print(" !", n)
    print("\n=== CATEGORIES ===")
    for c in res.categories:
        print(f"{c.name:<12} w{c.weight} score={c.score if c.score is None else round(c.score, 2)} winner={c.winner}")
        for m in c.metrics:
            print(f"    {m.label:<28} {m.text('a'):>10} | {m.text('b'):<10} {'<' if m.better == -1 else ('>' if m.better == 1 else '=')}  [{m.source}]")
    print("\ncard ->", path.relative_to(ROOT))
    await app.close()


if __name__ == "__main__":
    asyncio.run(main())
