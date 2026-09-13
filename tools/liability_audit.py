"""Offline audit: which cached matches would be "Liability" posts at a given blame-share threshold?

    python tools/liability_audit.py [state.json] [--share 35] [--margin 3] [--limit 400]

Reads the state file only (no API, no Discord). Use it to tune LIABILITY_BLAME_SHARE before enabling
the posts: it prints every qualifying match newest-first plus a per-player count and the near misses.
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from shamebot.blame import compute_blame  # noqa: E402
from shamebot.rules import Thresholds, grey_zone_reasons, is_liability  # noqa: E402
from shamebot.state import State  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("state", nargs="?", default="state.json")
    ap.add_argument("--share", type=int, default=35, help="LIABILITY_BLAME_SHARE to test")
    ap.add_argument("--margin", type=int, default=3, help="LIABILITY_CLOSE_WIN_MARGIN to test")
    ap.add_argument("--kills", type=int, default=10, help="KILL_THRESHOLD")
    ap.add_argument("--max-kd", type=float, default=0.9, help="LIABILITY_MAX_KD")
    ap.add_argument("--limit", type=int, default=400, help="newest N matches per player")
    args = ap.parse_args()

    st = State(args.state)
    st.load()
    th = Thresholds(kill_threshold=args.kills, liability_blame_share=args.share, liability_close_win_margin=args.margin, liability_max_kd=args.max_kd)
    near = Thresholds(kill_threshold=args.kills, liability_blame_share=max(0, args.share - 10), liability_close_win_margin=args.margin, liability_max_kd=args.max_kd)

    grey: list[tuple[int, str, str, int, str, str]] = []
    hits: list[tuple[int, str, str, int, bool, str]] = []
    misses: list[tuple[int, str, str, int, bool]] = []
    per_player: dict[str, int] = {}
    games: dict[str, int] = {}
    for pid, snap in st.players.items():
        for m in st.matches_for_player(pid)[: args.limit]:
            r = m.players[pid]
            if r.result is None or r.kills < args.kills or not m.teams:
                continue
            games[snap.nickname] = games.get(snap.nickname, 0) + 1
            reasons = grey_zone_reasons(m, pid, th)
            if reasons:
                grey.append((m.finished_at, snap.nickname, m.match_id, r.kills, f"{r.team_score}-{r.enemy_score}", " · ".join(reasons)))
                continue
            b = is_liability(m, pid, th)
            if b is not None:
                per_player[snap.nickname] = per_player.get(snap.nickname, 0) + 1
                hits.append((m.finished_at, snap.nickname, m.match_id, r.kills, r.result == 1, f"{r.team_score}-{r.enemy_score} · {b.share}% · " + " · ".join(b.facts)))
            elif is_liability(m, pid, near) is not None:
                nb = compute_blame(m, pid)
                misses.append((m.finished_at, snap.nickname, m.match_id, r.kills, nb.share if nb else 0))

    hits.sort(reverse=True)
    print(f"=== {len(hits)} liability post(s) at share>={args.share}%, K/D<{args.max_kd}, close-win margin<={args.margin} (last {args.limit} games/player) ===")
    for ts, nick, mid, kills, won, detail in hits:
        when = datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%d")
        print(f"{when}  {nick:<12} {kills:>2} K  {'WIN ' if won else 'LOSS'}  {detail}   {mid}")
    print("\n=== per player (liabilities / games with >= threshold kills) ===")
    for nick in sorted(games, key=lambda n: -per_player.get(n, 0)):
        n = per_player.get(nick, 0)
        print(f"  {nick:<12} {n:>3} / {games[nick]:<4} ({100 * n / games[nick]:.0f}%)")
    grey.sort(reverse=True)
    print(f"\n=== {len(grey)} grey-zone wall post(s) ({args.kills}-{args.kills + th.grey_zone - 1} kills, stats said otherwise) ===")
    for ts, nick, mid, kills, score, why in grey:
        when = datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%d")
        print(f"{when}  {nick:<12} {kills:>2} K  {score:>6}  {why}   {mid}")
    misses.sort(reverse=True)
    print(f"\n=== {len(misses)} near miss(es) between {max(0, args.share - 10)}% and {args.share}% ===")
    for ts, nick, mid, kills, share in misses[:25]:
        when = datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%d")
        print(f"{when}  {nick:<12} {kills:>2} K  {share}%   {mid}")


if __name__ == "__main__":
    main()
