#!/opt/anaconda3/envs/tflow/bin/python
"""
================================================================================
🌙 MOON DEV's BOX BUILDER v1.0 (Fable July 18th Fleet, Idea #2)
================================================================================
The early-window two-sided maker on BTC 5-minute Up/Down markets.

THESIS (from Moon Dev's real logs, nothing invented):
  Quote post-only bids on BOTH UP and DOWN in the FIRST HALF of each window,
  paying a combined bid_UP + bid_DOWN <= 0.94. When both legs fill, the pair
  redeems for exactly $1.00 at resolution, adverse selection stops being the
  enemy and becomes the thing that pays us.

EVIDENCE (fable_5min_maker_log / v5_89c_maker_log / order error log):
  ✅ Early quoting fills: fable maker 57% fill rate at T-240
  ❌ Late deep bids never fill: v5 armed 35x at 0.89 → ZERO fills
  ❌ Chasing the book = 249 post-only "crosses book" rejects → quotes are STATIC
  ❌ Directional maker fills carry ~9c adverse selection (losers avg 0.647 entry
     vs 0.736 winners) → a completed box is direction-neutral, poison → profit

RULES (do not "improve" these back out):
  ✅ Arm only if best_ask_UP + best_ask_DOWN >= 1.03 at window open
  ✅ Quote T-300..T-150 ONLY, static bids, reprice at most every 20s and only
     if >2c behind best bid AND the 0.94 cap still holds
  ✅ One leg fills at p1 → completion ladder: raise other bid to
     min(best_bid, 0.97 - p1); if ask <= 0.99 - p1 LIFT IT (marketable GTC ,
     real FAK gets 400'd under $1, v6 pattern)
  ✅ Stranded leg at T-90 → live coa: hold only if our side is coa-favored with
     coa >= 1.0, otherwise CUT at best bid
  ✅ Cancel ALL resting orders at T-10, never carry an order into rollover
  ✅ One box per window. 5 shares/leg until the CSV proves the both-fill rate.

Account: AUG14 | CLOB V2 SDK (V1 post_order now throws PolyApiException)
Built by Moon Dev 🌙
================================================================================
"""

import sys
import os

# 🌙 Moon Dev - Auto re-exec with tflow python if we're in the wrong env
TFLOW_PYTHON = "/opt/anaconda3/envs/tflow/bin/python"
if os.path.exists(TFLOW_PYTHON) and sys.executable != TFLOW_PYTHON:
    os.execv(TFLOW_PYTHON, [TFLOW_PYTHON] + sys.argv)

import time
import json
import requests
import pandas as pd
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv
from termcolor import colored

# ============================================================================
# 🌙 MOON DEV - PATH SETUP
# ============================================================================
BOT_DIR = os.path.dirname(os.path.abspath(__file__))                  # fable_july_18th/
PROJECT_ROOT = os.path.dirname(os.path.dirname(BOT_DIR))              # repo root
load_dotenv(os.path.join(PROJECT_ROOT, '.env'))

# ============================================================================
# 🌙 MOON DEV - KEY PARAMS (the whole edge lives in these numbers)
# ============================================================================
PAPER_MODE = False                  # 🌙 LIVE FIRE! Flip back to True for paper testing

ARM_MIN_SPREAD_SUM = 1.03           # best_ask_UP + best_ask_DOWN must be >= this at open
BID_SUM_CAP = 0.94                  # bid_UP + bid_DOWN cap → target >= 6c per box
COMPLETE_MAKER_CAP = 0.97           # after 1st fill: other bid <= 0.97 - p1 (>= 3c locked)
COMPLETE_TAKER_CAP = 0.99           # lift the ask if <= 0.99 - p1 (>= 1c locked box)
QUOTE_CUTOFF_SEC = 150              # no NEW two-sided quotes after T-150 (first half only)
BAILOUT_SEC = 90                    # stranded-leg coa decision at T-90
CANCEL_ALL_SEC = 10                 # cancel every resting order at T-10
REPRICE_INTERVAL = 20               # static quotes: reprice at most every 20s...
REPRICE_BEHIND = 0.02               # ...and only if > 2c behind that side's best bid
MIN_COA_HOLD = 1.0                  # stranded leg held naked only if coa-favored & coa >= 1.0

SHARES_PER_LEG = 5                  # exchange floor, SAME count both legs so the box redeems clean
HARD_CAP_SHARES = 20                # 🌙 Moon Dev - do NOT raise past this until 100 windows logged
MIN_SHARES = 5                      # Polymarket minimum order size
PRICE_TICK = 0.01                   # 1c ticks

KILL_SWITCH_WR = 0.40               # Pause arming if trailing-50 resolved pnl>0 rate < 40%
KILL_SWITCH_WINDOW = 50
KILL_SWITCH_MIN_TRADES = 20

BOT_POLL_INTERVAL = 3               # seconds between cycles (books + fill checks)
MARKET_DURATION = 300               # btc-updown-5m markets = 300 seconds

# === Files ===
DATA_DIR = os.path.join(BOT_DIR, "data")
LOG_FILE = os.path.join(DATA_DIR, "box_builder_log.csv")
LOG_COLS = ['snapshot_time', 'window_slug', 'spread_sum_at_open', 'bid_up', 'bid_dn',
            'action', 'up_fill_px', 'dn_fill_px', 'box_cost', 'box_locked_pnl',
            'stranded_side', 'stranded_px', 'coa_at_t90', 'bailout_action',
            'outcome', 'won', 'pnl_usd', 'shares', 'paper']

ET = timezone(timedelta(hours=-5))

# ============================================================================
# 🌙 MOON DEV - ACCOUNT CONFIGURATION (AUG14, keeps OG free)
# ============================================================================
ACCOUNT_SUFFIX = "_AUG14"
PRIVATE_KEY_ENV_NAME = f"PRIVATE_KEY{ACCOUNT_SUFFIX}"
PUBLIC_KEY_ENV_NAME = f"PUBLIC_KEY{ACCOUNT_SUFFIX}"
SIGNATURE_TYPE = 2                  # Gnosis Safe

# ============================================================================
# 🌙 MOON DEV - V2 CLOB CLIENT (V1 post_order is dead)
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


def place_maker_bid(token_id, price, size):
    """🌙 Moon Dev - POST-ONLY GTC bid. The API REJECTS post-only orders that
    would cross the book (249 logged rejects taught us this), on that reject
    we re-price 1c LOWER and retry. We never chase up into the book.

    Returns (order_id, price_actually_posted) or (None, None)."""
    from py_clob_client_v2.clob_types import OrderArgs, OrderType
    client = _build_client()
    px = round(price, 2)
    for attempt in range(3):
        if px < PRICE_TICK:
            return None, None
        order_args = OrderArgs(token_id=str(token_id), price=float(px), size=float(size), side="BUY")
        print(colored(f"   🪑 Moon Dev - Resting post-only bid @ ${px:.2f} x{size} shares", "cyan"))
        try:
            resp = client.create_and_post_order(order_args, order_type=OrderType.GTC, post_only=True)
        except Exception as e:
            err = str(e).lower()
            if 'post-only' in err and 'cross' in err:
                px = round(px - PRICE_TICK, 2)
                print(colored(f"   ↻ Moon Dev - post-only would cross book, re-pricing 1c lower → ${px:.2f}", "yellow"))
                continue
            if any(t in err for t in ['timeout', 'readtimeout', 'duplicated', 'request exception']):
                print(colored("   ⚠️ Moon Dev - order timed out, assuming it landed", "yellow"))
                return 'timeout-assumed-live', px
            print(colored(f"   ❌ Moon Dev - maker bid failed: {type(e).__name__}: {e}", "red"))
            return None, None
        if resp and resp.get('orderID'):
            return resp['orderID'], px
        if resp and 'cross' in str(resp).lower():
            px = round(px - PRICE_TICK, 2)
            print(colored(f"   ↻ Moon Dev - reject says crosses book, re-pricing 1c lower → ${px:.2f}", "yellow"))
            continue
        print(colored(f"   ❌ Moon Dev - maker bid rejected: {str(resp)[:120]}", "red"))
        return None, None
    return None, None


def place_taker_order(token_id, side, price, size):
    """🌙 Moon Dev - Marketable GTC that CROSSES the book (post_only=False).
    Why GTC and not FAK/FOK: Polymarket 400s marketable FAK/FOK when the
    crossable amount is < $1, verified live (v6 pattern). GTC crosses just
    the same and any remainder rests at our price (cancelled at T-10)."""
    from py_clob_client_v2.clob_types import OrderArgs, OrderType
    client = _build_client()
    order_args = OrderArgs(token_id=str(token_id), price=float(price), size=float(size), side=side.upper())
    print(colored(f"   🎯 Moon Dev - CROSSING with {side} @ ${price:.2f} x{size} shares (GTC taker)", "cyan", attrs=['bold']))
    try:
        resp = client.create_and_post_order(order_args, order_type=OrderType.GTC, post_only=False)
        return resp or {}
    except Exception as e:
        err = str(e).lower()
        if any(t in err for t in ['timeout', 'readtimeout', 'duplicated', 'request exception']):
            print(colored("   ⚠️ Moon Dev - taker order timed out, will verify via positions", "yellow"))
            return {"status": "timeout"}
        print(colored(f"   ❌ Moon Dev - taker order failed: {type(e).__name__}: {e}", "red"))
        return {}


def cancel_token_orders(token_id):
    """🌙 Moon Dev - Cancel ALL resting orders on a token."""
    from py_clob_client_v2.clob_types import OrderMarketCancelParams
    try:
        _build_client().cancel_market_orders(OrderMarketCancelParams(asset_id=str(token_id)))
    except Exception as e:
        print(colored(f"   ⚠️ Moon Dev - cancel failed: {type(e).__name__}", "yellow"))


def get_position_size(token_id):
    """🌙 Moon Dev - How many shares of token_id we actually hold (fill check)."""
    pub = os.getenv(PUBLIC_KEY_ENV_NAME)
    r = requests.get("https://data-api.polymarket.com/positions",
                     params={'user': pub, 'limit': 500, 'sortBy': 'CURRENT', 'sortDirection': 'DESC'},
                     timeout=10)
    if r.status_code != 200:
        return 0.0
    for pos in r.json():
        if str(pos.get('asset')) == str(token_id):
            return float(pos.get('size', 0))
    return 0.0


def get_order_matched(order_id):
    """🌙 Moon Dev - size_matched for an order id via client.get_order (2nd fill check)."""
    if not order_id or order_id in ('paper', 'timeout-assumed-live'):
        return None
    try:
        order = _build_client().get_order(order_id)
    except Exception as e:
        print(colored(f"   ⚠️ Moon Dev - get_order failed: {type(e).__name__}", "yellow"))
        return None
    if not order:
        return None
    if isinstance(order, dict):
        return float(order.get('size_matched', 0) or 0)
    return float(getattr(order, 'size_matched', 0) or 0)

# ============================================================================
# 🌙 MOON DEV - MARKET DISCOVERY (btc-updown-5m-{T})
# ============================================================================


def get_current_market_timestamp():
    return (int(time.time()) // MARKET_DURATION) * MARKET_DURATION


def get_time_remaining(market_ts):
    return MARKET_DURATION - (int(time.time()) - market_ts)


def get_market_info(market_ts):
    """🌙 Moon Dev - Find the active btc-updown-5m market + UP/DOWN token ids"""
    market_slug = f"btc-updown-5m-{market_ts}"
    r = requests.get("https://gamma-api.polymarket.com/markets",
                     params={'slug': market_slug, 'closed': 'false', 'active': 'true'},
                     timeout=10)
    if r.status_code != 200:
        return None
    markets = r.json()
    if not markets:
        return None
    market = markets[0]
    token_ids = json.loads(market['clobTokenIds'])   # [UP, DOWN] matches outcomes order
    print(colored(f"   ✅ Moon Dev - Found market: {market['question']}", "green"))
    return {
        'market_id': market['id'],
        'up_token_id': token_ids[0],
        'down_token_id': token_ids[1],
        'question': market['question'],
        'slug': market_slug,
    }


def get_order_book(token_id):
    """🌙 Moon Dev - Best bid/ask from CLOB"""
    r = requests.get("https://clob.polymarket.com/book", params={'token_id': token_id}, timeout=10)
    if r.status_code != 200:
        return None
    data = r.json()
    bids, asks = data.get('bids', []), data.get('asks', [])
    if not bids or not asks:
        return None
    best_bid = max(float(b['price']) for b in bids)
    best_ask = min(float(a['price']) for a in asks)
    return {'best_bid': best_bid, 'best_ask': best_ask, 'spread': best_ask - best_bid}

# ============================================================================
# 🌙 MOON DEV - LIVE COA SIGNAL (v5/v6 brain, real 1m bars, Binance→Coinbase)
# ============================================================================


def fetch_window_bars(window_ts):
    """🌙 Moon Dev - real 1-min BTC OHLC for this window (Binance, Coinbase fallback). {} on fail."""
    bars = {}
    r = requests.get("https://api.binance.com/api/v3/klines",
                     params={'symbol': 'BTCUSDT', 'interval': '1m',
                             'startTime': window_ts * 1000, 'endTime': (window_ts + 299) * 1000},
                     timeout=10)
    if r.status_code == 200:
        for k in r.json():
            off = int((k[0] / 1000 - window_ts) // 60)
            if 0 <= off <= 4:
                bars[off] = (float(k[1]), float(k[2]), float(k[3]), float(k[4]))
    if not bars:
        r = requests.get("https://api.exchange.coinbase.com/products/BTC-USD/candles",
                         params={'granularity': 60,
                                 'start': datetime.utcfromtimestamp(window_ts).isoformat(),
                                 'end': datetime.utcfromtimestamp(window_ts + 299).isoformat()},
                         timeout=10)
        if r.status_code == 200:
            for c in r.json():
                off = int((int(c[0]) - window_ts) // 60)
                if 0 <= off <= 4:
                    bars[off] = (float(c[3]), float(c[2]), float(c[1]), float(c[4]))
    return bars


def compute_live_coa(window_ts):
    """🌙 Moon Dev - coa at T-90 from the bars closed SO FAR (bars 0..2 are done
    by T-90; v5/v6 use bars 0..3 at T-60, same math, live cut). None = no data."""
    bars = fetch_window_bars(window_ts)
    done = sorted(o for o in bars if o <= 3)
    if 0 not in bars or len(done) < 3:
        return None
    open_p = bars[0][0]
    cushion = bars[max(done)][3] - open_p
    atr = sum(bars[o][1] - bars[o][2] for o in done) / len(done)
    if atr <= 0 or open_p <= 0:
        return None
    return {'coa': abs(cushion) / atr, 'cushion': cushion,
            'favored': 'UP' if cushion > 0 else 'DOWN'}

# ============================================================================
# 🌙 MOON DEV - LOGGING (one row per window, EVEN SKIPS, v5 discipline)
# ============================================================================


def append_log(row):
    """🌙 Moon Dev - Append one window row to the CSV (fuel for the next iteration)"""
    os.makedirs(DATA_DIR, exist_ok=True)
    full = {c: row.get(c, '') for c in LOG_COLS}
    df = pd.DataFrame([full], columns=LOG_COLS)
    header = not os.path.exists(LOG_FILE)
    df.to_csv(LOG_FILE, mode='a', header=header, index=False)


def resolve_pending_windows():
    """🌙 Moon Dev - Fill outcome/won/pnl on PENDING rows via gamma outcomePrices.
    Ties resolve UP per Chainlink. Locked pnl (box/cut) was written at window end;
    only STRANDED_HELD needs the resolution to price its pnl."""
    if not os.path.exists(LOG_FILE):
        return
    df = pd.read_csv(LOG_FILE)
    pending = df[df['outcome'] == 'PENDING']
    if pending.empty:
        return
    changed = False
    for idx, row in pending.iterrows():
        market_ts = int(str(row['window_slug']).rsplit('-', 1)[1])
        if time.time() < market_ts + MARKET_DURATION + 30:
            continue  # give the oracle a beat
        r = requests.get("https://gamma-api.polymarket.com/markets",
                         params={'slug': row['window_slug']}, timeout=10)
        if r.status_code != 200 or not r.json():
            continue
        market = r.json()[0]
        prices = json.loads(market.get('outcomePrices', '[]') or '[]')
        if len(prices) != 2:
            continue
        up_price = float(prices[0])
        if up_price not in (0.0, 1.0):
            continue  # not resolved yet
        winner = "UP" if up_price == 1.0 else "DOWN"
        action = row['action']
        shares = float(row['shares'])
        if action == 'BOX_COMPLETE':
            won, pnl = 1, float(row['pnl_usd'])            # locked at completion
        elif action == 'STRANDED_CUT':
            pnl = float(row['pnl_usd'])                    # locked at the cut
            won = 1 if pnl > 0 else 0
        elif action == 'STRANDED_HELD':
            px = float(row['stranded_px'])
            won = 1 if row['stranded_side'] == winner else 0
            pnl = shares * (1.0 - px) if won else -shares * px
            df.at[idx, 'pnl_usd'] = round(pnl, 2)
        else:                                              # ARMED_NOFILL etc.
            won, pnl = '', 0.0
        df.at[idx, 'outcome'] = winner
        df.at[idx, 'won'] = won
        if action == 'STRANDED_HELD':
            df.at[idx, 'pnl_usd'] = round(pnl, 2)
        changed = True
        emoji = "🏆" if won == 1 else ("💀" if won == 0 else "📭")
        print(colored(f"   {emoji} Moon Dev - Resolved {row['window_slug']}: {action} → "
                      f"{winner} wins (${float(pnl):+.2f})", "green" if won == 1 else "red"))
    if changed:
        df.to_csv(LOG_FILE, index=False)


def kill_switch_active():
    """🌙 Moon Dev - True = PAUSE arming. Trailing-50 resolved pnl>0 rate < 40%."""
    if not os.path.exists(LOG_FILE):
        return False
    df = pd.read_csv(LOG_FILE)
    resolved = df[(df['outcome'].isin(['UP', 'DOWN'])) & (df['won'].isin([0, 1, '0', '1', 0.0, 1.0]))]
    resolved = resolved.tail(KILL_SWITCH_WINDOW)
    if len(resolved) < KILL_SWITCH_MIN_TRADES:
        return False
    wr = (pd.to_numeric(resolved['won']) == 1).mean()
    if wr < KILL_SWITCH_WR:
        print(colored(f"   🚨 Moon Dev - KILL SWITCH! Trailing-{len(resolved)} win rate "
                      f"{wr*100:.1f}% < {KILL_SWITCH_WR*100:.0f}%, arming PAUSED", "red", attrs=['bold']))
        return True
    return False


def print_trade_tracker():
    """🌙 Moon Dev - Clickable tracker of past windows, printed FIRST every cycle."""
    if not os.path.exists(LOG_FILE):
        return
    df = pd.read_csv(LOG_FILE)
    traded = df[~df['action'].astype(str).str.startswith('SKIP')]
    if traded.empty:
        return
    print(colored(f"============ 🌙 MOON DEV'S TRADE TRACKER ({len(traded)} armed windows) 🌙 ============", "cyan", attrs=['bold']))
    for _, t in traded.tail(20).iterrows():
        ts = pd.to_datetime(t['snapshot_time']).strftime('%m-%d %H:%M')
        action = str(t['action'])
        if t['outcome'] in ('UP', 'DOWN') or action in ('BOX_COMPLETE', 'STRANDED_CUT'):
            pnl = float(t['pnl_usd']) if str(t['pnl_usd']) not in ('', 'nan') else 0.0
            status, color = f"${pnl:+.2f}", ("green" if pnl > 0 else "red")
        else:
            status, color = "PENDING", "white"
        cost = str(t['box_cost']) if str(t['box_cost']) not in ('', 'nan') else '..'
        print(colored(f"{ts} | {action:<14} box {cost:<5} | {status:<8} | "
                      f"https://polymarket.com/event/{t['window_slug']}", color))
    print(colored("=" * 78, "cyan", attrs=['bold']))


def print_session_footer():
    """🌙 Moon Dev - boxes built, both-fill %, locked PnL, stranded PnL."""
    if not os.path.exists(LOG_FILE):
        return
    df = pd.read_csv(LOG_FILE)
    armed = df[~df['action'].astype(str).str.startswith('SKIP')]
    filled = armed[armed['action'].isin(['BOX_COMPLETE', 'STRANDED_HELD', 'STRANDED_CUT'])]
    boxes = (armed['action'] == 'BOX_COMPLETE').sum()
    both_fill_pct = boxes / len(filled) * 100 if len(filled) else 0.0
    locked = pd.to_numeric(df[df['action'] == 'BOX_COMPLETE']['pnl_usd'], errors='coerce').sum()
    stranded = pd.to_numeric(df[df['action'].isin(['STRANDED_HELD', 'STRANDED_CUT'])]['pnl_usd'], errors='coerce').sum()
    print(colored(f"\n📦 Windows: {len(df)} | Armed: {len(armed)} | Boxes built: {boxes} | "
                  f"Both-fill: {both_fill_pct:.0f}% | Locked PnL: ${locked:+.2f} | "
                  f"Stranded PnL: ${stranded:+.2f}, Built by Moon Dev 🌙", "cyan", attrs=['bold']))

# ============================================================================
# 🌙 MOON DEV - BID MATH
# ============================================================================


def cap_bids(bid_up, bid_dn, cap=BID_SUM_CAP):
    """🌙 Moon Dev - Join both best bids but back off symmetrically in 1c ticks
    until bid_UP + bid_DOWN <= cap (higher leg gives first)."""
    bu, bd = round(bid_up, 2), round(bid_dn, 2)
    while round(bu + bd, 2) > cap and (bu > PRICE_TICK or bd > PRICE_TICK):
        if bu >= bd:
            bu = round(bu - PRICE_TICK, 2)
        else:
            bd = round(bd - PRICE_TICK, 2)
    return max(bu, PRICE_TICK), max(bd, PRICE_TICK)

# ============================================================================
# 🌙 MOON DEV - MAIN BOT
# ============================================================================


class BoxBuilderBot:
    def __init__(self):
        self.reset()

    def reset(self):
        self.market_info = None
        self.armed = None               # None = undecided, True/False after open check
        self.skip_reason = None
        self.spread_sum_at_open = None
        self.cancelled_all = False
        self.bailout_done = False
        self.bailout_action = ''
        self.coa_at_t90 = None
        self.completion_last_raise = 0.0
        self.legs = {
            'UP':   {'token_id': None, 'order_id': None, 'order_px': None, 'fill_px': None, 'fill_shares': 0.0},
            'DOWN': {'token_id': None, 'order_id': None, 'order_px': None, 'fill_px': None, 'fill_shares': 0.0},
        }
        self.last_reprice = 0.0

    # ==== helpers ==========================================================
    def leg_filled(self, side):
        return self.legs[side]['fill_px'] is not None

    def filled_sides(self):
        return [s for s in ('UP', 'DOWN') if self.leg_filled(s)]

    def cancel_all_resting(self):
        """🌙 Moon Dev - kill every open order on both tokens."""
        for side in ('UP', 'DOWN'):
            leg = self.legs[side]
            if leg['order_id'] is not None and not self.leg_filled(side):
                if not PAPER_MODE:
                    cancel_token_orders(leg['token_id'])
                leg['order_id'] = None   # keep order_px so the CSV still shows what we quoted
        print(colored("   🚫 Moon Dev - all resting orders cancelled", "yellow"))

    def mark_filled(self, side, px, shares):
        leg = self.legs[side]
        leg['fill_px'] = round(float(px), 2)
        leg['fill_shares'] = shares
        leg['order_id'] = None
        leg['order_px'] = None
        print(colored(f"   🧱 Moon Dev leg filled: {side} @ {leg['fill_px']:.2f}, "
                      f"hunting completion <= {COMPLETE_TAKER_CAP - leg['fill_px']:.2f}",
                      "green", attrs=['bold']))

    def check_fills(self, books):
        """🌙 Moon Dev - detect fills via positions API + get_order (+ paper sim)."""
        for side in ('UP', 'DOWN'):
            leg = self.legs[side]
            if self.leg_filled(side) or leg['order_id'] is None:
                continue
            if PAPER_MODE:
                book = books.get(side)
                # 🌙 paper fill: someone crossed down into our resting bid
                if book and book['best_ask'] <= leg['order_px'] + 1e-9:
                    self.mark_filled(side, leg['order_px'], SHARES_PER_LEG)
                continue
            held = get_position_size(leg['token_id'])
            if held > 0:
                self.mark_filled(side, leg['order_px'], held)
                continue
            matched = get_order_matched(leg['order_id'])
            if matched and matched > 0:
                self.mark_filled(side, leg['order_px'], matched)

    def place_leg_bid(self, side, price):
        """🌙 Moon Dev - rest one post-only bid on a leg."""
        leg = self.legs[side]
        if PAPER_MODE:
            leg['order_id'], leg['order_px'] = 'paper', round(price, 2)
            print(colored(f"   📄 Moon Dev - PAPER bid {side} @ ${price:.2f} x{SHARES_PER_LEG}", "yellow"))
            return True
        oid, px = place_maker_bid(leg['token_id'], price, SHARES_PER_LEG)
        if oid:
            leg['order_id'], leg['order_px'] = oid, px
            return True
        return False

    # ==== state machine ====================================================
    def try_arm(self, books, paused):
        """🌙 Moon Dev - the one arming decision, at window open. Wide book or bust."""
        up_book, dn_book = books.get('UP'), books.get('DOWN')
        if not up_book or not dn_book:
            return  # books not indexed yet, stay undecided
        spread_sum = round(up_book['best_ask'] + dn_book['best_ask'], 2)
        self.spread_sum_at_open = spread_sum
        if paused:
            self.armed, self.skip_reason = False, 'SKIP_KILLSWITCH'
            return
        if spread_sum < ARM_MIN_SPREAD_SUM:
            self.armed, self.skip_reason = False, 'SKIP_NARROW'
            print(colored(f"   🙅 Moon Dev - spread_sum {spread_sum:.2f} < {ARM_MIN_SPREAD_SUM:.2f}, "
                          f"book too tight to box, SKIP", "yellow"))
            return
        bid_up, bid_dn = cap_bids(up_book['best_bid'], dn_book['best_bid'])
        print(colored(f"   🌙 Moon Dev BOX BUILDER | quoting UP {bid_up:.2f} / DOWN {bid_dn:.2f} | "
                      f"cap {BID_SUM_CAP:.2f} | spread_sum {spread_sum:.2f}", "green", attrs=['bold']))
        ok_up = self.place_leg_bid('UP', bid_up)
        ok_dn = self.place_leg_bid('DOWN', bid_dn)
        if not ok_up and not ok_dn:
            self.armed, self.skip_reason = False, 'SKIP_ORDER_FAIL'
            return
        self.armed = True
        self.last_reprice = time.time()

    def maybe_reprice(self, books):
        """🌙 Moon Dev - STATIC quotes: at most one reprice per 20s, only if a bid
        is >2c behind its best bid AND the 0.94 cap still holds (no chasing ,
        chasing earned 249 post-only rejects last time)."""
        now = time.time()
        if now - self.last_reprice < REPRICE_INTERVAL:
            return
        up_book, dn_book = books.get('UP'), books.get('DOWN')
        if not up_book or not dn_book:
            return
        self.last_reprice = now
        for side, book in (('UP', up_book), ('DOWN', dn_book)):
            leg = self.legs[side]
            other = self.legs['DOWN' if side == 'UP' else 'UP']
            if self.leg_filled(side) or leg['order_px'] is None:
                continue
            behind = book['best_bid'] - leg['order_px']
            if behind <= REPRICE_BEHIND:
                continue
            other_px = other['order_px'] if other['order_px'] is not None else 0.0
            new_px = round(min(book['best_bid'], BID_SUM_CAP - other_px), 2)
            if new_px <= leg['order_px']:
                continue  # cap won't let us move up, stay static
            print(colored(f"   ↻ Moon Dev - {side} bid {leg['order_px']:.2f} is {behind:.2f} behind, "
                          f"repricing → {new_px:.2f} (cap holds)", "cyan"))
            if not PAPER_MODE:
                cancel_token_orders(leg['token_id'])
            leg['order_id'], leg['order_px'] = None, None
            self.place_leg_bid(side, new_px)

    def work_completion(self, books, time_remaining):
        """🌙 Moon Dev - COMPLETION LADDER. One leg is in at p1:
        - raise other bid to min(best_bid, 0.97 - p1) (still >= 3c locked)
        - if ask <= 0.99 - p1, LIFT IT (marketable GTC), a guaranteed >= 1c box
          beats a naked leg every time."""
        filled_side = self.filled_sides()[0]
        other_side = 'DOWN' if filled_side == 'UP' else 'UP'
        p1 = self.legs[filled_side]['fill_px']
        shares = self.legs[filled_side]['fill_shares'] or SHARES_PER_LEG
        leg = self.legs[other_side]
        book = books.get(other_side)
        if not book:
            return

        # taker path, checked every poll
        taker_cap = round(COMPLETE_TAKER_CAP - p1, 2)
        if book['best_ask'] <= taker_cap:
            print(colored(f"   🏋️ Moon Dev - {other_side} ask {book['best_ask']:.2f} <= {taker_cap:.2f}, "
                          f"LIFTING to complete the box!", "green", attrs=['bold']))
            if PAPER_MODE:
                self.mark_filled(other_side, book['best_ask'], shares)
            else:
                if leg['order_id'] is not None:
                    cancel_token_orders(leg['token_id'])
                    leg['order_id'], leg['order_px'] = None, None
                resp = place_taker_order(leg['token_id'], 'BUY', book['best_ask'], shares)
                if resp:
                    time.sleep(2)
                    held = get_position_size(leg['token_id'])
                    if held > 0:
                        self.mark_filled(other_side, book['best_ask'], held)
            return

        # maker raise path, rate-limited like every other quote
        now = time.time()
        if now - self.completion_last_raise < REPRICE_INTERVAL and leg['order_px'] is not None:
            return
        maker_cap = round(COMPLETE_MAKER_CAP - p1, 2)
        target = round(min(book['best_bid'], maker_cap), 2)
        if leg['order_px'] is not None and target <= leg['order_px']:
            return  # only ever RAISE toward completion
        self.completion_last_raise = now
        print(colored(f"   🪜 Moon Dev - completion ladder: raising {other_side} bid → {target:.2f} "
                      f"(cap {maker_cap:.2f})", "cyan"))
        if not PAPER_MODE and leg['order_id'] is not None:
            cancel_token_orders(leg['token_id'])
        leg['order_id'], leg['order_px'] = None, None
        self.place_leg_bid(other_side, target)

    def run_bailout(self, books, market_ts):
        """🌙 Moon Dev - STRANDED LEG at T-90: live coa decides. Hold naked ONLY
        if our leg is the coa-favored side with coa >= 1.0, otherwise CUT at
        the bid (the 0.647-entry losers in the fable log are exactly the trade
        we refuse to ride)."""
        self.bailout_done = True
        filled_side = self.filled_sides()[0]
        other_side = 'DOWN' if filled_side == 'UP' else 'UP'
        leg = self.legs[filled_side]
        shares = leg['fill_shares'] or SHARES_PER_LEG

        # completion is over, pull the other leg's resting bid
        oleg = self.legs[other_side]
        if oleg['order_id'] is not None:
            if not PAPER_MODE:
                cancel_token_orders(oleg['token_id'])
            oleg['order_id'], oleg['order_px'] = None, None

        sig = compute_live_coa(market_ts)
        self.coa_at_t90 = round(sig['coa'], 2) if sig else ''
        if sig and sig['favored'] == filled_side and sig['coa'] >= MIN_COA_HOLD:
            self.bailout_action = 'HOLD'
            print(colored(f"   🛡️ Moon Dev stranded leg at T-90 | coa {sig['coa']:.2f} favors {filled_side} "
                          f"→ HOLDING naked to expiry", "green"))
            return
        if not sig:
            print(colored("   ⚠️ Moon Dev - no live bars for coa (Binance AND Coinbase dry), "
                          "defaulting to the CUT rule", "yellow"))

        book = books.get(filled_side)
        if not book:
            self.bailout_action = 'CUT_NO_BOOK'
            print(colored("   ❌ Moon Dev - wanted to cut but no book, stuck holding", "red"))
            return
        cut_px = book['best_bid']
        coa_txt = f"{sig['coa']:.2f}" if sig else "n/a"
        print(colored(f"   ⚠️ Moon Dev stranded leg at T-90 | coa {coa_txt} → CUTTING {filled_side} "
                      f"at bid {cut_px:.2f}", "red", attrs=['bold']))
        if PAPER_MODE:
            self.bailout_action = f'CUT@{cut_px:.2f}'
            leg['cut_px'] = cut_px
            return
        resp = place_taker_order(leg['token_id'], 'SELL', cut_px, shares)
        if resp:
            time.sleep(2)
            still_held = get_position_size(leg['token_id'])
            if still_held > 0:
                print(colored(f"   ⚠️ Moon Dev - cut order in, still holding {still_held:.1f} sh (GTC resting)", "yellow"))
        self.bailout_action = f'CUT@{cut_px:.2f}'
        leg['cut_px'] = cut_px

    # ==== window finalize ===================================================
    def finalize_window(self):
        """🌙 Moon Dev - one honest CSV row per window, no matter what happened."""
        if self.market_info is None:
            return
        up, dn = self.legs['UP'], self.legs['DOWN']
        both = self.leg_filled('UP') and self.leg_filled('DOWN')
        one = len(self.filled_sides()) == 1
        shares = SHARES_PER_LEG
        row = {
            'snapshot_time': datetime.now(ET).strftime('%Y-%m-%d %H:%M:%S'),
            'window_slug': self.market_info['slug'],
            'spread_sum_at_open': '' if self.spread_sum_at_open is None else f"{self.spread_sum_at_open:.2f}",
            'bid_up': '' if up['order_px'] is None and up['fill_px'] is None else f"{(up['fill_px'] or up['order_px']):.2f}",
            'bid_dn': '' if dn['order_px'] is None and dn['fill_px'] is None else f"{(dn['fill_px'] or dn['order_px']):.2f}",
            'up_fill_px': '' if up['fill_px'] is None else f"{up['fill_px']:.2f}",
            'dn_fill_px': '' if dn['fill_px'] is None else f"{dn['fill_px']:.2f}",
            'box_cost': '', 'box_locked_pnl': '', 'stranded_side': '', 'stranded_px': '',
            'coa_at_t90': self.coa_at_t90 if self.coa_at_t90 is not None else '',
            'bailout_action': self.bailout_action,
            'outcome': '', 'won': '', 'pnl_usd': '',
            'shares': shares, 'paper': PAPER_MODE,
        }
        if self.armed is False:
            row['action'] = self.skip_reason or 'SKIP'
            row['pnl_usd'] = 0.0
        elif self.armed is None:
            row['action'] = 'SKIP_NO_BOOK'
            row['pnl_usd'] = 0.0
        elif both:
            shares = min(up['fill_shares'] or SHARES_PER_LEG, dn['fill_shares'] or SHARES_PER_LEG)
            cost = round(up['fill_px'] + dn['fill_px'], 2)
            locked = round(shares * (1.0 - cost), 2)
            row.update({'action': 'BOX_COMPLETE', 'box_cost': f"{cost:.2f}",
                        'box_locked_pnl': f"{locked:.2f}", 'pnl_usd': locked,
                        'shares': shares, 'outcome': 'PENDING'})
            print(colored(f"   📦 BOX COMPLETE for Moon Dev: cost {cost:.2f} -> "
                          f"locked ${locked:+.2f} 🔒", "green", attrs=['bold']))
        elif one:
            side = self.filled_sides()[0]
            leg = self.legs[side]
            shares = leg['fill_shares'] or SHARES_PER_LEG
            row.update({'stranded_side': side, 'stranded_px': f"{leg['fill_px']:.2f}", 'shares': shares})
            if self.bailout_action.startswith('CUT@'):
                cut_px = float(self.bailout_action.split('@')[1])
                pnl = round(shares * (cut_px - leg['fill_px']), 2)
                row.update({'action': 'STRANDED_CUT', 'pnl_usd': pnl, 'outcome': 'PENDING'})
            else:
                row.update({'action': 'STRANDED_HELD', 'outcome': 'PENDING'})
        else:
            row['action'] = 'ARMED_NOFILL'
            row['pnl_usd'] = 0.0
            row['outcome'] = 'PENDING'
        append_log(row)
        print(colored(f"   📝 Moon Dev - Logged window: {row['action']}", "yellow"))

    # ==== one full window ===================================================
    def run_market_cycle(self, market_ts):
        market_et = datetime.fromtimestamp(market_ts, tz=ET)
        print("")
        print_trade_tracker()  # 🌙 Moon Dev - tracker FIRST, clickable links up top
        print(colored(f"\n{'='*70}", "cyan"))
        print(colored(f"🌙 Moon Dev - BOX BUILDER CYCLE | {market_et.strftime('%I:%M:%S%p ET')}", "cyan", attrs=['bold']))
        print(colored(f"{'='*70}", "cyan"))

        resolve_pending_windows()
        paused = kill_switch_active()

        if get_time_remaining(market_ts) > MARKET_DURATION - 10:
            time.sleep(3)  # let gamma index the fresh market

        self.market_info = None
        for attempt in range(5):
            self.market_info = get_market_info(market_ts)
            if self.market_info:
                break
            print(colored(f"   🔄 Moon Dev - Market not indexed yet, retry {attempt+1}/5...", "yellow"))
            time.sleep(2)
        if not self.market_info:
            print(colored("   ❌ Moon Dev - Could not find market, skipping cycle", "red"))
            return

        self.legs['UP']['token_id'] = self.market_info['up_token_id']
        self.legs['DOWN']['token_id'] = self.market_info['down_token_id']
        print(colored(f"   🔗 https://polymarket.com/event/{self.market_info['slug']}", "cyan"))

        while True:
            time_remaining = get_time_remaining(market_ts)
            if time_remaining <= 0:
                print(colored("\n   ⏰ Moon Dev - Window closed!", "yellow"))
                break

            books = {'UP': get_order_book(self.legs['UP']['token_id']),
                     'DOWN': get_order_book(self.legs['DOWN']['token_id'])}
            self.check_fills(books)
            both = self.leg_filled('UP') and self.leg_filled('DOWN')
            n_filled = len(self.filled_sides())

            # === T-10: cancel everything still resting, never carry into rollover ===
            if time_remaining <= CANCEL_ALL_SEC:
                if not self.cancelled_all:
                    self.cancelled_all = True
                    self.cancel_all_resting()
                time.sleep(1)
                continue

            # === arming decision (once, at window open with real books) ===
            if self.armed is None:
                if time_remaining >= QUOTE_CUTOFF_SEC:
                    self.try_arm(books, paused)
                else:
                    self.armed, self.skip_reason = False, 'SKIP_NO_BOOK'
                time.sleep(BOT_POLL_INTERVAL)
                continue

            if self.armed is False:
                print(colored(f"\r   😴 Moon Dev - skipped ({self.skip_reason}), watching... "
                              f"{time_remaining}s left   ", "white"), end='', flush=True)
                time.sleep(BOT_POLL_INTERVAL)
                continue

            # === box complete: nothing left to do but hold 🔒 ===
            if both:
                cost = self.legs['UP']['fill_px'] + self.legs['DOWN']['fill_px']
                print(colored(f"\r   📦 Moon Dev - BOX LOCKED at {cost:.2f}, redeems $1.00/pair "
                              f"at expiry... {time_remaining}s left   ", "green"), end='', flush=True)
                time.sleep(BOT_POLL_INTERVAL)
                continue

            # === one leg in: work completion / T-90 bailout ===
            if n_filled == 1:
                if time_remaining <= BAILOUT_SEC and not self.bailout_done:
                    self.run_bailout(books, market_ts)
                elif not self.bailout_done:
                    self.work_completion(books, time_remaining)
                time.sleep(BOT_POLL_INTERVAL)
                continue

            # === no fills yet: static two-sided quotes, first half only ===
            if time_remaining >= QUOTE_CUTOFF_SEC:
                self.maybe_reprice(books)
                up_px, dn_px = self.legs['UP']['order_px'], self.legs['DOWN']['order_px']
                print(colored(f"\r   🪑 Moon Dev - resting UP {up_px if up_px else '..'} / "
                              f"DOWN {dn_px if dn_px else '..'} | cap {BID_SUM_CAP:.2f} | "
                              f"{time_remaining}s left   ", "cyan"), end='', flush=True)
            else:
                print(colored(f"\r   ⏳ Moon Dev - past T-{QUOTE_CUTOFF_SEC}, quotes frozen, "
                              f"waiting on fills... {time_remaining}s left   ", "white"), end='', flush=True)
            time.sleep(BOT_POLL_INTERVAL)

        # window over, belt & suspenders cancel, then the honest log row
        if not self.cancelled_all:
            self.cancel_all_resting()
        self.finalize_window()
        print_session_footer()


# ============================================================================
# 🌙 MOON DEV - MAIN ENTRY
# ============================================================================

def main():
    print(colored("""
╔══════════════════════════════════════════════════════════════════════════════╗
║   🌙 MOON DEV's BOX BUILDER v1.0 🌙                                          ║
║   Two-sided post-only bids, bid_UP + bid_DOWN <= 0.94                       ║
║   Both legs fill → box redeems $1.00/pair. The spread IS the product.       ║
╚══════════════════════════════════════════════════════════════════════════════╝
    """, "cyan", attrs=['bold']))

    print(colored("⚙️  Moon Dev - Configuration:", "yellow", attrs=['bold']))
    print(colored(f"   📄 PAPER MODE:     {PAPER_MODE} {'(flip to False for live!)' if PAPER_MODE else '🔴 LIVE FIRE'}", "yellow" if PAPER_MODE else "red"))
    print(colored(f"   🚪 Arm gate:       ask_UP + ask_DOWN >= {ARM_MIN_SPREAD_SUM:.2f} at open", "white"))
    print(colored(f"   🧢 Bid cap:        bid_UP + bid_DOWN <= {BID_SUM_CAP:.2f} (>= 6c/box target)", "white"))
    print(colored(f"   ⏳ Quote window:   T-300 → T-{QUOTE_CUTOFF_SEC} (first half ONLY, fills live early)", "white"))
    print(colored(f"   🪜 Completion:     maker to {COMPLETE_MAKER_CAP:.2f}-p1, taker lift <= {COMPLETE_TAKER_CAP:.2f}-p1", "white"))
    print(colored(f"   🛡️ Bailout:        T-{BAILOUT_SEC} coa >= {MIN_COA_HOLD:.1f} on our side → hold, else CUT", "white"))
    print(colored(f"   💵 Size:           {SHARES_PER_LEG} shares/leg flat (hard cap {HARD_CAP_SHARES} until 100 windows)", "white"))
    print(colored(f"   🚨 Kill switch:    trailing-{KILL_SWITCH_WINDOW} WR < {KILL_SWITCH_WR*100:.0f}% → pause arming", "white"))
    print(colored(f"   🔄 Poll:           every {BOT_POLL_INTERVAL}s | reprice >= {REPRICE_INTERVAL}s & only if >{REPRICE_BEHIND:.2f} behind", "white"))

    print_session_footer()
    print("")
    print_trade_tracker()

    if not PAPER_MODE:
        _build_client()  # fail fast on creds before the first window
        print(colored("   🔐 Moon Dev - CLOB V2 client ready (AUG14)", "green"))

    print(colored(f"\n{'='*70}", "green"))
    print(colored("🚀 Moon Dev - BOX BUILDER LIVE! Harvesting the early-window spread...", "green", attrs=['bold']))
    print(colored("   Press Ctrl+C to stop", "yellow"))
    print(colored(f"{'='*70}\n", "green"))

    bot = BoxBuilderBot()
    while True:
        try:
            market_ts = get_current_market_timestamp()
            time_remaining = get_time_remaining(market_ts)
            if time_remaining < QUOTE_CUTOFF_SEC + 20:
                next_ts = market_ts + MARKET_DURATION
                wait = next_ts - int(time.time())
                if wait > 0:
                    print(colored(f"\n⏰ Moon Dev - {time_remaining}s left is past the quote window, "
                                  f"waiting {wait}s for the next one...", "yellow"))
                    time.sleep(wait + 1)
                market_ts = get_current_market_timestamp()

            bot.reset()
            bot.run_market_cycle(market_ts)

        except KeyboardInterrupt:
            print(colored("\n\n🛑 Moon Dev - Box Builder stopped! Cancelling resting orders... ", "yellow", attrs=['bold']))
            if not PAPER_MODE:
                for side in ('UP', 'DOWN'):
                    tid = bot.legs[side]['token_id']
                    if tid:
                        cancel_token_orders(tid)
            print(colored("🌙 Moon Dev - Clean shutdown ✅", "yellow", attrs=['bold']))
            break


if __name__ == "__main__":
    print("🌙 Moon Dev's Box Builder - the spread is the product, let's go! 📦")
    main()
