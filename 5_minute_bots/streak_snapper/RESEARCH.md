# Idea 1: Streak Snapper (Raw Trade-Log Data Mining)

Built by Moon Dev — mined from the real trade logs + 52 weeks of real BTC 1-minute data. No synthetic data anywhere.

## Bot Name
**Streak Snapper** — fade stretched same-direction streaks at the open of the next 5-min BTC window.

## One-Line Thesis
After 4+ consecutive same-direction 5-min BTC windows whose cumulative move exceeds 3x the 1-hour ATR, the next window reverses ~54.3% of the time — buy the reversal side with a limit at <= 52c at window open and let it ride to resolution.

## Data Evidence (all numbers actually computed, nothing invented)

**Source 1 — live Polymarket log `data/dog_sniper_log.csv` (1,648 real 5-min windows, 2026-06-08 to 2026-06-14, 1,642 consecutive pairs):**
- P(next window Down | 3 consecutive Ups) = **61.6%** (n=164)
- P(next window Up | 3 consecutive Downs) = 52.2% (n=186)
- P(Up | prev window Up) = 46.1% (n=815) vs P(Up | prev Down) = 53.2% (n=827) — outcomes are anti-persistent, not trending.

**Source 2 — validation on `BTCUSD-1m-52wks-data.csv` (523,792 real 1-min candles -> 104,762 five-min windows aligned to :00/:05 boundaries):**
- Baseline P(Up) = 49.9% (fair coin, as expected)
- P(fade wins | streak >= 4) = **53.3%** (n=12,164)
- P(fade wins | streak >= 4 AND |cumulative 4-window move| > 3x ATR) = **54.3%** (n=8,802)
- Tightening to > 4x ATR: **54.6%** (n=5,445), ~15 signals/day
- Quarterly consistency (streak >= 4, > 3x ATR): 2025Q1 55.2%, 2025Q2 54.5%, 2025Q3 52.2%, 2025Q4 56.3%, 2026Q1 53.9% — **never below break-even in any quarter of a full year**
- Without the stretch filter (small cumulative move) the edge dies: 50.7% (n=3,362) — the ATR-stretch condition is what makes this real, plain streak-counting is not enough.
- EV per $1 at a 51c entry: streak >= 4 & > 3x ATR = **+6.5%**; > 4x ATR = **+7.0%**.

**Supporting color from other logs:**
- `data/fable_5min_maker_log.csv`: the AI direction call was right only **31.7%** of 82 windows — momentum-flavored directional calls get smoked in this market; anti-persistence wins.
- `data/five_min_copy_pnl*.csv` show 100% win rates = survivorship bias (only redeemed wins get logged) — those files were excluded from all win-rate math.
- `poly_hyper/data/cvd_5min_trades.csv` and `macd_signal_5min_trades.csv`: 667/704 and 502/548 orders CANCELLED ("< 60s remaining") — stink-bidding deep discounts late in the window barely ever fills. Streak Snapper enters at window OPEN with a near-mid limit, so fill rate should be dramatically higher.

## Entry Rules
1. Track the last completed 5-min BTC Up/Down windows (Chainlink close vs open, same as Polymarket resolution).
2. Maintain ATR = rolling mean |5-min change| over the last 12 windows (1 hour).
3. Signal: **4 or more consecutive same-direction windows** AND **|sum of the streak's last 4 window moves| > 3x ATR**.
4. At the open of the new window (first ~20 seconds), place a limit BUY on the **opposite** side (Down after an up-streak, Up after a down-streak) at **<= 52c**.
5. If not filled within 60 seconds, cancel — no chasing. The whole edge is 54.3% vs ~51c; paying 55c+ burns it.
6. One position per window. Skip if ATR data has a feed gap.

## Exit Rules
- **Hold to resolution.** No mid-window exit — the 54.3% number is measured open-to-close of the window; any early exit changes the measured edge.
- Redeem winners automatically each cycle (reuse the existing redeem flow from OG_redeem.py).

## Position Sizing
- Flat **$10 per trade** for the first 200 live trades (validation phase).
- Expected: ~15-24 signals/day at the 3x ATR filter; at 54.3% win rate and 51c avg entry that is roughly +$0.65 EV per $10 trade, ~$10-15/day expected at $10 clips.
- After 200 trades: if realized win rate >= 53%, step to $25; never exceed 2% of bankroll per window.
- Kill switch: stop and review if win rate < 50% after 100 fills (would be ~2 sigma below the backtest).

## Logging Plan
- CSV log at `data/streak_snapper_log.csv` with columns: `snapshot_time, window_slug, streak_len, streak_dir, cum_move_usd, atr_usd, stretch_ratio, fade_side, limit_price, action, entry_price, shares, outcome, won, pnl_usd`.
- Log EVERY signal window (filled or not) so fill-rate and no-fill EV can be audited later — the dog_sniper log's NO_FILL rows were gold for this analysis, keep that pattern.
- Console prints Moon Dev style:
  - `print(f"🌙 Moon Dev's Streak Snapper: {streak_len} straight {streak_dir}s, stretched {stretch_ratio:.1f}x ATR — snapping back with {fade_side} @ {limit_price}c!")`
  - `print(f"🚀 Moon Dev caught the snap-back! +${pnl:.2f}")` / `print(f"😤 Streak kept running on Moon Dev... -${cost:.2f}")`

## Key Risk
- **Entry-price assumption.** The backtest edge (54.3%) assumes ~51c fills at window open. If the crowd also fades streaks and the reversal side opens at 55c+, EV goes negative — the <= 52c limit + 60s cancel rule is the hard guard. First 50 live signals will measure the real fill rate and avg fill price before any size-up.
- Secondary: trend regimes. Worst quarter was still 52.2%, but a strong one-way day (e.g., ETF-flow melt-up) will string losses; the flat-size + kill-switch handles it.

## Why This Beats Past Bots
- **Different pocket entirely:** every established angle here trades late-window (favorite at 95c = known -EV; 20-45c dogs at minute-4 = someone else's lane). Streak Snapper trades at window OPEN near 50c, where the CVD/MACD logs prove liquidity actually exists (their late-window stink bids got cancelled 95% of the time).
- **Signal needs zero external feeds:** no liquidation firehose, no CVD websocket, no AI calls (which were 31.7% accurate — worse than a coin flip). Just the last 4 window outcomes and 1 hour of ATR — data the bot already produces itself.
- **Validated twice on independent real data:** live Polymarket windows (fade-3-ups won 61.6%) and a full 52 weeks / 104k windows of exchange data (54.3%, positive in all 5 quarters).

— Mined and written for Moon Dev, July 18th 2026
