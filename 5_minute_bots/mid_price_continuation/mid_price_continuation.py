"""
================================================================================
🌙 MOON DEV's MID-PRICE CONTINUATION 40-55 v1.0  (kimik3-july18 fleet #1)
================================================================================
Born from pooled forensics across data/lag_arb_trades*.csv (1,372 deduped live
trades, 318 BTC rows). The lag-arb family was benched for its EXPENSIVE bands
(60-70c: -12% EV; 80-90c: -7% EV) — but the 40-55c leading-side entries were
the cells that paid: n=168, ~60.5% win @ 0.475 avg → +15-30% EV, positive in
6/8 bot versions. This bot takes ONLY those cells. Hard cap 0.55. No chase.

THE TRADE (BTC only):
  - Strike = the market's OWN price-to-beat (Polymarket crypto-price openPrice)
  - Spot  = Hyperliquid BTC mark price
  - ENTER when ALL: |itm_pct| >= 0.05% through the strike, 120-300s left,
    leading-side ask 0.40-0.55, ask depth >= our size. Taker (GTC crossable).
  - One entry per window. Hold to resolution.

HONEST P&L (the round-2 fix, non-negotiable):
  - Trades resolve ONLY on definitive data: crypto-price completed==true
    (close vs open) or Gamma outcomePrices exactly 1/0. Polled every cycle,
    15s grace after close, retried forever — PENDING is never faked.
  - Unfilled GTCs are marked NO_FILL at rollover — never pollute the P&L.
  - Dashboard shows W / L / PENDING separately. P&L counts resolved only.

LOGGING (so the next iteration can re-mine us):
  - data/mid_price_continuation_eval.csv   — EVERY window: entries AND skips
  - data/mid_price_continuation_trades.csv — fills: signal_ask vs fill_price,
    fill_slippage, resolution W/L/PENDING persisted

🔴 PAPER_MODE = False — LIVE FIRE on the AUG14 account, $5 flat. Flip to True to paper.

📦 SINGLE-FILE: only pip dep is py_clob_client_v2. Repo-root .env required.
Account: AUG14 (signature_type 2, Gnosis Safe — V1 post_order is DEAD).
Built by Moon Dev 🌙
================================================================================
"""

import sys
import os
import time
import json
import csv
import math
import itertools
from collections import deque
import requests
from datetime import datetime, timezone, timedelta

# ============================================================================
# 🌙 MOON DEV - ZERO-DEP STAND-INS (stdlib only, py_clob_client_v2 is the only pip)
# ============================================================================
ANSI = {"red": "31", "green": "32", "yellow": "33", "magenta": "35", "cyan": "36", "white": "37"}


def colored(text, color=None, attrs=None):
    """🌙 Moon Dev - inline termcolor replacement (ANSI escapes, stdlib only)."""
    codes = (["1"] if attrs and "bold" in attrs else []) + ([ANSI[color]] if color in ANSI else [])
    return f"\033[{';'.join(codes)}m{text}\033[0m" if codes else str(text)


def load_env(path):
    """🌙 Moon Dev - inline dotenv replacement: parse KEY=VALUE lines into os.environ."""
    if not os.path.exists(path):
        return
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


# ============================================================================
# 🌙 MOON DEV - PATH / ENV / ACCOUNT (AUG14 — multi-account structure)
# ============================================================================
BOT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(os.path.dirname(BOT_DIR))
load_env(os.path.join(PROJECT_ROOT, '.env'))

ACCOUNT_SUFFIX = "_AUG14"
PRIVATE_KEY_ENV_NAME = f"PRIVATE_KEY{ACCOUNT_SUFFIX}"
PUBLIC_KEY_ENV_NAME = f"PUBLIC_KEY{ACCOUNT_SUFFIX}"
SIGNATURE_TYPE = 1 if ACCOUNT_SUFFIX == "" else 2  # OG=1, sub-accounts=2 (Gnosis Safe)

# ============================================================================
# 🌙 MOON DEV - KEY STRATEGY PARAMS (the mined cells — the whole edge lives here)
# ============================================================================
MIN_ITM_PCT = 0.05           # 🎯 spot must be >= this % through the strike (leading side)
PRICE_BAND = (0.40, 0.55)    # 💵 the paying cells from the pooled logs. HARD CAP 0.55 — never chase
TIME_BAND = (120, 300)       # ⏱ seconds remaining: enter from window open until 2:00 left
USD_SIZE = 5                 # 💰 flat stake per entry ($5 test size)
DAILY_STOP_LOSS = -30        # 🛑 halt new entries if realized day P&L hits this
PAPER_MODE = False           # 🔴 LIVE on AUG14. True = log would-be fills only

MIN_SHARES = 5               # Polymarket 5-share minimum per order
MARKET_DURATION = 300        # 5-minute windows
CHECK_INTERVAL = 3           # re-check a hot book every X sec
RESOLVE_GRACE_SEC = 15       # first resolution poll this many sec after close
ET = timezone(timedelta(hours=-4))

DATA_DIR = os.path.join(BOT_DIR, "data")
EVAL_LOG = os.path.join(DATA_DIR, "mid_price_continuation_eval.csv")
TRADES_FILE = os.path.join(DATA_DIR, "mid_price_continuation_trades.csv")
EVAL_FIELDS = ["timestamp", "window_ts", "slug", "strike", "spot", "itm_pct", "side",
               "best_bid", "best_ask", "minutes_left", "action", "paper"]
TRADE_FIELDS = ["timestamp", "market_ts", "slug", "side", "signal_ask", "entry_ask",
                "fill_slippage", "shares", "usd", "result", "resolved_winner", "pnl_usd", "paper"]

# ============================================================================
# 🌙 MOON DEV - SESSION STATS
# ============================================================================
SESSION_START = time.time()
S = {"entries": 0, "no_signal": 0, "ask_high": 0, "ask_low": 0, "too_late": 0,
     "no_fill": 0, "daily_pnl": 0.0, "day": None, "halted": False}
SPINNER = itertools.cycle(["◐", "◓", "◑", "◒"])
EVENT_LOG = deque(maxlen=9)


def log_event(msg, color="white"):
    EVENT_LOG.append((datetime.now().strftime("%H:%M:%S"), msg, color))


# ============================================================================
# 🌙 MOON DEV - V2 CLOB CLIENT (taker orders — V1 post_order throws PolyApiException!)
# ============================================================================
_CLIENT_CACHE = None


def _build_client():
    """🌙 Moon Dev - cached CLOB V2 client for AUG14."""
    global _CLIENT_CACHE
    if _CLIENT_CACHE is not None:
        return _CLIENT_CACHE
    from py_clob_client_v2.client import ClobClient
    from py_clob_client_v2.clob_types import ApiCreds
    from py_clob_client_v2.constants import POLYGON
    from web3 import Web3

    key = os.getenv(PRIVATE_KEY_ENV_NAME)
    pub = os.getenv(PUBLIC_KEY_ENV_NAME)
    if not key or not pub:
        print(colored(f"❌ Moon Dev - Missing {PRIVATE_KEY_ENV_NAME}/{PUBLIC_KEY_ENV_NAME} in .env!", "red"))
        sys.exit(1)
    try:
        funder = Web3.toChecksumAddress(pub)
    except AttributeError:
        funder = Web3.to_checksum_address(pub)

    client = ClobClient(host="https://clob.polymarket.com", chain_id=POLYGON,
                        key=key, signature_type=SIGNATURE_TYPE, funder=funder)
    api_key = os.getenv(f"API_KEY{ACCOUNT_SUFFIX}")
    # 🌙 Moon Dev - AUG14's secret lives in .env as SECRET_AUG14 (NOT API_SECRET_AUG14)
    api_secret = os.getenv(f"API_SECRET{ACCOUNT_SUFFIX}") or os.getenv(f"SECRET{ACCOUNT_SUFFIX}")
    passphrase = os.getenv(f"PASSPHRASE{ACCOUNT_SUFFIX}")
    if api_key and api_secret and passphrase:
        client.set_api_creds(ApiCreds(api_key=api_key, api_secret=api_secret, api_passphrase=passphrase))
    else:
        client.set_api_creds(client.create_or_derive_api_creds())
    _CLIENT_CACHE = client
    return client


def place_taker_buy(token_id, price, size):
    """🌙 Moon Dev - Marketable GTC BUY to TAKE the leading side (post_only=False, we cross).

    Why GTC and NOT FAK/FOK: Polymarket 400s marketable FAK/FOK orders when the
    crossable amount is < $1 — GTC has no such floor (verified live by the fleet)."""
    from py_clob_client_v2.clob_types import OrderArgs, OrderType
    client = _build_client()
    order_args = OrderArgs(token_id=str(token_id), price=float(price), size=float(size), side="BUY")
    log_event(f"🎯 TAKE leader @ ${price:.3f} x{size} (GTC taker)", "cyan")
    try:
        resp = client.create_and_post_order(order_args, order_type=OrderType.GTC, post_only=False)
        return resp or {}
    except Exception as e:
        err = str(e)
        if any(t in err.lower() for t in ['timeout', 'readtimeout', 'duplicated', 'request exception']):
            log_event("⚠️ order timed out — will verify via positions", "yellow")
            return {"status": "timeout"}
        log_event(f"❌ order failed: {type(e).__name__}: {e}", "red")
        return {}


def cancel_token_orders(token_id):
    """🌙 Moon Dev - Cancel resting GTC remainders on rollover."""
    from py_clob_client_v2.clob_types import OrderMarketCancelParams
    try:
        _build_client().cancel_market_orders(OrderMarketCancelParams(asset_id=str(token_id)))
    except Exception as e:
        log_event(f"⚠️ cancel failed: {type(e).__name__}", "yellow")


def position_for_token(token_id):
    """🌙 Moon Dev - (size, avg_price) we hold of token_id on AUG14, else (0, 0)."""
    pub = os.getenv(PUBLIC_KEY_ENV_NAME)
    r = safe_get("https://data-api.polymarket.com/positions",
                 {'user': pub, 'limit': 500, 'sortBy': 'CURRENT', 'sortDirection': 'DESC'})
    if r is None or r.status_code != 200:
        return (0.0, 0.0)
    for p in r.json():
        if str(p.get('asset')) == str(token_id):
            return (float(p.get('size', 0) or 0), float(p.get('avgPrice', 0) or 0))
    return (0.0, 0.0)


# ============================================================================
# 🌙 MOON DEV - MARKET DATA (read, blip-tolerant)
# ============================================================================
def safe_get(url, params, timeout=10):
    try:
        return requests.get(url, params=params, timeout=timeout,
                            headers={'User-Agent': 'Mozilla/5.0 (moondev)'})
    except requests.exceptions.RequestException as e:
        log_event(f"🌐 net blip: {type(e).__name__}", "yellow")
        return None


def get_current_window_ts():
    return (int(time.time()) // MARKET_DURATION) * MARKET_DURATION


def get_market_tokens(window_ts):
    """🌙 Moon Dev - btc-updown-5m-{T} -> {"Up": token, "Down": token} via Gamma."""
    slug = f"btc-updown-5m-{window_ts}"
    r = safe_get("https://gamma-api.polymarket.com/markets", {'slug': slug, 'closed': 'false', 'active': 'true'})
    if r is None or r.status_code != 200:
        return None
    mk = r.json()
    if not mk:
        return None
    m = mk[0]
    try:
        outcomes = json.loads(m.get('outcomes', '[]'))
        tokens = json.loads(m.get('clobTokenIds', '[]'))
    except (ValueError, TypeError):
        return None
    if len(outcomes) != len(tokens) or not tokens:
        return None
    return {"slug": slug, "by_outcome": {o: t for o, t in zip(outcomes, tokens)}}


def get_strike(window_ts):
    """🌙 Moon Dev - THE market's own price-to-beat (openPrice) — NOT first-tick spot!"""
    iso = datetime.fromtimestamp(window_ts, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.000Z')
    r = safe_get("https://polymarket.com/api/crypto/crypto-price",
                 {'symbol': 'btc', 'eventStartTime': iso})
    if r is None or r.status_code != 200:
        return None
    op = r.json().get('openPrice')
    return float(op) if op else None


def get_btc_mark():
    """🌙 Moon Dev - Hyperliquid BTC mark price."""
    try:
        r = requests.post("https://api.hyperliquid.xyz/info",
                          json={"type": "metaAndAssetCtxs"}, timeout=10)
    except requests.exceptions.RequestException as e:
        log_event(f"🌐 HL blip: {type(e).__name__}", "yellow")
        return None
    if r.status_code != 200:
        return None
    d = r.json()
    universe, ctxs = d[0]["universe"], d[1]
    for i, a in enumerate(universe):
        if a["name"] == "BTC":
            return float(ctxs[i]["markPx"])
    return None


def get_book(token_id):
    """🌙 Moon Dev - (best_bid, best_ask, ask_depth_shares) for a token."""
    r = safe_get("https://clob.polymarket.com/book", {'token_id': token_id}, timeout=5)
    if r is None or r.status_code != 200:
        return (None, None, 0.0)
    d = r.json()
    bids, asks = d.get('bids', []), d.get('asks', [])
    best_bid = float(bids[-1]['price']) if bids else None
    if not asks:
        return (best_bid, None, 0.0)
    best_ask = float(asks[-1]['price'])
    depth = sum(float(a.get('size', 0)) for a in asks if float(a['price']) == best_ask)
    return (best_bid, best_ask, depth)


def calc_shares(usd, price):
    """🌙 Moon Dev - shares for the stake, floored to 2dp, min 5 shares."""
    shares = math.floor((usd / price) * 100) / 100
    return max(float(MIN_SHARES), shares)


# ============================================================================
# 🌙 MOON DEV - CSV LOGGING (eval: every window. trades: fills, honest resolution)
# ============================================================================
def append_csv(path, fields, row):
    new = not os.path.exists(path)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        if new:
            w.writeheader()
        w.writerow(row)


def read_csv_rows(path):
    if not os.path.exists(path):
        return []
    with open(path, newline="") as f:
        return list(csv.DictReader(f))


def write_csv_rows(path, fields, rows):
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)


def record_trade(window_ts, slug, side, signal_ask, entry_ask, shares):
    """🌙 Moon Dev - a fresh fill enters the trades file as PENDING."""
    append_csv(TRADES_FILE, TRADE_FIELDS, {
        "timestamp": datetime.now(ET).strftime("%Y-%m-%d %H:%M:%S"),
        "market_ts": window_ts, "slug": slug, "side": side,
        "signal_ask": f"{signal_ask:.4f}", "entry_ask": f"{entry_ask:.4f}",
        "fill_slippage": f"{entry_ask - signal_ask:+.4f}",
        "shares": f"{shares:.2f}", "usd": f"{entry_ask * shares:.2f}",
        "result": "PENDING", "resolved_winner": "", "pnl_usd": "",
        "paper": int(PAPER_MODE)})


def mark_no_fill_if_flat(window_ts, token_id):
    """🌙 Moon Dev - at rollover: GTC never filled -> NO_FILL, P&L stays clean."""
    if PAPER_MODE or not os.path.exists(TRADES_FILE):
        return
    held, _ = position_for_token(token_id)
    if held > 0:
        return
    rows = read_csv_rows(TRADES_FILE)
    changed = False
    for r in rows:
        if r["market_ts"] == str(window_ts) and r["result"] == "PENDING":
            r["result"] = "NO_FILL"
            changed = True
    if changed:
        write_csv_rows(TRADES_FILE, TRADE_FIELDS, rows)
        log_event("🚫 GTC never filled → NO_FILL (P&L stays clean)", "yellow")


# ============================================================================
# 🌙 MOON DEV - HONEST RESOLUTION (definitive only — no phantom PENDING wins)
# ============================================================================
def get_definitive_winner(market_ts, slug):
    """🌙 Moon Dev - 'Up'/'Down' only when DEFINITIVELY resolved:
    1) crypto-price completed==true → close vs open (Chainlink rule).
    2) Gamma outcomePrices exactly 1/0 as fallback. None while unresolved."""
    iso = datetime.fromtimestamp(market_ts, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.000Z')
    r = safe_get("https://polymarket.com/api/crypto/crypto-price",
                 {'symbol': 'btc', 'eventStartTime': iso})
    if r is not None and r.status_code == 200:
        d = r.json()
        op, cp = d.get('openPrice'), d.get('closePrice')
        if d.get('completed') and op and cp:
            return "Up" if float(cp) >= float(op) else "Down"
    r = safe_get("https://gamma-api.polymarket.com/markets", {'slug': slug})
    if r is not None and r.status_code == 200 and r.json():
        try:
            prices = json.loads(r.json()[0].get('outcomePrices', '[]') or '[]')
        except (ValueError, TypeError):
            prices = []
        if len(prices) == 2:
            up = float(prices[0])
            if up == 1.0:
                return "Up"
            if up == 0.0:
                return "Down"
    return None


def resolve_pending_trades():
    """🌙 Moon Dev - poll PENDING trades every cycle; stamp WIN/LOSS + pnl when definitive."""
    if not os.path.exists(TRADES_FILE):
        return
    rows = read_csv_rows(TRADES_FILE)
    changed = False
    for r in rows:
        if r["result"] != "PENDING":
            continue
        close_ts = int(r["market_ts"]) + MARKET_DURATION
        if time.time() < close_ts + RESOLVE_GRACE_SEC:
            continue
        winner = get_definitive_winner(int(r["market_ts"]), r["slug"])
        if winner is None:
            continue  # stays PENDING, never faked — retried next cycle
        won = r["side"] == winner
        shares = float(r["shares"])
        cost = float(r["entry_ask"]) * shares
        pnl = (shares - cost) if won else -cost
        r["result"] = "WIN" if won else "LOSS"
        r["resolved_winner"] = winner
        r["pnl_usd"] = f"{pnl:+.2f}"
        changed = True
        S["daily_pnl"] += pnl
        log_event(f"{'🏆 WON' if won else '💀 LOST'} {r['side']} vs {winner} ${pnl:+.2f} {r['slug']}",
                  "green" if won else "red")
    if changed:
        write_csv_rows(TRADES_FILE, TRADE_FIELDS, rows)


# ============================================================================
# 🌙 MOON DEV - WINDOW STATE
# ============================================================================
def fresh_state():
    return {"slug": None, "tokens": None, "strike": None, "spot": None, "itm_pct": 0.0,
            "side": None, "bid": None, "ask": None, "entered": False, "placed": False,
            "shares": None, "token_id": None, "action": None, "signal_late": False,
            "why": "", "last_logged_action": None, "last_time_left": None,
            "last_check": 0.0}


def finalize_window(st, window_ts):
    """🌙 Moon Dev - one eval row per window (entries AND skips) — the re-mining dataset."""
    if st["slug"] is None:
        return  # market never discovered — nothing real to log
    action = st["action"]
    if action is None:
        action = "TOO_LATE" if st["signal_late"] else "NO_SIGNAL"
    if action == "TOO_LATE":
        S["too_late"] += 1
    elif action == "NO_SIGNAL":
        S["no_signal"] += 1
    elif action == "ASK_TOO_HIGH":
        S["ask_high"] += 1
    elif action == "ASK_TOO_LOW":
        S["ask_low"] += 1
    elif action == "NO_FILL" and not st["entered"]:
        S["no_fill"] += 1
    append_csv(EVAL_LOG, EVAL_FIELDS, {
        "timestamp": datetime.now(ET).strftime("%Y-%m-%d %H:%M:%S"),
        "window_ts": window_ts, "slug": st["slug"],
        "strike": "" if st["strike"] is None else f"{st['strike']:.2f}",
        "spot": "" if st["spot"] is None else f"{st['spot']:.2f}",
        "itm_pct": f"{st['itm_pct']:.4f}",
        "side": st["side"] or "",
        "best_bid": "" if st["bid"] is None else f"{st['bid']:.4f}",
        "best_ask": "" if st["ask"] is None else f"{st['ask']:.4f}",
        "minutes_left": "" if st.get("last_time_left") is None else f"{st['last_time_left'] / 60:.2f}",
        "action": action, "paper": int(PAPER_MODE)})


# ============================================================================
# 🌙 MOON DEV - THE HUNT (one BTC tick)
# ============================================================================
def note_skip(st, action, detail):
    """🌙 Moon Dev - set the skip action + the live WHY line; event-log each NEW
    reason once per window so the dashboard always says why we're not in."""
    st["action"] = action
    st["why"] = detail
    if st["last_logged_action"] != action:
        st["last_logged_action"] = action
        log_event(detail, "yellow")


def hunt(st, time_left):
    """🌙 Moon Dev - the mined cell: leading side, 0.40-0.55, 120-300s left. Nothing else."""
    if st["entered"] or st["placed"]:
        return
    if st["tokens"] is None or st["strike"] is None or st["spot"] is None:
        st["why"] = "waiting for market/strike/spot data..."
        return

    st["last_time_left"] = time_left
    st["itm_pct"] = (st["spot"] - st["strike"]) / st["strike"] * 100.0
    leading = "Up" if st["itm_pct"] >= 0 else "Down"
    mag = abs(st["itm_pct"])

    if mag < MIN_ITM_PCT:
        st["why"] = f"💤 no signal: |itm| {mag:.3f}% < {MIN_ITM_PCT}% gate — {leading} barely ahead"
        return

    if time_left < TIME_BAND[0]:
        st["signal_late"] = True
        if st["action"] is None:
            note_skip(st, "TOO_LATE",
                      f"⏰ {leading} leading {mag:.3f}% but only {time_left}s left (< {TIME_BAND[0]}s) — too late")
        return
    if time_left > TIME_BAND[1]:
        st["why"] = f"⏰ {leading} leading {mag:.3f}% but {time_left}s left — above the {TIME_BAND[1]}s band"
        return

    now = time.time()
    if now - st["last_check"] < CHECK_INTERVAL:
        return
    st["last_check"] = now

    if S["halted"]:
        st["why"] = "🛑 daily stop hit — watching only"
        return

    st["side"] = leading
    st["token_id"] = st["tokens"].get(leading)
    bid, ask, depth = get_book(st["token_id"])
    st["bid"], st["ask"] = bid, ask
    if ask is None:
        st["why"] = f"📖 {leading} leading {mag:.3f}% — empty book, re-checking"
        return

    if ask > PRICE_BAND[1]:
        note_skip(st, "ASK_TOO_HIGH",
                  f"⏭️ {leading} ask ${ask:.3f} > ${PRICE_BAND[1]:.2f} cap — dead zone, no chase (itm {mag:.3f}%)")
        return
    if ask < PRICE_BAND[0]:
        note_skip(st, "ASK_TOO_LOW",
                  f"⏭️ {leading} ask ${ask:.3f} < ${PRICE_BAND[0]:.2f} floor (itm {mag:.3f}%)")
        return

    shares = calc_shares(USD_SIZE, ask)
    if depth < shares:
        note_skip(st, "NO_FILL",
                  f"⏭️ {leading} ask ${ask:.3f} in band but depth {depth:.0f} < {shares:.0f} shares — thin book")
        return

    st["why"] = f"🚀 {leading} ask ${ask:.3f} in the {PRICE_BAND[0]:.2f}-{PRICE_BAND[1]:.2f} band — FIRING"

    signal_ask = ask  # 🌙 the price we saw — slippage is measured against THIS
    if PAPER_MODE:
        st["placed"] = True
        st["entered"] = True
        st["shares"] = shares
        st["action"] = "FILLED"
        S["entries"] += 1
        record_trade(get_current_window_ts(), st["slug"], leading, signal_ask, signal_ask, shares)
        log_event(f"📝 PAPER FILL {leading} @ ${signal_ask:.3f} x{shares:.0f} (itm {abs(st['itm_pct']):.2f}%)", "green")
        return

    st["placed"] = True
    resp = place_taker_buy(st["token_id"], ask, shares)
    time.sleep(1.5)
    held, avg = position_for_token(st["token_id"])
    status = str(resp.get("status", "")).lower()
    filled = held > 0 or "matched" in status or float(resp.get("takingAmount", 0) or 0) > 0
    if filled:
        entry = avg if held > 0 and avg > 0 else ask
        got = held if held > 0 else shares
        st["entered"] = True
        st["shares"] = got
        st["action"] = "FILLED"
        S["entries"] += 1
        record_trade(get_current_window_ts(), st["slug"], leading, signal_ask, entry, got)
        slip = entry - signal_ask
        log_event(f"✅ FILLED {leading} @ ${entry:.3f} x{got:.0f} (slip {slip:+.3f})", "green")
    else:
        st["action"] = "NO_FILL"
        log_event(f"😕 no fill @ ${ask:.3f} — missed the cross", "yellow")


# ============================================================================
# 🌙 MOON DEV - DASHBOARD
# ============================================================================
def clear_screen():
    os.system('cls' if os.name == 'nt' else 'clear')


def draw_tracker():
    """🌙 Moon Dev - clickable history, W/L/PENDING separate, P&L resolved-only."""
    rows = [r for r in read_csv_rows(TRADES_FILE) if r["result"] != "NO_FILL"]
    print(colored(f"  ========== 🌙 MOON DEV'S TRADE TRACKER ({len(rows)} trades) 🌙 ==========", "yellow", attrs=['bold']))
    for r in rows[-20:]:
        if r["result"] == "WIN":
            res, rcol = f"WIN  +${float(r['pnl_usd']):.2f}", "green"
        elif r["result"] == "LOSS":
            res, rcol = f"LOSS -${abs(float(r['pnl_usd'])):.2f}", "red"
        else:
            res, rcol = "PENDING", "yellow"
        print(colored(f"  {r['timestamp'][5:16]} | {r['side']:<4} @ {float(r['entry_ask']):.2f} | ", "white")
              + colored(f"{res:<12}", rcol, attrs=['bold'])
              + colored(f"| https://polymarket.com/event/{r['slug']}", "cyan"))
    wins = sum(1 for r in rows if r["result"] == "WIN")
    losses = sum(1 for r in rows if r["result"] == "LOSS")
    pend = sum(1 for r in rows if r["result"] == "PENDING")
    pnl = sum(float(r["pnl_usd"]) for r in rows if r["result"] in ("WIN", "LOSS"))
    wr = wins / (wins + losses) * 100 if (wins + losses) else 0
    pcol = "green" if pnl >= 0 else "red"
    print(colored(f"  📊 W {wins} | L {losses} | PENDING {pend} | WR {wr:.1f}% | ", "white", attrs=['bold'])
          + colored(f"P&L (resolved only) ${pnl:+.2f}", pcol, attrs=['bold']))
    print(colored("  ========================================================================", "yellow", attrs=['bold']))


def draw(st, time_left):
    clear_screen()
    draw_tracker()
    mode = colored("📝 PAPER", "yellow", attrs=['bold']) if PAPER_MODE else colored("🔴 LIVE · $5 real", "red", attrs=['bold'])
    print(colored("\n  ⚡ MOON DEV's MID-PRICE CONTINUATION 40-55 — the cells that paid ⚡  ", "cyan", attrs=['bold']) + mode + "\n")
    print(colored(f"  entries {S['entries']:<3} skips: no-sig {S['no_signal']:<4} hi {S['ask_high']:<3} lo {S['ask_low']:<3} "
                  f"late {S['too_late']:<3} no-fill {S['no_fill']:<3}"
                  f"{' 🛑 DAILY STOP' if S['halted'] else ''}", "white"))
    mins, secs = max(0, time_left) // 60, max(0, time_left) % 60
    in_band = TIME_BAND[0] <= time_left <= TIME_BAND[1]
    print(colored(f"\n  ⏱  time left {mins}:{secs:02d}  ({'IN BAND 🎯' if in_band else ('too late' if time_left < TIME_BAND[0] else 'too early')})",
                  "green" if in_band else "yellow", attrs=['bold']))
    if st["strike"] is None or st["spot"] is None:
        print(colored(f"\n  BTC {next(SPINNER)} waiting for strike/spot...", "white"))
    else:
        itm = st["itm_pct"]
        lead = "Up" if itm >= 0 else "Down"
        icol = "green" if abs(itm) >= MIN_ITM_PCT else "white"
        status = ""
        if st["entered"]:
            status = colored(f"  ✅ IN {st['side']} x{st['shares']:.0f} — riding to resolution", "green", attrs=['bold'])
        elif st["action"]:
            status = colored(f"  [{st['action']}]", "yellow")
        elif abs(itm) >= MIN_ITM_PCT:
            status = colored("  🔥 LEADING — hunting the 40-55c ask", "magenta", attrs=['bold'])
        ask_s = f"${st['ask']:.3f}" if st["ask"] else "—"
        print(colored(f"\n  BTC strike {st['strike']:>12,.2f}  spot {st['spot']:>12,.2f}  ", "cyan")
              + colored(f"itm {itm:+.3f}% ({lead})", icol, attrs=['bold'])
              + colored(f"  ask {ask_s}", "white") + status)
        # 🌙 Moon Dev - the WHY line: always says exactly why we're not in a trade (with numbers)
        if not st["entered"] and st["why"]:
            wcol = "green" if st["why"].startswith("🚀") else ("yellow" if st["action"] else "white")
            print(colored(f"    ↳ {st['why']}", wcol, attrs=['bold'] if st["action"] else None))
    print()
    print(colored("  ┌───────────────────────── 📜 RECENT ─────────────────────────┐", "white"))
    for ts, msg, color in EVENT_LOG:
        print(colored((f"  │ {ts} {msg}")[:64].ljust(64) + "│", color))
    print(colored("  └──────────────────────────────────────────────────────────────┘", "white"))
    up = (time.time() - SESSION_START) / 60
    print(colored(f"\n  🌙 itm>={MIN_ITM_PCT}% | ask {PRICE_BAND[0]:.2f}-{PRICE_BAND[1]:.2f} HARD CAP | "
                  f"{TIME_BAND[0]}-{TIME_BAND[1]}s | ${USD_SIZE} | up {up:.1f}m — Ctrl+C", "magenta"))


# ============================================================================
# 🌙 MOON DEV - MAIN
# ============================================================================
def main():
    if not PAPER_MODE and (not os.getenv(PRIVATE_KEY_ENV_NAME) or not os.getenv(PUBLIC_KEY_ENV_NAME)):
        print(colored(f"❌ Missing {PRIVATE_KEY_ENV_NAME}/{PUBLIC_KEY_ENV_NAME} in .env", "red"))
        sys.exit(1)

    st = fresh_state()
    current_window = None
    last_draw = 0.0
    last_mark = 0.0
    log_event("⚡ Mid-price continuation spinning up" + (" (PAPER)" if PAPER_MODE else " — LIVE on AUG14"), "cyan")
    log_event(f"🎯 itm>={MIN_ITM_PCT}% | ask {PRICE_BAND[0]}-{PRICE_BAND[1]} | {TIME_BAND[0]}-{TIME_BAND[1]}s | ${USD_SIZE}", "cyan")

    while True:
        now = time.time()
        window_ts = get_current_window_ts()
        time_left = MARKET_DURATION - (int(now) - window_ts)

        # 🌙 daily stop-loss bookkeeping (ET day)
        today = datetime.now(ET).strftime("%Y-%m-%d")
        if S["day"] != today:
            S["day"] = today
            S["daily_pnl"] = 0.0
            S["halted"] = False
        if not S["halted"] and S["daily_pnl"] <= DAILY_STOP_LOSS:
            S["halted"] = True
            log_event(f"🛑 DAILY STOP {S['daily_pnl']:+.2f} <= {DAILY_STOP_LOSS} — no new entries today", "red")

        # 🌙 honest resolution pass (definitive only, PENDING never faked)
        resolve_pending_trades()

        # 🌙 window rollover: cancel remainders, mark NO_FILL if flat, log the window, reset
        if window_ts != current_window:
            if current_window is not None:
                if not PAPER_MODE and st["placed"] and st["token_id"]:
                    cancel_token_orders(st["token_id"])
                    mark_no_fill_if_flat(current_window, st["token_id"])
                finalize_window(st, current_window)
                st = fresh_state()
            current_window = window_ts
            log_event(f"🔄 new window {datetime.fromtimestamp(window_ts, ET).strftime('%H:%M')} ET", "magenta")

        # 🌙 discover market + strike (the market's OWN price-to-beat)
        if st["tokens"] is None:
            info = get_market_tokens(window_ts)
            if info:
                st["tokens"] = info["by_outcome"]
                st["slug"] = info["slug"]
        if st["strike"] is None and st["tokens"] is not None:
            op = get_strike(window_ts)
            if op:
                st["strike"] = op
                log_event(f"🎯 strike {op:,.2f}", "cyan")

        # 🌙 spot
        if now - last_mark >= 1.0:
            last_mark = now
            px = get_btc_mark()
            if px:
                st["spot"] = px
                if st["strike"]:
                    st["itm_pct"] = (px - st["strike"]) / st["strike"] * 100.0

        hunt(st, time_left)

        if now - last_draw >= 0.5:
            last_draw = now
            draw(st, time_left)
        time.sleep(0.3)


if __name__ == "__main__":
    tag = "PAPER MODE — logging would-be fills" if PAPER_MODE else "LIVE on AUG14 (real money, $5)"
    print(colored(f"🌙 Moon Dev's Mid-Price Continuation 40-55 — {tag}...", "cyan", attrs=['bold']))
    print(colored(f"📒 trades: {TRADES_FILE}\n📒 eval:   {EVAL_LOG}", "cyan"))
    time.sleep(0.4)
    while True:
        try:
            main()
        except KeyboardInterrupt:
            print()
            print(colored("  ╔════════════════════════════════════════════════════╗", "yellow", attrs=['bold']))
            print(colored("  ║  ⚡ MOON DEV — MID-PRICE CONTINUATION STOPPED ⚡   ║", "yellow", attrs=['bold']))
            print(colored("  ╚════════════════════════════════════════════════════╝", "yellow", attrs=['bold']))
            print(colored(f"  🎯 entries {S['entries']}  day P&L ${S['daily_pnl']:+.2f}", "green", attrs=['bold']))
            print(colored(f"  💾 {TRADES_FILE}", "cyan"))
            print(colored("  🌙 Moon Dev out — never chase above 0.55.", "magenta"))
            print()
            break
        except Exception as e:
            print(colored(f"  💥 crashed: {type(e).__name__}: {e} — restarting in 3s", "red", attrs=['bold']))
            _CLIENT_CACHE = None
            time.sleep(3)
            continue
