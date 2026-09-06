"""
================================================================================
🌙 MOON DEV's SERVICE EXPORTER, for FLIP HARVESTER ML
================================================================================
Pulls window/fill history out of YOUR OWN bot service (the thing serving
/openapi.json) and writes a CSV that `flip_harvester_ml.py --replay` can train on.

    # 1. see what an endpoint actually returns, before trusting anything
    python export_from_service.py --url http://dragon:8787 --inspect

    # 2. pull one endpoint into a replay CSV
    python export_from_service.py --url http://dragon:8787 \
        --pull /api/polymarket/fleet/fills --out flip_history.csv

    # 3. fix any column it could not guess, then re-pull
    python export_from_service.py --url http://dragon:8787 \
        --pull /api/polymarket/fleet/fills --map dog_won=result --map touched=flipped

WHY THIS EXISTS: the brain trains on TWO labels per window, `touched` (did the dog
cross the strike) and `dog_won` (did it finish ahead), plus the feature columns.
Almost no other bot logs `touched`, so this tool does not pretend: it reports
exactly which required columns it found, which it guessed, and which are missing,
and it REFUSES to invent one. A model trained on a fabricated label is worse than
no model, and you would never know.

Field guessing is alias-based and always printed. `--map ours=theirs` overrides it.
Nothing is renamed silently.
Built by Moon Dev 🌙
================================================================================
"""

import os
import sys
import csv
import json
import argparse
import urllib.request
import urllib.error

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import features as FEAT

# ============================================================================
# 🌙 MOON DEV - WHAT WE NEED, AND WHAT WE'LL ACCEPT AS ITS NAME
# ============================================================================
# The two labels are non-negotiable. Everything else is a feature the enabled
# groups asked for (see features.py). Aliases are guesses, and every applied
# guess is printed, so a wrong one is visible instead of silent.
# ============================================================================
ALIASES = {
    'touched': ['touched', 'touch', 'flipped', 'did_touch', 'touch_hit', 'crossed', 'touched_strike'],
    'dog_won': ['dog_won', 'won', 'win', 'is_win', 'dog_win', 'result_win', 'underdog_won',
                'result', 'is_winner'],   # 'result' is usually WIN/LOSS, which to_bool reads
    'window_slug': ['window_slug', 'slug', 'market_slug', 'market', 'window', 'event_slug'],
    'market_ts': ['market_ts', 'window_ts', 'ts', 'timestamp', 'time', 'start_ts', 'window_start'],
    'dog_side': ['dog_side', 'side', 'outcome_side', 'position_side', 'dog'],
    'dog_ask': ['dog_ask', 'ask', 'entry_price', 'entry_ask', 'fill_price', 'price', 'avg_price'],
    'spread': ['spread', 'book_spread', 'bid_ask_spread'],
    'coa': ['coa', 'cushion_over_atr', 'coa_ratio'],
    'cushion_bps': ['cushion_bps', 'cushion', 'cushion_bp'],
    'atr4_bps': ['atr4_bps', 'atr4', 'atr_4', 'atr4_bp'],
    'atr_ratio': ['atr_ratio', 'atr_rel', 'vol_ratio'],
    'secs_left': ['secs_left', 'seconds_left', 'time_left', 'ttl_s', 't_minus'],
    'dog_is_up': ['dog_is_up', 'is_up', 'up'],
    'rate_mult': ['rate_mult', 'tick_rate_mult', 'volume_mult'],
    'hour_et': ['hour_et', 'hour', 'et_hour'],
}
REQUIRED_LABELS = ['touched', 'dog_won']
TRUEISH = ('true', '1', 'yes', 'y', 'win', 'won', 't')
FALSEISH = ('false', '0', 'no', 'n', 'loss', 'lost', 'lose', 'f')


def fetch(url, timeout=20):
    """🌙 Moon Dev - Plain GET, stdlib only, so this runs anywhere your service does."""
    try:
        req = urllib.request.Request(url, headers={'Accept': 'application/json'})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        print(f"❌ Moon Dev - HTTP {e.code} on {url}")
    except Exception as e:
        print(f"❌ Moon Dev - {type(e).__name__} on {url}: {e}")
    return None


def find_rows(payload):
    """🌙 Moon Dev - Dig the first list-of-dicts out of whatever shape came back.
    Services wrap rows in items/data/rows/results/fills/... , so look, don't assume."""
    if isinstance(payload, list):
        return payload if (not payload or isinstance(payload[0], dict)) else []
    if isinstance(payload, dict):
        for key in ('items', 'rows', 'data', 'results', 'fills', 'trades', 'windows',
                    'records', 'history', 'signals'):
            v = payload.get(key)
            if isinstance(v, list) and (not v or isinstance(v[0], dict)):
                return v
        for v in payload.values():                       # one level deeper, then give up
            if isinstance(v, list) and v and isinstance(v[0], dict):
                return v
    return []


def describe(payload, label):
    """🌙 Moon Dev - Print the SHAPE of a response, so nothing is mapped blind."""
    rows = find_rows(payload)
    if not rows:
        preview = json.dumps(payload)[:400] if payload is not None else "(no response)"
        print(f"   {label}: no list-of-records found | {preview}")
        return
    keys = sorted({k for r in rows[:50] for k in r})
    print(f"   {label}: {len(rows)} records, {len(keys)} columns")
    sample = rows[0]
    for k in keys:
        v = sample.get(k)
        s = json.dumps(v) if not isinstance(v, str) else v
        print(f"      {k:<28} = {s[:60]}")


def to_bool(value):
    """🌙 Moon Dev - Bool or None. NEVER a default, an unreadable label is dropped."""
    if isinstance(value, bool):
        return value
    if value is None:
        return None
    s = str(value).strip().lower()
    if s in TRUEISH:
        return True
    if s in FALSEISH:
        return False
    return None


def resolve_columns(rows, overrides):
    """🌙 Moon Dev - Decide which of THEIR columns feeds which of OURS, and say so."""
    present = {k for r in rows[:200] for k in r}
    needed = REQUIRED_LABELS + FEAT.active_feature_names()
    mapping, guessed, missing = {}, [], []
    for ours in needed:
        if ours in overrides:
            theirs = overrides[ours]
            if theirs not in present:
                print(f"   ⚠️  --map {ours}={theirs} but '{theirs}' is not in the payload")
                missing.append(ours)
                continue
            mapping[ours] = theirs
            continue
        for cand in ALIASES.get(ours, [ours]):
            if cand in present:
                mapping[ours] = cand
                if cand != ours:
                    guessed.append((ours, cand))
                break
        else:
            missing.append(ours)
    return mapping, guessed, missing


def export(url, path, out, overrides, limit):
    payload = fetch(f"{url.rstrip('/')}{path}" + (f"?limit={limit}" if limit else ""))
    if payload is None:
        return 1
    rows = find_rows(payload)
    if not rows:
        print("❌ Moon Dev - No records in that response. Run --inspect and pick another endpoint.")
        return 1
    print(f"🌙 Moon Dev - {len(rows)} records from {path}")

    mapping, guessed, missing = resolve_columns(rows, overrides)
    for ours, theirs in guessed:
        print(f"   🔎 guessed  {ours:<16} ← '{theirs}'   (override with --map {ours}=<column>)")
    if missing:
        print(f"   ❓ missing  {', '.join(missing)}")

    hard = [c for c in REQUIRED_LABELS if c in missing]
    if hard:
        print(f"\n❌ Moon Dev - Cannot build a training file without {', '.join(hard)}.")
        print("   These are LABELS, not features. I will not invent them, and a model trained")
        print("   on a fabricated label is worse than no model. Either point --pull at an")
        print("   endpoint that carries them, or --map them to the right column.")
        if 'touched' in hard:
            print("   NOTE: `touched` (did the dog cross the strike) is specific to this bot.")
            print("   If your service never logged it, this history cannot train touch_model,")
            print("   and the honest move is to let the bot label its own windows in PAPER_MODE.")
        return 2

    feature_cols = FEAT.active_feature_names()
    feats_missing = [c for c in feature_cols if c in missing]
    if feats_missing:
        print(f"\n❌ Moon Dev - Missing {len(feats_missing)} of {len(feature_cols)} feature columns: "
              f"{', '.join(feats_missing)}")
        print("   The model's input shape is fixed by the enabled groups. Either switch groups")
        print("   off (e.g. --groups core) so only what you have is required, or pull an")
        print("   endpoint that carries them.")
        return 2

    # 🌙 Moon Dev - slug/timestamp are not model inputs, so they are not in `needed`
    # and resolve_columns never looked for them. Carry them anyway: a training row you
    # cannot trace back to a window is a training row you cannot argue with later.
    present = {k for r in rows[:200] for k in r}
    slug_col = overrides.get('window_slug') or next(
        (c for c in ALIASES['window_slug'] if c in present), None)
    ts_col = overrides.get('market_ts') or next(
        (c for c in ALIASES['market_ts'] if c in present), None)

    written = dropped = 0
    with open(out, 'w', newline='') as f:
        w = csv.writer(f)
        w.writerow(['window_slug', 'market_ts', 'touched', 'dog_won', 'features_json'])
        for r in rows:
            touched = to_bool(r.get(mapping['touched']))
            dog_won = to_bool(r.get(mapping['dog_won']))
            if touched is None:
                dropped += 1
                continue
            try:
                x = {c: float(r[mapping[c]]) for c in feature_cols}
            except (KeyError, TypeError, ValueError):
                dropped += 1
                continue
            slug = r.get(slug_col, '') if slug_col else ''
            mts = r.get(ts_col, '') if ts_col else ''
            w.writerow([slug, mts, touched, '' if dog_won is None else dog_won,
                        json.dumps(x, sort_keys=True)])
            written += 1

    print(f"\n✅ Moon Dev - Wrote {written} rows to {out} ({dropped} dropped: unreadable label "
          f"or non-numeric feature)")
    if written:
        print(f"   Train on it:  python flip_harvester_ml.py --replay {out}")
        print("   ⚠️  Replay teaches the brain the PAST, on someone else's fills. It is a warm")
        print("       start, not a backtest, and not a reason to skip PAPER_MODE.")
    return 0 if written else 2


def main():
    ap = argparse.ArgumentParser(description="Moon Dev's service exporter → --replay CSV")
    ap.add_argument('--url', required=True, help="service base URL, e.g. http://dragon:8787")
    ap.add_argument('--inspect', action='store_true', help="print the shape of every JSON endpoint")
    ap.add_argument('--pull', metavar='PATH', help="endpoint path to export, e.g. /api/polymarket/fleet/fills")
    ap.add_argument('--out', default="flip_history.csv", help="output CSV (default flip_history.csv)")
    ap.add_argument('--limit', type=int, default=0, help="append ?limit=N to the request")
    ap.add_argument('--map', action='append', default=[], metavar='OURS=THEIRS',
                    help="force a column mapping, repeatable")
    ap.add_argument('--groups', help="feature groups to require, e.g. core (default: features.py)")
    args = ap.parse_args()

    if args.groups:
        FEAT.set_groups(args.groups)
    overrides = {}
    for m in args.map:
        if '=' not in m:
            print(f"❌ Moon Dev - bad --map '{m}', expected OURS=THEIRS")
            return 1
        k, v = m.split('=', 1)
        overrides[k.strip()] = v.strip()

    print(f"🌙 Moon Dev - service {args.url} | requiring {len(FEAT.active_feature_names())} features "
          f"{FEAT.active_groups()} + labels {REQUIRED_LABELS}")

    if args.inspect:
        spec = fetch(f"{args.url.rstrip('/')}/openapi.json")
        if not spec:
            return 1
        paths = [p for p, ops in (spec.get('paths') or {}).items() if 'get' in ops]
        print(f"🌙 Moon Dev - {len(paths)} GET endpoints, fetching each to see its shape...\n")
        for p in paths:
            if '{' in p:                       # path params, nothing to guess them with
                print(f"   {p}: skipped (needs a path parameter)")
                continue
            describe(fetch(f"{args.url.rstrip('/')}{p}"), p)
        print("\n🌙 Moon Dev - Now pick one:  --pull <path> --out flip_history.csv")
        return 0

    if not args.pull:
        print("❌ Moon Dev - Nothing to do. Use --inspect first, then --pull <path>.")
        return 1
    return export(args.url, args.pull, args.out, overrides, args.limit)


if __name__ == "__main__":
    sys.exit(main())
