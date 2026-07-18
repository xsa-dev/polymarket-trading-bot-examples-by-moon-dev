"""
================================================================================
🌙 MOON DEV's SMALL-LIQ CONTINUATION 30-45 v1.0  (kimik3-july18 fleet #3)
================================================================================
Born from honest re-resolution of the stink-bid logs against Coinbase candles
(they only ever said FILLED/CANCELLED, never win/loss). The finding: liquidation
continuation pays in the 0.30-0.45 band — liq fills 0.30-0.40 went 48.8% win @
34.1c → +43% EV (n=41) — while MACD/CVD-signal fills in the same band LOST
(controls prove it's the LIQ signal, not the fill mechanic). Sub-0.30 liq fills
were toxic (16.7% win). Above 0.45 is mega_liq_continuation's thin +11% zone.

THE HOLE THIS BOT FILLS: mega_liq_continuation requires $500K+ liqs at 0.45-0.60;
momentum_ignition_dog is CVD-primary. Nobody trades the $25K-$500K tier
standalone, taker, in the 0.30-0.45 band. This bot. BTC only.

THE TRADE:
  - MoonDev API liquidation feed, trailing 2 minutes, BTC only.
  - longs rekt >= $25K & dominant  → price hammered → BUY DOWN (continuation)
    shorts rekt >= $25K & dominant → price ripping  → BUY UP
  - >= $500K dominant → SKIP_MEGA (that's mega_liq_continuation's trade — don't
    double the family's exposure on the same event).
  - >= $100K dominant → 1.5x size kicker.
  - Price gate: continuation-side ask 0.30-0.45 ONLY, 60-240s left. Taker GTC.
  - One entry per window. Hold to resolution.

HONEST P&L (round-2 fix): resolve ONLY on crypto-price completed==true or Gamma
outcomePrices 1/0; PENDING never faked; unfilled GTCs marked NO_FILL at rollover;
W / L / PENDING shown separately; P&L counts resolved only.

LOGGING:
  - data/small_liq_continuation_eval.csv   — EVERY poll: the liq tape + gates
    (this is the dataset that validates small-liq DIRECTION standalone)
  - data/small_liq_continuation_trades.csv — fills: signal_ask vs fill_price,
    fill_slippage, resolution persisted

🔴 PAPER_MODE = False — LIVE FIRE on the AUG14 account, $5 base. Flip to True to paper.

📦 DEPS: py_clob_client_v2 + MoonDevAPI (sys.path to moon-dev-trading-bots, same
as mega_liq_continuation.py) + MOONDEV_API_KEY in the repo-root .env.
Account: AUG14 (signature_type 2, Gnosis Safe).
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
# 🌙 MOON DEV - ZERO-DEP STAND-INS
# ============================================================================
ANSI = {"red": "31", "green": "32", "yellow": "33", "magenta": "35", "cyan": "36", "white": "37"}


def colored(text, color=None, attrs=None):
    codes = (["1"] if attrs and "bold" in attrs else []) + ([ANSI[color]] if color in ANSI else [])
    return f"\033[{';'.join(codes)}m{text}\033[0m" if codes else str(text)


def load_env(path):
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
# 🌙 MOON DEV - PATH / ENV / ACCOUNT (AUG14) / MOON DEV API
# ============================================================================
BOT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(os.path.dirname(BOT_DIR))
MOON_DEV_API_PATH = "/Users/md/Dropbox/dev/github/moon-dev-trading-bots"
load_env(os.path.join(PROJECT_ROOT, '.env'))
sys.path.insert(0, MOON_DEV_API_PATH)

ACCOUNT_SUFFIX = "_AUG14"
PRIVATE_KEY_ENV_NAME = f"PRIVATE_KEY{ACCOUNT_SUFFIX}"
PUBLIC_KEY_ENV_NAME = f"PUBLIC_KEY{ACCOUNT_SUFFIX}"
SIGNATURE_TYPE = 2  # AUG14 = Gnosis Safe

try:
    from api import MoonDevAPI
    api = MoonDevAPI()
    if not api.api_key:
        api = None
except Exception as e:
    print(colored(f"⚠️ Moon Dev - MoonDevAPI import failed: {e}", "yellow"))
    api = None

# ============================================================================
# 🌙 MOON DEV - KEY STRATEGY PARAMS (the mined cell)
# ============================================================================
LIQ_MIN_USD = 25_000         # 🎯 trailing 2-min dominant-side liqs to trigger (the unbuilt tier)
LIQ_MEGA_USD = 500_000       # 🚫 at/above this = mega_liq_continuation's trade → SKIP_MEGA
KICKER_USD = 100_000         # 💪 >= this → 1.5x size (mined: $25K-$100K cell was +29% EV)
LIQ_LOOKBACK_SEC = 120       # trailing 2 minutes of liquidations
LIQ_TIMEFRAME = "10m"        # API's smallest liq window, we filter to 2 min ourselves

PRICE_ZONE = (0.30, 0.45)    # 💵 THE mined cell. Sub-0.30 toxic, >0.45 belongs to mega_liq
TIME_BAND = (60, 240)        # ⏱ seconds remaining
USD_SIZE = 5                 # 💰 flat base stake
KICKER_MULT = 1.5            # size multiplier at >= KICKER_USD
DAILY_STOP_LOSS = -30        # 🛑 halt new entries at this realized day P&L
PAPER_MODE = False           # 🔴 LIVE on AUG14. True = log would-be fills only

MIN_SHARES = 5
MARKET_DURATION = 300
BOT_POLL_INTERVAL = 6        # seconds between liq tape scans
RESOLVE_GRACE_SEC = 15
ET = timezone(timedelta(hours=-4))

DATA_DIR = os.path.join(BOT_DIR, "data")
EVAL_LOG = os.path.join(DATA_DIR, "small_liq_continuation_eval.csv")
TRADES_FILE = os.path.join(DATA_DIR, "small_liq_continuation_trades.csv")
EVAL_FIELDS = ["timestamp", "market_ts", "slug", "long_liq_usd", "short_liq_usd",
               "dominant_side", "cont_side", "minutes_left", "best_bid", "best_ask",
               "action", "paper"]
TRADE_FIELDS = ["timestamp", "market_ts", "slug", "side", "liq_usd_dominant", "liq_usd_other",
                "kicker", "signal_ask", "entry_ask", "fill_slippage", "shares", "usd",
                "result", "resolved_winner", "pnl_usd", "paper"]

# ============================================================================
# 🌙 MOON DEV - SESSION STATS
# ============================================================================
SESSION_START = time.time()
S = {"entries": 0, "no_liq": 0, "mega_skip": 0, "ask_high": 0, "ask_low": 0, "too_late": 0,
     "no_fill": 0, "daily_pnl": 0.0, "day": None, "halted": False}
SPINNER = itertools.cycle(["◐", "◓", "◑", "◒"])
EVENT_LOG = deque(maxlen=9)


def log_event(msg, color="white"):
    EVENT_LOG.append((datetime.now().strftime("%H:%M:%S"), msg, color))


# ============================================================================
# 🌙 MOON DEV - V2 CLOB CLIENT (AUG14)
# ============================================================================
_CLIENT_CACHE = None


def _build_client():
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
    api_secret = os.getenv(f"API_SECRET{ACCOUNT_SUFFIX}") or os.getenv(f"SECRET{ACCOUNT_SUFFIX}")
    passphrase = os.getenv(f"PASSPHRASE{ACCOUNT_SUFFIX}")
    if api_key and api_secret and passphrase:
        client.set_api_creds(ApiCreds(api_key=api_key, api_secret=api_secret, api_passphrase=passphrase))
    else:
        client.set_api_creds(client.create_or_derive_api_creds())
    _CLIENT_CACHE = client
    return client


def place_taker_buy(token_id, price, size):
    """🌙 Moon Dev - Marketable GTC BUY (post_only=False). GTC not FAK/FOK:
    Polymarket 400s marketable FAK/FOK when crossable < $1 — verified live."""
    from py_clob_client_v2.clob_types import OrderArgs, OrderType
    client = _build_client()
    order_args = OrderArgs(token_id=str(token_id), price=float(price), size=float(size), side="BUY")
    log_event(f"🎯 TAKE continuation @ ${price:.3f} x{size} (GTC taker)", "cyan")
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
    from py_clob_client_v2.clob_types import OrderMarketCancelParams
    try:
        _build_client().cancel_market_orders(OrderMarketCancelParams(asset_id=str(token_id)))
    except Exception as e:
        log_event(f"⚠️ cancel failed: {type(e).__name__}", "yellow")


def position_for_token(token_id):
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
# 🌙 MOON DEV - MARKET DATA
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


def get_book(token_id):
    """🌙 Moon Dev - (best_bid, best_ask, ask_depth_shares)."""
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
    shares = math.floor((usd / price) * 100) / 100
    return max(float(MIN_SHARES), shares)


# ============================================================================
# 🌙 MOON DEV - LIQUIDATION FEED (trailing 2-min, same feed as the liq_stink line)
# ============================================================================
def get_btc_liquidations_2min():
    """🌙 Moon Dev - BTC liqs from last 2 minutes → (long_liqd_usd, short_liqd_usd).
    longs rekt = price pushed DOWN. shorts rekt = price pushed UP."""
    if api is None:
        return 0.0, 0.0
    try:
        data = api.get_all_liquidations(LIQ_TIMEFRAME)
    except Exception as e:
        log_event(f"🌐 liq API blip: {type(e).__name__}", "yellow")
        return 0.0, 0.0
    if not data:
        return 0.0, 0.0
    cutoff_ms = int(time.time() * 1000) - LIQ_LOOKBACK_SEC * 1000
    liqs = data.get('liquidations', data.get('data', []))
    long_total = 0.0
    short_total = 0.0
    if isinstance(liqs, list):
        for liq in liqs:
            if liq.get('timestamp', 0) < cutoff_ms:
                continue
            coin = str(liq.get('coin', liq.get('symbol', ''))).upper()
            if coin != 'BTC':
                continue
            value = float(liq.get('value_usd', liq.get('value', liq.get('usd_value', 0))))
            side = str(liq.get('side', liq.get('direction', ''))).lower()
            if side in ('long', 'sell', 'b'):
                long_total += value
            elif side in ('short', 'buy', 's'):
                short_total += value
    return long_total, short_total


def check_signal():
    """🌙 Moon Dev - the mined cell:
    longs rekt >= $25K & dominant → BUY DOWN continuation
    shorts rekt >= $25K & dominant → BUY UP continuation
    >= $500K dominant → SKIP_MEGA (mega_liq_continuation owns that trade).
    Returns (cont_side or None, skip_tag, long_liq, short_liq, dominant_usd)."""
    long_liq, short_liq = get_btc_liquidations_2min()
    dom_long = long_liq > short_liq
    dominant = max(long_liq, short_liq)
    if dominant < LIQ_MIN_USD:
        return None, "NO_LIQ", long_liq, short_liq, dominant
    if dominant >= LIQ_MEGA_USD:
        return None, "SKIP_MEGA", long_liq, short_liq, dominant
    if dom_long:
        return "Down", "FIRE", long_liq, short_liq, dominant
    return "Up", "FIRE", long_liq, short_liq, dominant


# ============================================================================
# 🌙 MOON DEV - CSV LOGGING
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


def record_trade(window_ts, slug, side, dom_usd, other_usd, kicker, signal_ask, entry_ask, shares):
    append_csv(TRADES_FILE, TRADE_FIELDS, {
        "timestamp": datetime.now(ET).strftime("%Y-%m-%d %H:%M:%S"),
        "market_ts": window_ts, "slug": slug, "side": side,
        "liq_usd_dominant": f"{dom_usd:.0f}", "liq_usd_other": f"{other_usd:.0f}",
        "kicker": int(kicker),
        "signal_ask": f"{signal_ask:.4f}", "entry_ask": f"{entry_ask:.4f}",
        "fill_slippage": f"{entry_ask - signal_ask:+.4f}",
        "shares": f"{shares:.2f}", "usd": f"{entry_ask * shares:.2f}",
        "result": "PENDING", "resolved_winner": "", "pnl_usd": "",
        "paper": int(PAPER_MODE)})


def mark_no_fill_if_flat(window_ts, token_id):
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
# 🌙 MOON DEV - HONEST RESOLUTION (definitive only)
# ============================================================================
def get_definitive_winner(market_ts, slug):
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
            continue
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
# 🌙 MOON DEV - WINDOW STATE + THE HUNT
# ============================================================================
def fresh_state():
    return {"slug": None, "tokens": None, "entered": False, "placed": False,
            "shares": None, "token_id": None, "last_eval_log": 0.0}


def main():
    if api is None:
        print(colored("❌ Moon Dev - MOONDEV_API_KEY / MoonDevAPI unavailable — liq feed dead. Fix .env.", "red"))
        sys.exit(1)
    if not PAPER_MODE and (not os.getenv(PRIVATE_KEY_ENV_NAME) or not os.getenv(PUBLIC_KEY_ENV_NAME)):
        print(colored(f"❌ Missing {PRIVATE_KEY_ENV_NAME}/{PUBLIC_KEY_ENV_NAME} in .env", "red"))
        sys.exit(1)

    st = fresh_state()
    current_window = None
    last_draw = 0.0
    last_poll = 0.0
    tape = {"long": 0.0, "short": 0.0, "cont": None, "tag": "NO_LIQ", "dom": 0.0,
            "ask": None, "bid": None, "time_left": 0}
    log_event("🌊 Small-liq continuation spinning up" + (" (PAPER)" if PAPER_MODE else " — LIVE on AUG14"), "cyan")
    log_event(f"🎯 liq ${LIQ_MIN_USD/1000:.0f}K-${LIQ_MEGA_USD/1000:.0f}K | ask {PRICE_ZONE[0]}-{PRICE_ZONE[1]} | {TIME_BAND[0]}-{TIME_BAND[1]}s | ${USD_SIZE}", "cyan")

    while True:
        now = time.time()
        window_ts = get_current_window_ts()
        time_left = MARKET_DURATION - (int(now) - window_ts)
        tape["time_left"] = time_left

        today = datetime.now(ET).strftime("%Y-%m-%d")
        if S["day"] != today:
            S["day"] = today
            S["daily_pnl"] = 0.0
            S["halted"] = False
        if not S["halted"] and S["daily_pnl"] <= DAILY_STOP_LOSS:
            S["halted"] = True
            log_event(f"🛑 DAILY STOP {S['daily_pnl']:+.2f} <= {DAILY_STOP_LOSS} — no new entries today", "red")

        resolve_pending_trades()

        if window_ts != current_window:
            if current_window is not None:
                if not PAPER_MODE and st["placed"] and st["token_id"]:
                    cancel_token_orders(st["token_id"])
                    mark_no_fill_if_flat(current_window, st["token_id"])
                st = fresh_state()
            current_window = window_ts
            log_event(f"🔄 new window {datetime.fromtimestamp(window_ts, ET).strftime('%H:%M')} ET", "magenta")

        if st["tokens"] is None:
            info = get_market_tokens(window_ts)
            if info:
                st["tokens"] = info["by_outcome"]
                st["slug"] = info["slug"]

        # 🌙 scan the liq tape + evaluate gates every BOT_POLL_INTERVAL
        if now - last_poll >= BOT_POLL_INTERVAL and st["tokens"] is not None:
            last_poll = now
            cont, tag, long_liq, short_liq, dom = check_signal()
            tape.update({"long": long_liq, "short": short_liq, "cont": cont, "tag": tag, "dom": dom})

            bid = ask = None
            if cont is not None:
                bid, ask, _depth = get_book(st["tokens"].get(cont))
                tape["bid"], tape["ask"] = bid, ask

            action = tag
            if tag == "SKIP_MEGA":
                S["mega_skip"] += 1
            elif cont is None:
                S["no_liq"] += 1
            elif st["entered"] or st["placed"]:
                action = "ALREADY_IN"
            elif not (TIME_BAND[0] <= time_left <= TIME_BAND[1]):
                action = "TOO_LATE" if time_left < TIME_BAND[0] else "TOO_EARLY"
                if action == "TOO_LATE":
                    S["too_late"] += 1
            elif S["halted"]:
                action = "HALTED"
            elif ask is None:
                action = "NO_BOOK"
            elif ask > PRICE_ZONE[1]:
                action = "ASK_TOO_HIGH"
                S["ask_high"] += 1
            elif ask < PRICE_ZONE[0]:
                action = "ASK_TOO_LOW"
                S["ask_low"] += 1
            else:
                # 🚀 ALL GATES PASSED — liq continuation, 0.30-0.45, in time band
                kicker = dom >= KICKER_USD
                usd = USD_SIZE * (KICKER_MULT if kicker else 1.0)
                shares = calc_shares(usd, ask)
                signal_ask = ask
                other = short_liq if cont == "Down" else long_liq
                if PAPER_MODE:
                    st["placed"] = True
                    st["entered"] = True
                    st["shares"] = shares
                    st["token_id"] = st["tokens"].get(cont)
                    S["entries"] += 1
                    record_trade(window_ts, st["slug"], cont, dom, other, kicker, signal_ask, signal_ask, shares)
                    log_event(f"📝 PAPER FILL {cont} @ ${signal_ask:.3f} x{shares:.0f} (liq ${dom/1000:.0f}K{' KICKER' if kicker else ''})", "green")
                    action = "FILLED"
                else:
                    st["placed"] = True
                    st["token_id"] = st["tokens"].get(cont)
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
                        S["entries"] += 1
                        record_trade(window_ts, st["slug"], cont, dom, other, kicker, signal_ask, entry, got)
                        log_event(f"✅ FILLED {cont} @ ${entry:.3f} x{got:.0f} (slip {entry - signal_ask:+.3f}, liq ${dom/1000:.0f}K)", "green")
                        action = "FILLED"
                    else:
                        S["no_fill"] += 1
                        log_event(f"😕 no fill @ ${ask:.3f} — missed", "yellow")
                        action = "NO_FILL"

            # 🌙 log EVERY evaluation — the small-liq direction-validation dataset
            append_csv(EVAL_LOG, EVAL_FIELDS, {
                "timestamp": datetime.now(ET).strftime("%Y-%m-%d %H:%M:%S"),
                "market_ts": window_ts, "slug": st["slug"] or "",
                "long_liq_usd": f"{long_liq:.0f}", "short_liq_usd": f"{short_liq:.0f}",
                "dominant_side": ("long" if long_liq > short_liq else "short") if dom > 0 else "",
                "cont_side": cont or "", "minutes_left": f"{time_left / 60:.2f}",
                "best_bid": "" if bid is None else f"{bid:.4f}",
                "best_ask": "" if ask is None else f"{ask:.4f}",
                "action": action, "paper": int(PAPER_MODE)})

        if now - last_draw >= 0.5:
            last_draw = now
            draw(st, tape)
        time.sleep(0.5)


# ============================================================================
# 🌙 MOON DEV - DASHBOARD
# ============================================================================
def clear_screen():
    os.system('cls' if os.name == 'nt' else 'clear')


def draw_tracker():
    rows = [r for r in read_csv_rows(TRADES_FILE) if r["result"] != "NO_FILL"]
    print(colored(f"  ========== 🌙 MOON DEV'S TRADE TRACKER ({len(rows)} trades) 🌙 ==========", "yellow", attrs=['bold']))
    for r in rows[-20:]:
        if r["result"] == "WIN":
            res, rcol = f"WIN  +${float(r['pnl_usd']):.2f}", "green"
        elif r["result"] == "LOSS":
            res, rcol = f"LOSS -${abs(float(r['pnl_usd'])):.2f}", "red"
        else:
            res, rcol = "PENDING", "yellow"
        kick = " 💪" if r.get("kicker") == "1" else ""
        print(colored(f"  {r['timestamp'][5:16]} | {r['side']:<4} @ {float(r['entry_ask']):.2f}{kick} | ", "white")
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


def draw(st, tape):
    clear_screen()
    draw_tracker()
    mode = colored("📝 PAPER", "yellow", attrs=['bold']) if PAPER_MODE else colored("🔴 LIVE · $5 real", "red", attrs=['bold'])
    print(colored("\n  🌊 MOON DEV's SMALL-LIQ CONTINUATION 30-45 — the $25K-$500K hole 🌊  ", "cyan", attrs=['bold']) + mode + "\n")
    print(colored(f"  entries {S['entries']:<3} skips: no-liq {S['no_liq']:<4} mega {S['mega_skip']:<3} hi {S['ask_high']:<3} "
                  f"lo {S['ask_low']:<3} late {S['too_late']:<3} no-fill {S['no_fill']:<3}"
                  f"{' 🛑 DAILY STOP' if S['halted'] else ''}", "white"))
    tl = tape["time_left"]
    mins, secs = max(0, tl) // 60, max(0, tl) % 60
    in_band = TIME_BAND[0] <= tl <= TIME_BAND[1]
    print(colored(f"\n  ⏱  time left {mins}:{secs:02d}  ({'IN BAND 🎯' if in_band else 'out of band'})",
                  "green" if in_band else "yellow", attrs=['bold']))
    lcol = "red" if tape["long"] >= LIQ_MIN_USD else "white"
    scol = "green" if tape["short"] >= LIQ_MIN_USD else "white"
    print(colored(f"\n  💥 liqs 2min: ", "white")
          + colored(f"longs rekt ${tape['long']:>10,.0f}", lcol, attrs=['bold'])
          + colored("  |  ", "white")
          + colored(f"shorts rekt ${tape['short']:>10,.0f}", scol, attrs=['bold']))
    cont_s = tape["cont"] or "—"
    ask_s = f"${tape['ask']:.3f}" if tape["ask"] else "—"
    status = ""
    if st["entered"]:
        status = colored(f"  ✅ IN x{st['shares']:.0f} — riding", "green", attrs=['bold'])
    elif tape["tag"] == "SKIP_MEGA":
        status = colored("  🚫 MEGA territory (≥$500K) — mega_liq_continuation owns this", "yellow")
    elif tape["cont"]:
        status = colored(f"  🔥 SIGNAL → {tape['cont']}", "magenta", attrs=['bold'])
    print(colored(f"  continuation: {cont_s}  ask {ask_s}", "cyan") + status)
    print()
    print(colored("  ┌───────────────────────── 📜 RECENT ─────────────────────────┐", "white"))
    for ts, msg, color in EVENT_LOG:
        print(colored((f"  │ {ts} {msg}")[:64].ljust(64) + "│", color))
    print(colored("  └──────────────────────────────────────────────────────────────┘", "white"))
    up = (time.time() - SESSION_START) / 60
    print(colored(f"\n  🌙 liq ${LIQ_MIN_USD/1000:.0f}K-${LIQ_MEGA_USD/1000:.0f}K | ask {PRICE_ZONE[0]}-{PRICE_ZONE[1]} | "
                  f"{TIME_BAND[0]}-{TIME_BAND[1]}s | ${USD_SIZE} base | up {up:.1f}m — Ctrl+C", "magenta"))


if __name__ == "__main__":
    tag = "PAPER MODE — logging would-be fills" if PAPER_MODE else "LIVE on AUG14 (real money, $5 base)"
    print(colored(f"🌙 Moon Dev's Small-Liq Continuation 30-45 — {tag}...", "cyan", attrs=['bold']))
    print(colored(f"📒 trades: {TRADES_FILE}\n📒 eval:   {EVAL_LOG}", "cyan"))
    if api is not None:
        print(colored("📡 testing liq feed...", "yellow"))
        l, s = get_btc_liquidations_2min()
        print(colored(f"📡 liqs 2min — longs ${l:,.0f} / shorts ${s:,.0f}", "cyan"))
    time.sleep(0.4)
    while True:
        try:
            main()
        except KeyboardInterrupt:
            print()
            print(colored("  ╔════════════════════════════════════════════════════╗", "yellow", attrs=['bold']))
            print(colored("  ║  🌊 MOON DEV — SMALL-LIQ CONTINUATION STOPPED 🌊   ║", "yellow", attrs=['bold']))
            print(colored("  ╚════════════════════════════════════════════════════╝", "yellow", attrs=['bold']))
            print(colored(f"  🎯 entries {S['entries']}  day P&L ${S['daily_pnl']:+.2f}", "green", attrs=['bold']))
            print(colored(f"  💾 {TRADES_FILE}", "cyan"))
            print(colored("  🌙 Moon Dev out — the tape doesn't lie.", "magenta"))
            print()
            break
        except Exception as e:
            print(colored(f"  💥 crashed: {type(e).__name__}: {e} — restarting in 3s", "red", attrs=['bold']))
            _CLIENT_CACHE = None
            time.sleep(3)
            continue
