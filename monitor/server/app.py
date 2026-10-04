"""HTTP surface: JSON API, SSE stream and the SPA.
Bound to 127.0.0.1 by monitor.server.run; never exposed beyond localhost.
Every refusal is {"detail": {"error": <one short uppercase line, as the terminal shows it>, ...data}}."""
import asyncio
import logging
from contextlib import asynccontextmanager, suppress
from pathlib import Path

from starlette.middleware.gzip import GZipMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware
from fastapi import Body, FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from monitor import config
from monitor.alerts import watchlist
from monitor.data.buffer import cached_quotes
from monitor.portfolio.tradebook import Blocked, Conflict, Invalid, Missing, TradeBook
from monitor.server.alerting import AlertLoop, AlertService
from monitor.server.engine import Engine
from monitor.server.schedule import BuildSchedule
from monitor.server.stream import sse_stream
from monitor.universe import lookup

log = logging.getLogger("monitor.server")

class _GzipExceptStream:
    """gzip every response except the SSE stream — compressing it would buffer live events."""

    def __init__(self, app, minimum_size: int = 1024):
        self.app = app
        self.gz = GZipMiddleware(app, minimum_size=minimum_size)

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and scope.get("path") == "/api/stream":
            await self.app(scope, receive, send)
        else:
            await self.gz(scope, receive, send)


def _own_origins(host: str) -> set[str]:
    """This terminal's origins for a request whose Host header is `host` (already allow-listed to localhost /
    127.0.0.1): the same port under either name."""
    port = host.rsplit(":", 1)[1] if ":" in host else ""
    return {f"http://{name}{':' + port if port else ''}" for name in ("localhost", "127.0.0.1")}


class _SameOriginWrites:
    """CSRF guard: a state-changing request (POST / PUT / PATCH / DELETE) that carries an Origin header must
    come from this terminal's own page — http://localhost:<port> or http://127.0.0.1:<port>, the port its
    Host names — else 403. A page on another site (or another local port) cannot make the browser BUILD,
    REFRESH, WATCH, ALERT or set prefs. No Origin (curl, scripts) passes; reads pass; the Host allow-list
    already stops DNS rebinding."""
    WRITES = frozenset({"POST", "PUT", "PATCH", "DELETE"})

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and scope["method"] in self.WRITES:
            hdr = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers", [])}
            origin = hdr.get("origin")
            if origin is not None and origin not in _own_origins(hdr.get("host", "")):
                await JSONResponse({"detail": {"error": "CROSS-ORIGIN WRITE REFUSED", "origin": origin}},
                                   status_code=403)(scope, receive, send)
                return
        await self.app(scope, receive, send)


def quote_check(ticker: str, buffer_dir: Path | None = None) -> dict | None:
    """TRADES' one quote for a ticker new to your file, through the quote buffer (a ticker PORT already quoted
    costs no network): {price, …, ccy?} or None when Yahoo has none."""
    quotes, _, _ = cached_quotes([ticker], buffer_dir=buffer_dir)
    return quotes.get(ticker)


def terminal_book(engine: Engine) -> TradeBook:
    """The terminal's TradeBook over the engine's input/portfolio.csv: Yahoo for new tickers, the universe for
    ISINs."""
    return TradeBook(engine.ctx.portfolio_csv, example=config.EXAMPLES_DIR / "portfolio.example.csv",
                     quote=lambda t: quote_check(t, engine.ctx.buffer_dir), isin=lookup.by_isin)


def create_app(engine: Engine | None = None, *, web_dir: Path = config.WEB_DIR,
               schedule_file: Path | None = config.SCHEDULE_FILE, trade_book: TradeBook | None = None) -> FastAPI:
    engine = engine or Engine.default()
    alerts = AlertService(engine, engine.ctx.alerts) if engine.ctx.alerts is not None else None
    book = trade_book or terminal_book(engine)

    @asynccontextmanager
    async def lifespan(_app):
        # the alert loop lives exactly as long as the server: cancelled on shutdown / reload
        try:
            engine.prune_params()           # housekeeping: never blocks the server from starting
        except Exception:
            log.warning("pruning stored parameter screens failed", exc_info=True)
        task = asyncio.create_task(AlertLoop(engine).run()) if alerts is not None else None
        _app.state.alert_task = task
        monthly = any(s.monthly for s in engine.screens.values())      # rebuilt at each month's start
        sched = asyncio.create_task(BuildSchedule(engine, schedule_file).run()) if monthly else None
        _app.state.build_task = sched
        try:
            yield
        finally:
            for t in (task, sched):
                if t is not None:
                    t.cancel()
                    with suppress(asyncio.CancelledError):
                        await t
            engine.shutdown()                            # workers + any build child

    app = FastAPI(title="Investment Monitor", docs_url=None, redoc_url=None, openapi_url=None,
                  lifespan=lifespan)
    app.state.engine = engine
    app.state.alerts = alerts
    # DNS-rebinding guard: a hostile page rebinding to 127.0.0.1 must not read private payloads.
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost"])
    app.add_middleware(_SameOriginWrites)                  # CSRF: writes only from this terminal's own page
    app.add_middleware(_GzipExceptStream)

    @app.exception_handler(RequestValidationError)
    async def malformed(_request, _exc):                  # a body that is not the JSON a route expects
        return JSONResponse({"detail": {"error": "MALFORMED REQUEST"}}, status_code=422)

    def live_screen(sid: str):
        scr = engine.screens.get(sid.upper())
        if scr is None:
            raise HTTPException(404, {"error": f"UNKNOWN SCREEN {sid.upper()}", "id": sid.upper()})
        if scr.status != "live":
            raise HTTPException(404, {"error": f"{scr.id} — SOON", "id": scr.id})
        return scr

    def resolve(sid: str, param: str | None):
        scr = live_screen(sid)
        if scr.params is None:
            if param is not None:
                raise HTTPException(404, {"error": f"{scr.id} TAKES NO PARAMETER", "id": scr.id, "param": param})
            return scr, None
        if param is None:
            raise HTTPException(404, {"error": f"{scr.id}: {scr.param_name} REQUIRED", "id": scr.id})
        p = param.upper()
        if not engine.accepts(scr.id, p):
            raise HTTPException(404, {"error": f"{p}: {scr.unknown_param}", "id": scr.id, "param": p})
        return scr, p

    @app.get("/api/screens")
    def screens():
        params = {sid: engine.params(sid) for sid, s in engine.screens.items()
                  if s.status == "live" and s.params is not None}
        return {"screens": [s.entry() for s in engine.screens.values()],
                "quote_interval_s": config.QUOTE_INTERVAL_S,
                "params": params, "targets": list(config.PORTFOLIOS),
                "settings_error": config.SETTINGS_ERROR}

    @app.get("/api/screen/{sid}")
    @app.get("/api/screen/{sid}/{param}")
    def screen(sid: str, param: str | None = None):
        scr, p = resolve(sid, param)
        if reason := engine.cold_reason(scr.id):      # e.g. no portfolio yet: say how to start
            return JSONResponse({"screen": scr.id, "param": p, "cold": True, "reason": reason,
                                 "live": engine.live(scr.id, p)}, status_code=202)
        engine.ensure_fresh(scr.id, p)
        payload = engine.payload(scr.id, p)
        if payload is None:
            return JSONResponse({"screen": scr.id, "param": p, "cold": True, "live": engine.live(scr.id, p)},
                                status_code=202)
        return {**payload, "live": engine.live(scr.id, p)}

    @app.post("/api/ensure/{sid}")
    @app.post("/api/ensure/{sid}/{param}")
    def ensure(sid: str, param: str | None = None):
        scr, p = resolve(sid, param)
        jobs = engine.ensure_fresh(scr.id, p)
        return {"jobs": [j.as_dict() for j in jobs], "live": engine.live(scr.id, p)}

    @app.post("/api/refresh/{sid}")
    @app.post("/api/refresh/{sid}/{param}")
    def refresh(sid: str, param: str | None = None, tier: str | None = None):
        scr, p = resolve(sid, param)
        if tier is not None and tier not in scr.tiers:
            raise HTTPException(400, {"error": f"{scr.id} HAS NO TIER '{tier.upper()}' — "
                                               + " · ".join(t.upper() for t in scr.tiers), "tiers": list(scr.tiers)})
        return {"jobs": [j.as_dict() for j in engine.refresh(scr.id, [tier] if tier else None, param=p)]}

    @app.post("/api/build/{sid}")
    def build(sid: str):
        """BUILD <screen>: run the screen's build child now (REFRESH only reloads its artifact)."""
        scr = live_screen(sid)
        if not scr.build_cmd:
            builds = [s.id for s in engine.screens.values() if s.build_cmd]
            raise HTTPException(400, {"error": f"{scr.id} HAS NOTHING TO BUILD"
                                               + (" — " + " · ".join(f"BUILD {b}" for b in builds) if builds else ""),
                                      "builds": builds})
        return {"jobs": [engine.build(scr.id).as_dict()], "eta": scr.build_eta}

    @app.get("/api/prefs")
    def get_prefs():
        return engine.prefs

    @app.post("/api/prefs")
    def set_prefs(body: dict = Body(...)):
        try:
            return engine.set_target(body.get("target", ""))
        except ValueError:
            name = str(body.get("target") or "?").upper()
            raise HTTPException(400, {"error": f"UNKNOWN TARGET {name} — TARGET <{'|'.join(config.PORTFOLIOS)}>",
                                      "targets": list(config.PORTFOLIOS)})

    @app.get("/api/lookup")
    def lookup_route(q: str = ""):
        return lookup.search(q)

    @app.get("/api/watchlist")
    def get_watchlist():
        return watchlist.load(engine.ctx.watchlist)

    @app.post("/api/watchlist")
    def add_watch(body: dict = Body(...)):
        t = str(body.get("ticker") or "").strip().upper()
        hit = lookup.info(t)
        if hit is None:
            raise HTTPException(400, {"error": f"{t or '?'}: NOT A TRADEABLE TICKER"})
        fresh = t not in watchlist.tickers(engine.ctx.watchlist)
        items = watchlist.add(engine.ctx.watchlist, t, hit["name"])
        if fresh:                                       # re-watching changes nothing: no forced MKT recompute
            engine.watchlist_changed()
        return items

    @app.delete("/api/watchlist/{ticker}")
    def del_watch(ticker: str):
        t = ticker.strip().upper()
        if t not in watchlist.tickers(engine.ctx.watchlist):
            raise HTTPException(404, {"error": f"{t}: NOT WATCHED"})
        items = watchlist.remove(engine.ctx.watchlist, t)
        engine.watchlist_changed()
        return items

    def alert_service() -> AlertService:
        if alerts is None:
            raise HTTPException(404, {"error": "ALERTS DISABLED"})
        return alerts

    @app.get("/api/alerts")
    def get_alerts():
        return alert_service().view()

    @app.post("/api/alerts")
    def add_alert(body: dict = Body(...)):
        svc = alert_service()
        try:
            return svc.add(str(body.get("text") or ""))
        except ValueError as e:
            raise HTTPException(400, {"error": str(e)})

    @app.post("/api/alerts/ack")
    def ack_alerts(body: dict = Body(...)):
        svc = alert_service()
        key = "ALL" if body.get("all") is True else str(body.get("id") or "").upper()
        n = svc.ack(key) if key else 0
        if not n and key != "ALL":
            raise HTTPException(404, {"error": f"NO ACTIVE ALERT {key or '?'}"})
        return {"acked": n}

    @app.delete("/api/alerts/{rid}")
    def delete_alert(rid: str):
        try:
            alert_service().remove(rid)
        except KeyError:
            raise HTTPException(404, {"error": f"NO RULE {rid.upper()}"})
        return {"removed": rid.upper()}

    # ── TRADES: your trades, read and written through TradeBook ─────────────────────────────────────
    def written(call):
        """Run a TradeBook write; refusals become one line (409 changed meanwhile, 404 no such row, 400 a wrong
        file, 423 the file is held by another program). A write brings TRADES up to date at once and the
        portfolio screens after it."""
        try:
            out = call()
        except Blocked as e:
            raise HTTPException(423, {"error": str(e)})
        except Conflict:
            raise HTTPException(409, {"error": "YOUR TRADES CHANGED MEANWHILE — RELOADED: CHECK AND SAVE AGAIN"})
        except Missing as e:
            raise HTTPException(404, {"error": str(e)})
        except Invalid as e:
            raise HTTPException(400, {"error": str(e), "errors": e.errors})
        engine.inputs_changed()
        return out

    def need_etag(body: dict) -> str:
        tag = body.get("etag")
        if not isinstance(tag, str) or not tag:
            raise HTTPException(400, {"error": "ETAG REQUIRED — RELOAD"})
        return tag

    def need(body: dict, key: str, kind: type):
        value = body.get(key)
        if not isinstance(value, kind):
            raise RequestValidationError([{"loc": ("body", key), "msg": f"{key} must be a {kind.__name__}"}])
        return value

    @app.get("/api/trades")
    def get_trades():
        return book.read()

    @app.post("/api/trades")
    def add_trade(body: dict = Body(...)):
        tag, trade = need_etag(body), need(body, "trade", dict)
        return written(lambda: book.add(tag, trade))

    @app.put("/api/trades/{rid}")
    def edit_trade(rid: str, body: dict = Body(...)):
        tag, trade = need_etag(body), need(body, "trade", dict)
        return written(lambda: book.update(tag, rid, trade))

    @app.delete("/api/trades/{rid}")
    def delete_trade(rid: str, body: dict = Body(...)):
        tag = need_etag(body)
        return written(lambda: book.delete(tag, rid))

    @app.post("/api/trades/preview")
    def preview_trades(body: dict = Body(...)):
        text, mode = need(body, "text", str), body.get("mode") or "append"
        try:
            return book.preview(text, mode)
        except Invalid as e:
            raise HTTPException(400, {"error": str(e)})

    @app.post("/api/trades/import")
    def import_trades(body: dict = Body(...)):
        tag, text, mode = need_etag(body), need(body, "text", str), body.get("mode") or "append"
        return written(lambda: book.import_text(tag, text, mode))

    @app.post("/api/trades/reset")
    def reset_trades(body: dict = Body(...)):
        tag = need_etag(body)
        return written(lambda: book.reset(tag))

    @app.get("/api/jobs")
    def jobs():
        return {"jobs": engine.runner.jobs()}

    @app.get("/api/stream")
    async def stream(request: Request):
        q = engine.broker.subscribe()

        async def events():
            try:
                async for chunk in sse_stream(q):
                    if await request.is_disconnected():
                        break
                    yield chunk
            finally:
                engine.broker.unsubscribe(q)
        return StreamingResponse(events(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})

    if (web_dir / "app").is_dir():
        app.mount("/app", StaticFiles(directory=web_dir / "app"), name="app")

    @app.get("/")
    def index():
        return FileResponse(web_dir / "index.html", media_type="text/html",
                            headers={"Cache-Control": "no-store"})

    return app
