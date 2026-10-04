"""Server side of alerts.

AlertService owns local/buffer/alerts.json: the ("ALRT", "watch") job, rule edits and acks —
every read-modify-write under one lock. After each change it publishes an SSE `alerts` event
{type, active, down, new} and refreshes the ALRT screen's payload when that screen is live.
The job marks the STORED PORT quote part's positions (shares) to the quotes it just fetched, with
PORT's own valuation (watch.port_view), so PORT DAY / PORT WEIGHT follow the market whichever
screen is open; drift comes from the STORED OPT payload. Nothing heavy is recomputed.

AlertLoop submits that job every ALERT_INTERVAL_S while at least one terminal tab holds the
event stream open; with no tab connected nothing runs. The JobRunner dedupes a job still queued
or running, records failures (status bar ERR ALRT) and never lets one kill the loop.
"""
from __future__ import annotations

import asyncio
import logging
import threading
import time
from datetime import datetime
from pathlib import Path

from monitor import config
from monitor.alerts import rules as R
from monitor.alerts import store as astore
from monitor.alerts import watch, watchlist
from monitor.data.instruments import BOARD_TICKERS
from monitor.portfolio import snapshot
from monitor.server.engine import _NET_LOCK, Engine
from monitor.universe import lookup

log = logging.getLogger("monitor.alerting")


class AlertService:
    def __init__(self, engine: Engine, path: Path):
        self.engine, self.path = engine, Path(path)
        self._lock = threading.Lock()
        self._last_run: datetime | None = None          # the previous check; a gap = no tab was open
        self._since: datetime | None = None             # when the current run of checks began
        if not self.path.exists():                      # first start: the default rules, stored and editable
            astore.save(self.path, astore.defaults())
        engine.tasks[("ALRT", "watch")] = self.run

    # ── inputs from the store (no recompute) ─────────────────────────────────
    def _held(self) -> list[str]:
        try:
            return sorted(snapshot.load_book(self.engine.ctx.portfolio_csv)["holdings"])
        except Exception:                                # no ledger yet: alerts still watch the watchlist
            return []

    @staticmethod
    def _safe(fn, *args):
        """A malformed stored PORT / OPT payload means 'unknown': the PORT rules wait, the rest run."""
        try:
            return fn(*args)
        except Exception as e:
            log.warning("alerts: ignoring unreadable stored input (%s: %s)", fn.__name__, e)
            return None

    def _port(self, quotes: dict, held: list[str]) -> dict | None:
        """PORT DAY / WEIGHT inputs: the stored PORT part's positions marked to this check's quotes
        (its stored day_pct may be hours old — PORT refreshes only while on screen). No part → None."""
        rec = self.engine.store.get_part("PORT", "quote")
        return None if rec is None else watch.port_view(rec["data"]["positions"], quotes, held)

    def _drift(self) -> dict | None:
        opt = self.engine.store.get_payload("OPT")
        target = next((p for p in (opt or {}).get("panels", []) if p.get("id") == "target"), None)
        items = {i.get("k"): i.get("v") for i in (target or {}).get("items", [])}
        return {"value": items["DRIFT"], "target": items.get("TARGET")} if items.get("DRIFT") is not None else None

    # ── the job ──────────────────────────────────────────────────────────────
    def run(self, force: bool = True) -> list[dict]:
        """One alert check: quotes (forced) → evaluate → persist → publish. Returns new entries."""
        with self._lock:
            rules = astore.load(self.path)["rules"]
        held, watched = self._held(), watchlist.tickers(self.engine.ctx.watchlist)
        drift = self._safe(self._drift)
        now = datetime.now()
        with self._lock:
            if self._last_run is None or (now - self._last_run).total_seconds() > 3 * config.ALERT_INTERVAL_S:
                self._since = now                        # first check, or the first after no tab was open
            self._last_run, since = now, self._since
        with _NET_LOCK:                                  # yfinance is not thread-safe (see engine._NET_LOCK)
            snap = watch.gather(rules, held=held, watched=watched, port=None, drift=drift,
                                buffer_dir=self.engine.ctx.buffer_dir, since=since)
        snap["port"] = self._safe(self._port, snap["quotes"], held)
        with self._lock:
            data = astore.load(self.path)
            state, events = R.evaluate(data["rules"], data["state"], snap, datetime.now())
            data, new = astore.record(data, state, events)
            astore.save(self.path, data)
            self._changed(data, new)
        return new

    # ── edits from the API ───────────────────────────────────────────────────
    def view(self) -> dict:
        with self._lock:
            return astore.view(astore.load(self.path))

    def _known(self, t: str) -> bool:
        return (t == "*" or t in BOARD_TICKERS or t in self._held()
                or t in watchlist.tickers(self.engine.ctx.watchlist) or lookup.is_tradeable(t))

    def add(self, text: str) -> dict:
        rule = R.parse_rule(text)                                     # ValueError(usage)
        if rule["kind"] in watch.PRICE_KINDS and not self._known(rule["ticker"]):
            raise ValueError(f"{rule['ticker']}: NOT A TRADEABLE TICKER")
        with self._lock:
            data, r = astore.add_rule(astore.load(self.path), rule)
            astore.save(self.path, data)
            self._changed(data, [])
        # rerun: a check already running (even the loop's forced one) loaded its rules before this add
        self.engine.runner.submit("ALRT", "watch", force=True, rerun=True)   # check the new rule now
        return {**r, "text": R.describe(r)}

    def remove(self, rid: str) -> None:
        with self._lock:
            data = astore.remove_rule(astore.load(self.path), rid)   # KeyError if unknown
            astore.save(self.path, data)
            self._changed(data, [])

    def ack(self, key: str) -> int:
        with self._lock:
            data, n = astore.ack(astore.load(self.path), key)
            if n:
                astore.save(self.path, data)
                self._changed(data, [])
        return n

    def _changed(self, data: dict, new: list[dict]) -> None:
        """Publish a change already saved: a failure here is logged, never raised — the edit or the
        check did happen, so the API must not report it failed nor the job turn ERR ALRT."""
        try:
            act = astore.active(data)
            self.engine.broker.publish({"type": "alerts", "active": len(act),
                                        "down": any(e.get("down") for e in act), "new": new})
            scr = self.engine.screens.get("ALRT")
            if scr is not None and scr.status == "live":
                self.engine.put_and_assemble("ALRT", "quote", data)
        except Exception as e:
            log.warning("alerts: saved, but publishing the change failed (%s: %s)", type(e).__name__, e)


class AlertLoop:
    """tick(): submit one ("ALRT", "watch") job if a tab is connected and the interval has passed."""

    def __init__(self, engine: Engine, interval: float = config.ALERT_INTERVAL_S, clock=time.monotonic):
        self.engine, self.interval, self.clock = engine, interval, clock
        self.last: float | None = None

    def tick(self) -> bool:
        if not getattr(self.engine.broker, "subscribers", 0):
            return False
        now = self.clock()
        if self.last is not None and now - self.last < self.interval:
            return False
        self.last = now
        self.engine.runner.submit("ALRT", "watch")
        return True

    async def run(self, poll_s: float = 1.0) -> None:
        while True:
            try:
                self.tick()
            except Exception:                            # never let one tick end the loop
                log.exception("alert loop tick failed")
            await asyncio.sleep(poll_s)
