#!/opt/anaconda3/envs/tflow/bin/python
"""
================================================================================
🌙 MOON DEV's LIQ CASCADE CHASER v1.0 (Fable July 18th Fleet, Bot #4)
================================================================================
Liquidation-aligned TAKER at 0.50-0.85 in minutes 0-3 of BTC 5-minute markets.

THESIS (from 187 real liq signals + 52 weeks of 1-min BTC candles):
  Liquidations were the ONLY signal with real directional edge (58.8% vs
  coin-flip CVD 51.4% / MACD 52.4%). What lost money fleet-wide was the
  30%-pullback STINK BID (34.2% wr on fills — you miss the winners and own
  the losers). Cascades continue to window close ~95% of the time
  (0.15%+ move w/ 3x volume, n=1,029), but Polymarket prices the aligned
  side at only 50-85c early in the window. Pay the taker fee, buy the
  winner WHILE it's winning.

RULES (learned from the March 2026 fleet autopsy — do not "improve" back in):
  ❌ NO stink bids. Ever. That was the whole bug.
  ❌ NO entries below 0.50 — market disagrees with the liq → it loses (-EV)
  ❌ NO entries above 0.85 — fee eats the edge + never buy the late favorite
  ❌ NO entries after minute 3 — late fills went 0-for-2
  ✅ Liq total ≥ $10k in trailing 2 min (sub-10k fills won only 24.2%)
  ✅ Window move ≥ 0.15% in liq direction + elevated tick rate (95% signature)
  ✅ LONG_LIQ → buy DOWN | SHORT_LIQ → buy UP (cascade continuation)
  ✅ Hold to resolution — 71.7-87.0% measured win rates, no scratch-outs
  ✅ Daily stop -$60 → done for the day, print the loss table

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
import math
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
MOON_DEV_API_PATH = "/Users/md/Dropbox/dev/github/moon-dev-trading-bots"

load_dotenv(os.path.join(PROJECT_ROOT, '.env'))
sys.path.insert(0, MOON_DEV_API_PATH)
from api import MoonDevAPI

# ============================================================================
# 🌙 MOON DEV - KEY PARAMS (the whole edge lives in these numbers)
# ============================================================================
PAPER_MODE = False                  # 🌙 LIVE FIRE! Flip back to True for paper testing

MIN_LIQ_USD = 10_000                # Trailing-2-min BTC liq total must be ≥ $10k
LIQ_LOOKBACK_SEC = 120              # Trailing 2 minutes of liquidations
LIQ_TIMEFRAME = "10m"               # API's smallest liq window, we filter to 2 min

PRICE_ZONE = (0.50, 0.85)           # Aligned ask band — below 0.50 market disagrees,
                                    # above 0.85 fee eats the edge. HARD limits.
MIN_MOVE_PCT = 0.15                 # Window move so far must be ≥ 0.15% in liq direction
VOLUME_MULT = 2.0                   # In-window tick rate ≥ 2x trailing-1h rate = elevated
MAX_ELAPSED = 180                   # Minutes 0-3 ONLY (elapsed seconds into window)
MIN_ELAPSED = 10                    # Need a few ticks to establish the window open
MAX_SPREAD = 0.05                   # Skip if spread > 5c — book too thin to take
FILL_WAIT_SEC = 5                   # Unfilled after 5s → cancel, skip window

BASE_SIZE_USD = 15                  # Flat $15 per trade (matches the +$57 liq bot sizing)
DAILY_STOP_USD = -60                # Down $60 on the day → shut it down, print loss table

KILL_SWITCH_WR = 0.50               # Pause entries if trailing-30 win rate < 50%
KILL_SWITCH_WINDOW = 30             # (breakeven at avg 0.65 entry is ~65% — 50% = broken)
KILL_SWITCH_MIN_TRADES = 15

BOT_POLL_INTERVAL = 5               # Cascades are fast — poll every 5s
MARKET_DURATION = 300               # btc-updown-5m markets = 300 seconds
MIN_SHARES = 5                      # Polymarket minimum order size
STATS_EVERY = 10                    # Rolling bucket stats every N resolved trades

# --- Files ---
DATA_DIR = os.path.join(BOT_DIR, "data")
SIGNAL_LOG_FILE = os.path.join(DATA_DIR, "liq_cascade_signals.csv")
TRADES_FILE = os.path.join(DATA_DIR, "liq_cascade_chaser_trades.csv")

ET = timezone(timedelta(hours=-5))

# ============================================================================
# 🌙 MOON DEV - ACCOUNT CONFIGURATION (AUG14, keeps OG free)
# ============================================================================
ACCOUNT_SUFFIX = "_AUG14"
PRIVATE_KEY_ENV_NAME = f"PRIVATE_KEY{ACCOUNT_SUFFIX}"
PUBLIC_KEY_ENV_NAME = f"PUBLIC_KEY{ACCOUNT_SUFFIX}"
SIGNATURE_TYPE = 2                  # Gnosis Safe

# ============================================================================
# 🌙 MOON DEV - MOON DEV API SETUP (liq feed + tick feed)
# ============================================================================
api = MoonDevAPI()
if not api.api_key:
    print(colored("❌ Moon Dev - MOONDEV_API_KEY not found in .env!", "red"))
    sys.exit(1)
print(colored("✅ Moon Dev - Moon Dev API loaded!", "green"))

# ============================================================================
# 🌙 MOON DEV - V2 CLOB CLIENT (taker orders — V1 post_order is dead)
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
    """🌙 Moon Dev - Marketable GTC BUY that CROSSES the ask (post_only=False).

    Why GTC and not FAK/FOK: Polymarket 400s marketable FAK/FOK orders when the
    crossable amount is < $1 — verified live. GTC crosses just the same and any
    unfilled remainder rests at our price (we cancel it after FILL_WAIT_SEC)."""
    from py_clob_client_v2.clob_types import OrderArgs, OrderType
    client = _build_client()
    order_args = OrderArgs(token_id=str(token_id), price=float(price), size=float(size), side="BUY")
    print(colored(f"   🎯 Moon Dev - TAKING the ask @ ${price:.3f} x{size} shares (GTC taker, NO stink bids ever)",
                  "cyan", attrs=['bold']))
    try:
        resp = client.create_and_post_order(order_args, order_type=OrderType.GTC, post_only=False)
        return resp or {}
    except Exception as e:
        err = str(e).lower()
        if any(t in err for t in ['timeout', 'readtimeout', 'duplicated', 'request exception']):
            print(colored("   ⚠️ Moon Dev - order timed out, will verify via positions", "yellow"))
            return {"status": "timeout"}
        print(colored(f"   ❌ Moon Dev - order failed: {type(e).__name__}: {e}", "red"))
        return {}


def cancel_token_orders(token_id):
    """🌙 Moon Dev - Cancel resting GTC remainders on a token."""
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

# ============================================================================
# 🌙 MOON DEV - LIQUIDATION SIGNAL (the only signal that graded > coin flip)
# ============================================================================


def get_btc_liquidations_2min():
    """🌙 Moon Dev - BTC liqs from last 2 minutes → (long_liqd_usd, short_liqd_usd)"""
    data = api.get_all_liquidations(LIQ_TIMEFRAME)
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


def check_liq_signal():
    """🌙 Moon Dev - Cascade signal: ≥$10k liqs one side in trailing 2 min.
    LONG_LIQ (longs getting rekt → price hammered) → buy DOWN
    SHORT_LIQ (shorts getting rekt → price ripping) → buy UP
    Returns (direction, liq_type, liq_usd, long_liq, short_liq) or Nones."""
    long_liq, short_liq = get_btc_liquidations_2min()
    print(colored(f"   💧 Liqs (2m): Long ${long_liq:,.0f} | Short ${short_liq:,.0f}", "cyan"))
    if long_liq >= MIN_LIQ_USD and long_liq > short_liq:
        return "DOWN", "LONG_LIQ", long_liq, long_liq, short_liq
    if short_liq >= MIN_LIQ_USD and short_liq > long_liq:
        return "UP", "SHORT_LIQ", short_liq, long_liq, short_liq
    return None, None, 0.0, long_liq, short_liq

# ============================================================================
# 🌙 MOON DEV - TAPE CONFIRMATION (the 95%-continuation candle signature)
# ============================================================================


def get_window_tape(market_ts):
    """🌙 Moon Dev - From the real tick feed: window move % so far + tick-rate
    multiple vs the trailing hour. Returns (move_pct, rate_mult, n_window_ticks)
    or (None, None, 0) when the feed is thin — we SKIP on no data, never fake it."""
    tick_response = api.get_ticks("BTC", "1h", limit=10000)
    if not tick_response or not isinstance(tick_response, dict):
        return None, None, 0
    all_ticks = tick_response.get('ticks', [])
    if len(all_ticks) < 20:
        return None, None, 0

    window_start_ms = market_ts * 1000
    window_ticks = [t for t in all_ticks if t.get('t', 0) >= window_start_ms]
    if len(window_ticks) < 3:
        return None, None, len(window_ticks)

    prices = [t.get('p', t.get('price', 0)) for t in window_ticks]
    open_px = prices[0]
    last_px = prices[-1]
    if not open_px:
        return None, None, len(window_ticks)
    move_pct = (last_px - open_px) / open_px * 100

    # 🌙 Moon Dev - elevated volume proxy: in-window ticks/sec vs trailing-1h ticks/sec
    elapsed = max(1, int(time.time()) - market_ts)
    window_rate = len(window_ticks) / elapsed
    hour_rate = len(all_ticks) / 3600
    rate_mult = (window_rate / hour_rate) if hour_rate > 0 else 0
    return move_pct, rate_mult, len(window_ticks)

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
    best_bid = float(bids[-1]['price'])
    best_ask = float(asks[0]['price'])
    return {'best_bid': best_bid, 'best_ask': best_ask, 'spread': best_ask - best_bid}


def taker_fee_est(price, shares):
    """🌙 Moon Dev - 5-min crypto markets taker fee ≈ 0.10 x min(p, 1-p) per share"""
    return round(0.10 * min(price, 1 - price) * shares, 2)

# ============================================================================
# 🌙 MOON DEV - LOGGING (every signal evaluation, entries AND skips)
# ============================================================================
SIGNAL_COLS = ['timestamp', 'slug', 'liq_type', 'liq_usd', 'long_liq', 'short_liq',
               'btc_move_pct', 'rate_mult', 'direction', 'ask', 'spread',
               'elapsed_s', 'size_usd', 'action']


def log_signal(slug, liq_type, liq_usd, long_liq, short_liq, move_pct, rate_mult,
               direction, ask, spread, elapsed_s, size_usd, action):
    """🌙 Moon Dev - Append one signal evaluation row (fuel for the next iteration)"""
    os.makedirs(DATA_DIR, exist_ok=True)
    row = pd.DataFrame([{
        'timestamp': datetime.now().isoformat(),
        'slug': slug,
        'liq_type': liq_type or '',
        'liq_usd': round(liq_usd, 0),
        'long_liq': round(long_liq, 0),
        'short_liq': round(short_liq, 0),
        'btc_move_pct': round(move_pct, 4) if move_pct is not None else '',
        'rate_mult': round(rate_mult, 2) if rate_mult is not None else '',
        'direction': direction or '',
        'ask': round(ask, 3) if ask is not None else '',
        'spread': round(spread, 3) if spread is not None else '',
        'elapsed_s': elapsed_s,
        'size_usd': size_usd or 0,
        'action': action,
    }], columns=SIGNAL_COLS)
    header = not os.path.exists(SIGNAL_LOG_FILE)
    row.to_csv(SIGNAL_LOG_FILE, mode='a', header=header, index=False)
    color = "green" if action == "ENTER" else "yellow"
    print(colored(f"   📝 Moon Dev - Logged: {action} ({liq_type} ${liq_usd:,.0f} → {direction})", color))


def log_trade_entry(market_ts, slug, side, liq_type, liq_usd, move_pct, ask, shares, size_usd, paper):
    """🌙 Moon Dev - Record an entry for resolution grading + kill-switch math.
    Outcome logging at resolution is MANDATORY — the March fleet never logged
    outcomes and it took candle forensics to grade it. Never again."""
    os.makedirs(DATA_DIR, exist_ok=True)
    row = pd.DataFrame([{
        'timestamp': datetime.now().isoformat(),
        'market_ts': market_ts,
        'slug': slug,
        'side': side,
        'liq_type': liq_type,
        'liq_usd': round(liq_usd, 0),
        'btc_move_pct': round(move_pct, 4),
        'entry_ask': round(ask, 3),
        'shares': shares,
        'size_usd': round(size_usd, 2),
        'fee_est': taker_fee_est(ask, shares),
        'paper': bool(paper),
        'result': 'PENDING',
        'pnl_usd': 0.0,
    }])
    header = not os.path.exists(TRADES_FILE)
    row.to_csv(TRADES_FILE, mode='a', header=header, index=False)

# ============================================================================
# 🌙 MOON DEV - RESOLUTION + DAILY STOP + KILL SWITCH
# ============================================================================


def resolve_pending_trades():
    """🌙 Moon Dev - Mark PENDING trades WIN/LOSS via gamma outcomePrices."""
    if not os.path.exists(TRADES_FILE):
        return
    df = pd.read_csv(TRADES_FILE)
    pending = df[df['result'] == 'PENDING']
    if pending.empty:
        return
    changed = False
    for idx, row in pending.iterrows():
        if time.time() < row['market_ts'] + MARKET_DURATION + 30:
            continue  # give the oracle a beat
        r = requests.get("https://gamma-api.polymarket.com/markets",
                         params={'slug': row['slug']}, timeout=10)
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
        won = row['side'] == winner
        shares = float(row['shares'])
        cost = float(row['entry_ask']) * shares + float(row.get('fee_est', 0))
        pnl = (shares - cost) if won else -cost
        df.at[idx, 'result'] = 'WIN' if won else 'LOSS'
        df.at[idx, 'pnl_usd'] = round(pnl, 2)
        changed = True
        emoji = "💰" if won else "💀"
        print(colored(f"   {emoji} Moon Dev - Resolved {row['slug']}: {row['side']} → "
                      f"{'WIN' if won else 'LOSS'} (${pnl:+.2f})", "green" if won else "red"))
    if changed:
        df.to_csv(TRADES_FILE, index=False)
        maybe_print_bucket_stats(df)


def todays_pnl():
    """🌙 Moon Dev - Realized PnL since midnight ET (resolved trades only)."""
    if not os.path.exists(TRADES_FILE):
        return 0.0
    df = pd.read_csv(TRADES_FILE)
    resolved = df[df['result'].isin(['WIN', 'LOSS'])].copy()
    if resolved.empty:
        return 0.0
    resolved['ts'] = pd.to_datetime(resolved['timestamp'])
    today_et = datetime.now(ET).date()
    todays = resolved[resolved['ts'].dt.date == today_et]
    return float(todays['pnl_usd'].sum())


def daily_stop_hit():
    """🌙 Moon Dev - Down $60 on the day → SHUT IT DOWN + print the loss table
    so Moon Dev sees exactly what happened."""
    pnl = todays_pnl()
    if pnl > DAILY_STOP_USD:
        return False
    print(colored(f"\n   🛑 Moon Dev - DAILY STOP HIT! Today's PnL ${pnl:+.2f} ≤ ${DAILY_STOP_USD} — done for the day.",
                  "red", attrs=['bold']))
    df = pd.read_csv(TRADES_FILE)
    df['ts'] = pd.to_datetime(df['timestamp'])
    todays = df[(df['ts'].dt.date == datetime.now(ET).date()) & df['result'].isin(['WIN', 'LOSS'])]
    print(colored("   📋 Moon Dev - Today's loss table:", "red"))
    for _, t in todays.iterrows():
        print(colored(f"      {t['ts'].strftime('%H:%M')} | {t['side']:<4} @ {t['entry_ask']:.2f} | "
                      f"{t['liq_type']} ${t['liq_usd']:,.0f} | {t['result']} ${t['pnl_usd']:+.2f}",
                      "green" if t['result'] == 'WIN' else "red"))
    return True


def kill_switch_active():
    """🌙 Moon Dev - True = PAUSE. Trailing-30 resolved win rate < 50%
    (breakeven at avg 0.65 entry is ~65% — 50% means the edge is broken)."""
    if not os.path.exists(TRADES_FILE):
        return False
    df = pd.read_csv(TRADES_FILE)
    resolved = df[df['result'].isin(['WIN', 'LOSS'])].tail(KILL_SWITCH_WINDOW)
    if len(resolved) < KILL_SWITCH_MIN_TRADES:
        return False
    wr = (resolved['result'] == 'WIN').mean()
    if wr < KILL_SWITCH_WR:
        print(colored(f"   🚨 Moon Dev - KILL SWITCH! Trailing-{len(resolved)} win rate "
                      f"{wr*100:.1f}% < {KILL_SWITCH_WR*100:.0f}% — entries PAUSED", "red", attrs=['bold']))
        return True
    return False


def maybe_print_bucket_stats(df):
    """🌙 Moon Dev - Every 10 resolved trades: win rate by entry-price bucket and
    by liq size, so edge decay is visible IMMEDIATELY (not after candle forensics)."""
    resolved = df[df['result'].isin(['WIN', 'LOSS'])].copy()
    if resolved.empty or len(resolved) % STATS_EVERY != 0:
        return
    print(colored(f"\n   📊 Moon Dev - ROLLING BUCKET STATS ({len(resolved)} resolved) 📊", "magenta", attrs=['bold']))
    resolved['win'] = resolved['result'] == 'WIN'
    price_buckets = [(0.50, 0.60), (0.60, 0.70), (0.70, 0.85)]
    for lo, hi in price_buckets:
        b = resolved[(resolved['entry_ask'] >= lo) & (resolved['entry_ask'] < hi)]
        if len(b):
            print(colored(f"      entry {lo:.2f}-{hi:.2f}: n={len(b)} | wr {b['win'].mean()*100:.1f}% | "
                          f"pnl ${b['pnl_usd'].sum():+.2f}", "white"))
    liq_buckets = [(10_000, 25_000), (25_000, 50_000), (50_000, float('inf'))]
    for lo, hi in liq_buckets:
        b = resolved[(resolved['liq_usd'] >= lo) & (resolved['liq_usd'] < hi)]
        if len(b):
            label = f"${lo/1000:.0f}k-{'∞' if hi == float('inf') else f'${hi/1000:.0f}k'}"
            print(colored(f"      liq {label}: n={len(b)} | wr {b['win'].mean()*100:.1f}% | "
                          f"pnl ${b['pnl_usd'].sum():+.2f}", "white"))


def print_trade_tracker():
    """🌙 Moon Dev - Clickable tracker of past trades, printed FIRST every cycle."""
    if not os.path.exists(TRADES_FILE):
        return
    df = pd.read_csv(TRADES_FILE)
    if df.empty:
        return
    print(colored(f"============ 🌙 MOON DEV'S TRADE TRACKER ({len(df)} trades) 🌙 ============", "cyan", attrs=['bold']))
    for _, t in df.tail(20).iterrows():
        ts = pd.to_datetime(t['timestamp']).strftime('%m-%d %H:%M')
        if t['result'] == 'WIN':
            status, color = f"WIN  {float(t['pnl_usd']):+.2f}".replace("+", "+$"), "green"
        elif t['result'] == 'LOSS':
            status, color = f"LOSS {float(t['pnl_usd']):+.2f}".replace("-", "-$"), "red"
        else:
            status, color = "PENDING", "white"
        print(colored(f"{ts} | {t['side']:<4} @ {float(t['entry_ask']):.2f} | {t['liq_type']:<9} | {status:<12} | "
                      f"https://polymarket.com/event/{t['slug']}", color))
    print(colored("=" * 71, "cyan", attrs=['bold']))


def print_session_summary():
    """🌙 Moon Dev - Quick PnL + win rate readout"""
    if not os.path.exists(TRADES_FILE):
        print(colored("   📋 Moon Dev - No trade history yet (first run)", "yellow"))
        return
    df = pd.read_csv(TRADES_FILE)
    resolved = df[df['result'].isin(['WIN', 'LOSS'])]
    wins = (resolved['result'] == 'WIN').sum()
    print(colored(f"\n📋 Moon Dev - LIQ CASCADE CHASER HISTORY", "cyan", attrs=['bold']))
    print(colored(f"   Entries: {len(df)} | Resolved: {len(resolved)} | Wins: {wins} | "
                  f"WR: {(wins/len(resolved)*100) if len(resolved) else 0:.1f}% | "
                  f"PnL: ${resolved['pnl_usd'].sum():+.2f} | Today: ${todays_pnl():+.2f}", "white"))

# ============================================================================
# 🌙 MOON DEV - MAIN BOT
# ============================================================================


class LiqCascadeChaser:
    def __init__(self):
        self.reset()

    def reset(self):
        self.market_info = None
        self.window_done = False        # entered OR cancelled-no-fill → done for window
        self.entry_token_id = None
        self.entry_side = None
        self.entry_shares = 0
        self.entry_ask = 0

    def try_enter(self, market_ts, direction, liq_type, liq_usd, long_liq, short_liq, elapsed):
        """🌙 Moon Dev - Run the gate gauntlet, log the verdict, maybe chase the cascade"""
        slug = self.market_info['slug']
        token_id = self.market_info['up_token_id'] if direction == "UP" else self.market_info['down_token_id']

        # --- GATE 1: tape confirmation (0.15% move in liq direction + elevated rate) ---
        move_pct, rate_mult, n_ticks = get_window_tape(market_ts)
        if move_pct is None:
            print(colored(f"   📡 Moon Dev - Tick feed too thin ({n_ticks} window ticks) — no data, no trade!", "yellow"))
            log_signal(slug, liq_type, liq_usd, long_liq, short_liq, None, None,
                       direction, None, None, elapsed, 0, "SKIP_NO_TAPE")
            return
        aligned_move = move_pct if direction == "UP" else -move_pct
        print(colored(f"   📊 Tape: window move {move_pct:+.3f}% | rate {rate_mult:.1f}x hourly | "
                      f"need ≥ {MIN_MOVE_PCT}% aligned + ≥ {VOLUME_MULT}x", "cyan"))
        if aligned_move < MIN_MOVE_PCT or rate_mult < VOLUME_MULT:
            log_signal(slug, liq_type, liq_usd, long_liq, short_liq, move_pct, rate_mult,
                       direction, None, None, elapsed, 0, "SKIP_TAPE")
            return

        # --- GATE 2: price zone 0.50-0.85 on the ACTUAL ask ---
        book = get_order_book(token_id)
        ask = book['best_ask'] if book else None
        spread = book['spread'] if book else None
        if ask is None or ask < PRICE_ZONE[0] or ask > PRICE_ZONE[1]:
            log_signal(slug, liq_type, liq_usd, long_liq, short_liq, move_pct, rate_mult,
                       direction, ask, spread, elapsed, 0, "SKIP_PRICE")
            if ask is not None:
                reason = "market DISAGREES with the liq — it loses" if ask < PRICE_ZONE[0] \
                    else "edge gone after fees + never buy the late favorite"
                print(colored(f"   🙅 Moon Dev - Ask ${ask:.2f} outside {PRICE_ZONE[0]:.2f}-{PRICE_ZONE[1]:.2f} ({reason})", "yellow"))
            return

        # --- GATE 3: spread ---
        if spread > MAX_SPREAD:
            log_signal(slug, liq_type, liq_usd, long_liq, short_liq, move_pct, rate_mult,
                       direction, ask, spread, elapsed, 0, "SKIP_SPREAD")
            return

        # --- ALL GATES PASSED → CHASE THE CASCADE 🚀 ---
        shares = max(MIN_SHARES, math.floor(BASE_SIZE_USD / ask))
        fee = taker_fee_est(ask, shares)
        print(colored(f"\n   🌊🌊🌊 MOON DEV CASCADE: {liq_type} ${liq_usd:,.0f} | BTC {move_pct:+.3f}% into window "
                      f"→ BUY {direction} @ ${ask:.3f} x{shares} (${shares*ask:.2f} + ~${fee:.2f} fee) 🌊🌊🌊",
                      "green" if direction == "UP" else "red", attrs=['bold']))
        print(colored(f"   🔗 https://polymarket.com/event/{slug}", "cyan"))

        if PAPER_MODE:
            print(colored(f"   📄 Moon Dev - PAPER MODE: simulated fill @ ${ask:.3f}", "yellow", attrs=['bold']))
            filled = True
        else:
            resp = place_taker_buy(token_id, ask, shares)
            if not resp:
                log_signal(slug, liq_type, liq_usd, long_liq, short_liq, move_pct, rate_mult,
                           direction, ask, spread, elapsed, 0, "ORDER_FAILED")
                return
            # 🌙 Moon Dev - the 5-second rule: unfilled = book moved through us → cancel & skip
            time.sleep(FILL_WAIT_SEC)
            held = get_position_size(token_id)
            if held <= 0:
                print(colored(f"   ⏱️ Moon Dev - No fill after {FILL_WAIT_SEC}s (book moved) — cancelling, skipping window", "yellow"))
                cancel_token_orders(token_id)
                log_signal(slug, liq_type, liq_usd, long_liq, short_liq, move_pct, rate_mult,
                           direction, ask, spread, elapsed, 0, "NO_FILL")
                self.window_done = True
                return
            filled = True

        self.window_done = True
        self.entry_token_id = token_id
        self.entry_side = direction
        self.entry_shares = shares
        self.entry_ask = ask
        log_signal(slug, liq_type, liq_usd, long_liq, short_liq, move_pct, rate_mult,
                   direction, ask, spread, elapsed, round(shares * ask, 2), "ENTER")
        log_trade_entry(market_ts, slug, direction, liq_type, liq_usd, move_pct, ask, shares,
                        shares * ask, PAPER_MODE)
        wr_note = "71.7%" if ask < 0.70 else "87.0%"
        print(colored(f"   💰 Moon Dev riding the cascade — measured pocket wr {wr_note}, holding to resolution!", "green"))

    def run_market_cycle(self, market_ts):
        """🌙 Moon Dev - One full 5-minute market window"""
        market_et = datetime.fromtimestamp(market_ts, tz=ET)
        print("")
        print_trade_tracker()  # 🌙 Moon Dev - tracker FIRST, clickable trade links up top
        print(colored(f"\n{'='*70}", "cyan"))
        print(colored(f"🌙 Moon Dev - LIQ CASCADE CYCLE | {market_et.strftime('%I:%M:%S%p ET')}", "cyan", attrs=['bold']))
        print(colored(f"{'='*70}", "cyan"))

        resolve_pending_trades()
        if daily_stop_hit():
            # 🌙 Moon Dev - sleep out the rest of the ET day, checking hourly
            print(colored("   😴 Moon Dev - Sleeping until tomorrow ET...", "yellow"))
            time.sleep(3600)
            return
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

        print(colored(f"   🔗 https://polymarket.com/event/{self.market_info['slug']}", "cyan"))

        while True:
            time_remaining = get_time_remaining(market_ts)
            elapsed = MARKET_DURATION - time_remaining

            if time_remaining <= 0:
                print(colored("\n   ⏰ Moon Dev - Window closed!", "yellow"))
                if self.entry_token_id and not PAPER_MODE:
                    cancel_token_orders(self.entry_token_id)  # clean GTC remainder
                if self.entry_side:
                    print(colored(f"   🎲 Moon Dev - Holding {self.entry_side} to resolution "
                                  f"(no scratch-outs — exiting into cascade chop donates spread)", "white"))
                break

            if self.window_done:
                if self.entry_side:
                    mins, secs = divmod(time_remaining, 60)
                    emoji = "🟢" if self.entry_side == "UP" else "🔴"
                    print(colored(f"\r   {emoji} Moon Dev - Riding {self.entry_side} @ ${self.entry_ask:.3f} "
                                  f"x{self.entry_shares} to resolution... {mins}:{secs:02d} left    ",
                                  "white"), end='', flush=True)
                time.sleep(5)
                continue

            # 🌙 Moon Dev - minutes 0-3 ONLY (late fills went 0-for-2 in the logs)
            if elapsed > MAX_ELAPSED:
                print(colored(f"   ⏳ Moon Dev - Past minute 3 ({elapsed}s in) — watching only, no late entries", "white"))
                time.sleep(time_remaining if time_remaining < BOT_POLL_INTERVAL else BOT_POLL_INTERVAL)
                continue
            if elapsed < MIN_ELAPSED:
                time.sleep(MIN_ELAPSED - elapsed)
                continue

            if paused:
                print(colored(f"   🚨 Kill switch active — watching, not trading ({time_remaining}s left)", "red"))
                time.sleep(BOT_POLL_INTERVAL)
                continue

            print(colored(f"\n   📡 Moon Dev - Scanning for cascades... (minute {elapsed//60}, {time_remaining}s left)", "yellow"))
            direction, liq_type, liq_usd, long_liq, short_liq = check_liq_signal()

            if direction:
                self.try_enter(market_ts, direction, liq_type, liq_usd, long_liq, short_liq, elapsed)
            else:
                print(colored("   😴 No qualifying liq cascade. Waiting...", "white"))

            time.sleep(BOT_POLL_INTERVAL)


# ============================================================================
# 🌙 MOON DEV - MAIN ENTRY
# ============================================================================


def main():
    print(colored("""
╔══════════════════════════════════════════════════════════════════════════════╗
║   🌙 MOON DEV's LIQ CASCADE CHASER v1.0 🌙                                  ║
║   ≥$10k liq + 0.15% aligned move → TAKER at 0.50-0.85, minutes 0-3         ║
║   The only signal that graded 58.8% | NO stink bids EVER | hold to close   ║
╚══════════════════════════════════════════════════════════════════════════════╝
    """, "cyan", attrs=['bold']))

    print(colored("⚙️  Moon Dev - Configuration:", "yellow", attrs=['bold']))
    print(colored(f"   📄 PAPER MODE:     {PAPER_MODE} {'(flip to False for live!)' if PAPER_MODE else '🔴 LIVE FIRE'}", "yellow" if PAPER_MODE else "red"))
    print(colored(f"   💧 Liq trigger:    ≥ ${MIN_LIQ_USD:,} one-sided in trailing {LIQ_LOOKBACK_SEC}s", "white"))
    print(colored(f"   📊 Tape confirm:   ≥ {MIN_MOVE_PCT}% aligned window move + ≥ {VOLUME_MULT}x tick rate", "white"))
    print(colored(f"   💰 Price zone:     {PRICE_ZONE[0]:.2f}-{PRICE_ZONE[1]:.2f} on the actual ask (below = market disagrees, above = fee-dead)", "white"))
    print(colored(f"   ⏳ Entry window:   minutes 0-3 only ({MIN_ELAPSED}-{MAX_ELAPSED}s elapsed)", "white"))
    print(colored(f"   ⏱️  Fill rule:      unfilled after {FILL_WAIT_SEC}s → cancel + skip window", "white"))
    print(colored(f"   💵 Size:           ${BASE_SIZE_USD} flat | Daily stop ${DAILY_STOP_USD}", "white"))
    print(colored(f"   🚨 Kill switch:    trailing-{KILL_SWITCH_WINDOW} WR < {KILL_SWITCH_WR*100:.0f}% → pause", "white"))
    print(colored(f"   🔄 Poll:           every {BOT_POLL_INTERVAL}s (cascades are fast)", "white"))

    print_session_summary()
    print("")
    print_trade_tracker()  # 🌙 Moon Dev - seed the tracker display from the trades CSV

    # 🌙 Moon Dev - Smoke-test the liq + tick feeds before going live (no data = no bot)
    print(colored("\n📡 Moon Dev - Testing feeds...", "yellow", attrs=['bold']))
    long_liq, short_liq = get_btc_liquidations_2min()
    print(colored(f"   BTC Liqs (2m): Long ${long_liq:,.0f} | Short ${short_liq:,.0f}", "cyan"))
    tick_response = api.get_ticks("BTC", "1h", limit=10000)
    all_ticks = tick_response.get('ticks', []) if isinstance(tick_response, dict) else []
    if len(all_ticks) < 10:
        print(colored("❌ Moon Dev - Tick feed is dead — refusing to run on no data!", "red"))
        sys.exit(1)
    print(colored(f"   BTC ticks (1h): {len(all_ticks)} — feed healthy ✅", "cyan"))

    print(colored(f"\n{'='*70}", "green"))
    print(colored("🚀 Moon Dev - LIQ CASCADE CHASER LIVE! Buying winners WHILE they're winning...", "green", attrs=['bold']))
    print(colored("   Press Ctrl+C to stop", "yellow"))
    print(colored(f"{'='*70}\n", "green"))

    bot = LiqCascadeChaser()
    while True:
        try:
            market_ts = get_current_market_timestamp()
            bot.reset()
            bot.run_market_cycle(market_ts)

        except KeyboardInterrupt:
            print(colored("\n\n🛑 Moon Dev - Liq Cascade Chaser stopped! Clean shutdown ✅", "yellow", attrs=['bold']))
            if bot.entry_token_id and not PAPER_MODE:
                cancel_token_orders(bot.entry_token_id)
            break


if __name__ == "__main__":
    print("🌙 Moon Dev's Liq Cascade Chaser - chase the cascade, skip the stink! 🌊")
    main()
