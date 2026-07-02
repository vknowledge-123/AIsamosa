from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import urlencode

from fastapi import FastAPI, File, Form, HTTPException, Request, Response, UploadFile, status
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.config import get_settings
from app.dashboard.serializers import serialize_summary
from app.services.market_data import generate_sample_candles
from app.services.simulation import SimulationEngine


settings = get_settings()
app_dir = Path(__file__).resolve().parent
static_version = str(
    int(
        max(
            (app_dir / "static" / "app.js").stat().st_mtime,
            (app_dir / "static" / "styles.css").stat().st_mtime,
        )
    )
)
app = FastAPI(title=settings.app_title)
app.add_middleware(GZipMiddleware, minimum_size=2048)
app.mount("/static", StaticFiles(directory=app_dir / "static"), name="static")
templates = Jinja2Templates(directory=str(app_dir / "templates"))
engine = SimulationEngine(settings)


@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request, request_token: str | None = None, status: str | None = None):
    if request_token:
        query = urlencode({"zerodha_login": "error", "message": "Use /zerodha/callback as the Kite redirect URL."})
        return RedirectResponse(url=f"/?{query}", status_code=303)
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={"title": settings.app_title, "static_version": static_version},
    )


@app.get("/zerodha/callback")
async def zerodha_callback(request_token: str | None = None, status: str | None = None):
    if not request_token:
        query = urlencode({"zerodha_login": "error", "message": "Zerodha did not return request_token."})
        return RedirectResponse(url=f"/?{query}", status_code=303)
    try:
        await run_in_threadpool(engine.start_zerodha_session_async, request_token)
    except Exception as exc:
        query = urlencode({"zerodha_login": "error", "message": str(exc)})
        return RedirectResponse(url=f"/?{query}", status_code=303)
    return RedirectResponse(url="/?zerodha_login=pending", status_code=303)


@app.get("/api/static-health")
async def static_health():
    static_dir = app_dir / "static"
    assets = {
        "styles.css": static_dir / "styles.css",
        "app.js": static_dir / "app.js",
    }
    return {
        "static_dir": str(static_dir),
        "assets": {
            name: {
                "exists": path.exists(),
                "size": path.stat().st_size if path.exists() else 0,
                "mtime": int(path.stat().st_mtime) if path.exists() else None,
            }
            for name, path in assets.items()
        },
        "static_version": static_version,
    }


@app.get("/api/state")
async def get_state(request: Request):
    revision = await run_in_threadpool(engine.get_state_revision)
    current_etag = f'W/"state-{revision}"'
    if request.headers.get("if-none-match") == current_etag:
        return Response(status_code=status.HTTP_304_NOT_MODIFIED, headers={"ETag": current_etag})
    state = await run_in_threadpool(engine.get_state)
    etag = f'W/"state-{state.state_revision}"'
    return Response(content=state.model_dump_json(), media_type="application/json", headers={"ETag": etag})


@app.get("/api/state/summary")
async def get_state_summary():
    return await run_in_threadpool(serialize_summary, engine)


@app.get("/api/state/stream")
async def stream_state(request: Request):
    async def event_stream():
        last_revision = -1
        while True:
            if await request.is_disconnected():
                break
            revision = await run_in_threadpool(engine.wait_for_state_revision, last_revision, 15.0)
            if revision > last_revision:
                last_revision = revision
                summary = await run_in_threadpool(serialize_summary, engine)
                yield f"event: state\ndata: {json.dumps({'revision': revision, 'summary': summary})}\n\n"
            else:
                yield ": keep-alive\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@app.get("/api/settings/credentials")
async def get_credentials():
    return await run_in_threadpool(engine.get_credential_summary)


@app.get("/api/broker/zerodha/login-url")
async def zerodha_login_url():
    try:
        return {"login_url": await run_in_threadpool(engine.zerodha_login_url)}
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/broker/zerodha/session")
async def generate_zerodha_session(request_token: str = Form(default="")):
    try:
        state = await run_in_threadpool(engine.generate_zerodha_session, request_token)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"message": "Zerodha access token generated and saved.", "state": state}


@app.get("/api/health/ai")
async def get_ai_health():
    return await run_in_threadpool(engine.ai_service.health)


@app.on_event("shutdown")
async def shutdown_event():
    engine.shutdown()


@app.post("/api/simulation/load-sample")
async def load_sample():
    await run_in_threadpool(engine.reset_with_candles, generate_sample_candles())
    return await run_in_threadpool(engine.get_state)


@app.post("/api/instrument-mode")
async def set_instrument_mode(instrument_mode: str = Form(...)):
    try:
        state = await run_in_threadpool(engine.set_instrument_mode, instrument_mode)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"message": f"Switched instrument mode to {state.instrument.label}.", "state": state}


@app.get("/api/stocks/search")
async def search_stocks(q: str = "", limit: int = 20):
    try:
        matches = await run_in_threadpool(engine.search_stocks, q, limit)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"results": matches}


@app.post("/api/stocks/watchlist/add")
async def add_stock_to_watchlist(symbol: str = Form(...)):
    try:
        state = await run_in_threadpool(engine.add_stock_to_watchlist, symbol)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"message": f"Added {state.instrument.symbol} to the stock watchlist.", "state": state}


@app.post("/api/stocks/watchlist/bulk-add")
async def bulk_add_stocks_to_watchlist(
    bulk_text: str = Form(...),
    trade_bias: str = Form(default="both"),
):
    try:
        state, added_symbols, skipped_symbols = await run_in_threadpool(
            engine.add_bulk_stocks_to_watchlist,
            bulk_text,
            trade_bias,
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    message = f"Added {len(added_symbols)} stock(s) to the watchlist: {', '.join(added_symbols)}."
    if skipped_symbols:
        message += f" Skipped: {', '.join(skipped_symbols)}."
    return {
        "message": message,
        "state": state,
        "added_symbols": added_symbols,
        "skipped_symbols": skipped_symbols,
    }


@app.post("/api/universe/warmup/save")
async def save_universe_warmup_list(bulk_text: str = Form(...)):
    try:
        warmup = await run_in_threadpool(engine.save_universe_warmup_list, bulk_text)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "message": warmup.message,
        "universe_warmup": warmup.model_dump(mode="json"),
    }


@app.post("/api/universe/warmup/start")
async def start_universe_warmup(
    client_id: str = Form(default=""),
    access_token: str = Form(default=""),
):
    try:
        job = await run_in_threadpool(engine.start_universe_warmup_async, client_id, access_token)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "message": job.message,
        "job": job.model_dump(mode="json"),
    }


@app.post("/api/stocks/watchlist/select")
async def select_stock(symbol: str = Form(...)):
    try:
        state = await run_in_threadpool(engine.select_stock, symbol)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"message": f"Selected {state.instrument.symbol} as the active stock.", "state": state}


@app.post("/api/stocks/watchlist/remove")
async def remove_stock_from_watchlist(symbol: str = Form(...)):
    try:
        state = await run_in_threadpool(engine.remove_stock_from_watchlist, symbol)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"message": f"Removed {symbol.strip().upper()} from the stock watchlist.", "state": state}


@app.post("/api/stocks/watchlist/remove-all")
async def remove_all_stocks_from_watchlist():
    try:
        state = await run_in_threadpool(engine.remove_all_stocks_from_watchlist)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"message": "Removed all stocks from the stock watchlist.", "state": state}


@app.post("/api/hybrid/watchlist/bulk-add")
async def bulk_add_hybrid_stocks(bulk_text: str = Form(...)):
    try:
        state, added_symbols, skipped_symbols = await run_in_threadpool(engine.add_bulk_hybrid_stocks, bulk_text)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    message = f"Saved {len(added_symbols)} hybrid stock(s): {', '.join(added_symbols)}."
    if skipped_symbols:
        message += f" Skipped: {', '.join(skipped_symbols)}."
    return {
        "message": message,
        "state": state,
        "added_symbols": added_symbols,
        "skipped_symbols": skipped_symbols,
    }


@app.post("/api/hybrid/watchlist/select")
async def select_hybrid_stock(symbol: str = Form(...)):
    try:
        state = await run_in_threadpool(engine.select_hybrid_stock, symbol)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"message": f"Selected {symbol.strip().upper()} for fixed-stock hybrid trading.", "state": state}


@app.post("/api/stocks/watchlist/square-off")
async def square_off_stock_position(symbol: str = Form(...)):
    try:
        state = await run_in_threadpool(engine.square_off_stock_position, symbol)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    normalized = symbol.strip().upper()
    return {"message": f"Square off requested for {normalized}; trading is disabled for this stock.", "state": state}


@app.post("/api/stocks/watchlist/enable-trading")
async def enable_stock_trading(symbol: str = Form(...)):
    try:
        state = await run_in_threadpool(engine.enable_stock_trading, symbol)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    normalized = symbol.strip().upper()
    return {"message": f"Trading enabled again for {normalized}.", "state": state}


@app.post("/api/simulation/step")
async def step_simulation(steps: int = Form(default=1)):
    if steps < 1 or steps > 30:
        raise HTTPException(status_code=400, detail="steps must be between 1 and 30")
    return await run_in_threadpool(engine.step, steps=steps)


@app.post("/api/simulation/today")
async def simulate_today(
    client_id: str = Form(default=""),
    access_token: str = Form(default=""),
    decision_duration_minutes: int = Form(default=1),
    stock_replay_scope: str = Form(default="all"),
):
    try:
        state = await run_in_threadpool(
            engine.simulate_today_session,
            client_id=client_id,
            access_token=access_token,
            replay_decision_duration_minutes=decision_duration_minutes,
            stock_replay_scope=stock_replay_scope,
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "message": (
            f"Simulated closed 1-minute session candles for {state.instrument.label} "
            f"using {decision_duration_minutes}-minute replay decisions."
        ),
        "state": state,
    }


@app.post("/api/simulation/today/start")
async def start_simulate_today(
    client_id: str = Form(default=""),
    access_token: str = Form(default=""),
    decision_duration_minutes: int = Form(default=1),
    stock_replay_scope: str = Form(default="all"),
):
    try:
        state = await run_in_threadpool(
            engine.start_simulate_today_session_async,
            client_id=client_id,
            access_token=access_token,
            replay_decision_duration_minutes=decision_duration_minutes,
            stock_replay_scope=stock_replay_scope,
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "message": (
            f"Started background today replay for {state.instrument.label} "
            f"using {decision_duration_minutes}-minute replay decisions."
        ),
        "state": state,
    }


@app.post("/api/simulation/historical")
async def simulate_historical(
    client_id: str = Form(default=""),
    access_token: str = Form(default=""),
    replay_date: str = Form(...),
    previous_context_date: str = Form(...),
    decision_duration_minutes: int = Form(default=1),
    stock_replay_scope: str = Form(default="all"),
):
    try:
        state = await run_in_threadpool(
            engine.simulate_historical_session,
            client_id=client_id,
            access_token=access_token,
            replay_date=replay_date,
            previous_context_date=previous_context_date,
            replay_decision_duration_minutes=decision_duration_minutes,
            stock_replay_scope=stock_replay_scope,
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "message": (
            f"Simulated historical 1-minute session candles for {state.instrument.label} "
            f"using {decision_duration_minutes}-minute replay decisions."
        ),
        "state": state,
    }


@app.post("/api/simulation/historical/start")
async def start_simulate_historical(
    client_id: str = Form(default=""),
    access_token: str = Form(default=""),
    replay_date: str = Form(...),
    previous_context_date: str = Form(...),
    decision_duration_minutes: int = Form(default=1),
    stock_replay_scope: str = Form(default="all"),
):
    try:
        state = await run_in_threadpool(
            engine.start_simulate_historical_session_async,
            client_id=client_id,
            access_token=access_token,
            replay_date=replay_date,
            previous_context_date=previous_context_date,
            replay_decision_duration_minutes=decision_duration_minutes,
            stock_replay_scope=stock_replay_scope,
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "message": (
            f"Started background historical replay for {state.instrument.label} "
            f"using {decision_duration_minutes}-minute replay decisions."
        ),
        "state": state,
    }


@app.post("/api/simulation/historical-range/start")
async def start_simulate_historical_range(
    client_id: str = Form(default=""),
    access_token: str = Form(default=""),
    replay_start_date: str = Form(...),
    replay_end_date: str = Form(...),
    decision_duration_minutes: int = Form(default=1),
    stock_replay_scope: str = Form(default="all"),
    instrument_mode: str = Form(default=""),
):
    try:
        state = await run_in_threadpool(
            engine.start_simulate_historical_range_async,
            client_id=client_id,
            access_token=access_token,
            replay_start_date=replay_start_date,
            replay_end_date=replay_end_date,
            replay_decision_duration_minutes=decision_duration_minutes,
            stock_replay_scope=stock_replay_scope,
            replay_instrument_mode=instrument_mode or None,
            return_state=False,
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "message": (
            f"Started background historical range replay from {replay_start_date} to {replay_end_date} "
            f"using {decision_duration_minutes}-minute replay decisions."
        ),
        "job": state.model_dump(mode="json"),
    }


@app.post("/api/upload/candles")
async def upload_candles(file: UploadFile = File(...)):
    if not file.filename:
        raise HTTPException(status_code=400, detail="A CSV file is required")
    content = await file.read()
    try:
        await run_in_threadpool(engine.load_csv, content)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"message": f"Loaded candle data from {file.filename}", "state": await run_in_threadpool(engine.get_state)}


@app.post("/api/upload/rulebook")
async def upload_rulebook(file: UploadFile = File(...)):
    if not file.filename:
        raise HTTPException(status_code=400, detail="A document file is required")
    content = await file.read()
    try:
        text = await run_in_threadpool(engine.rulebook_service.extract_text, file.filename, content)
        job = await run_in_threadpool(engine.start_rulebook_job, file.filename, text)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "message": f"Started rulebook learning for {file.filename}.",
        "job": job.model_dump(mode="json"),
        "state": await run_in_threadpool(engine.get_state),
    }


@app.post("/api/learn/text")
async def learn_from_text(source_name: str = Form(...), source_text: str = Form(...)):
    if not source_text.strip():
        raise HTTPException(status_code=400, detail="source_text is required")
    message = await run_in_threadpool(engine.update_rulebook_from_text, source_name, source_text)
    return {"message": message, "state": await run_in_threadpool(engine.get_state)}


@app.post("/api/live/connect")
async def connect_live_feed(
    client_id: str = Form(default=""),
    access_token: str = Form(default=""),
):
    try:
        summary = await run_in_threadpool(
            engine.connect_live_feed,
            client_id=client_id,
            access_token=access_token,
            return_state=False,
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    instrument = summary.get("instrument", {}) if isinstance(summary, dict) else {}
    broker = summary.get("broker_provider") if isinstance(summary, dict) else None
    return {
        "message": f"Connecting to {(broker or 'Dhan').title()} live feed for {instrument.get('label') or 'selected instrument'}.",
        "summary": summary,
    }


@app.post("/api/live/sync-history")
async def sync_live_history(
    client_id: str = Form(default=""),
    access_token: str = Form(default=""),
):
    try:
        state = await run_in_threadpool(engine.sync_dhan_context, client_id=client_id, access_token=access_token)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"message": f"Synced previous-day and session 1-minute candles for {state.instrument.label} from Dhan.", "state": state}


@app.post("/api/live/sync-history/start")
async def start_sync_live_history(
    client_id: str = Form(default=""),
    access_token: str = Form(default=""),
):
    try:
        state = await run_in_threadpool(engine.start_sync_dhan_context_async, client_id, access_token)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"message": f"Started background Dhan sync for {state.instrument.label}.", "state": state}


@app.post("/api/live/disconnect")
async def disconnect_live_feed():
    state = await run_in_threadpool(engine.disconnect_live_feed)
    return {"message": "Disconnected Dhan live feed.", "state": state}


@app.post("/api/trading/start")
async def start_live_trading():
    try:
        state = await run_in_threadpool(engine.start_live_trading)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"message": "Live heuristic trading is armed.", "state": state}


@app.post("/api/trading/start-paper")
async def start_live_paper_trading():
    try:
        state = await run_in_threadpool(engine.start_live_paper_trading)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"message": "Live paper trading is armed. No broker orders will be placed.", "state": state}


@app.post("/api/trading/square-off")
async def square_off_all_trades():
    try:
        state = await run_in_threadpool(engine.square_off_all_trades)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"message": "Square off requested and live heuristic trading stopped.", "state": state}


@app.post("/api/settings/credentials")
async def save_credentials(
    client_id: str = Form(default=""),
    access_token: str = Form(default=""),
    broker_provider: str = Form(default="dhan"),
    zerodha_api_key: str = Form(default=""),
    zerodha_api_secret: str = Form(default=""),
    zerodha_access_token: str = Form(default=""),
    openai_api_key: str = Form(default=""),
    openai_model: str = Form(default=""),
    deepseek_api_key: str = Form(default=""),
    deepseek_model: str = Form(default=""),
    full_ai_provider: str = Form(default=""),
    operating_mode: str = Form(default=""),
    nifty_order_lots: int = Form(default=1),
    stock_trade_capital: float = Form(default=25000.0),
    stock_execution_mode: str = Form(default="cash"),
    stock_future_lots: int = Form(default=1),
    stock_option_lots: int = Form(default=1),
    heuristic_advance_timeframe_minutes: int = Form(default=3),
    heuristic_advance_min_2m_turnover: float = Form(default=10000000.0),
    nifty_expiry_preference: str = Form(default="current-weekly"),
    stock_partial_profit_enabled: str = Form(default="true"),
    stock_trailing_stop_enabled: str = Form(default="true"),
    stock_heuristic_early_exit_enabled: str = Form(default="true"),
    nifty_trailing_stop_enabled: str = Form(default="true"),
    nifty_heuristic_early_exit_enabled: str = Form(default="true"),
    nifty_cost_sl_enabled: str = Form(default="false"),
    nifty_cost_sl_points: float = Form(default=35.0),
    nifty_min_sl_points: float = Form(default=40.0),
    nifty_max_sl_points: float = Form(default=60.0),
    nifty_target_enabled: str = Form(default="false"),
    nifty_target_points: float = Form(default=90.0),
    nifty_target_trailing_enabled: str = Form(default="false"),
    nifty_target_trailing_points: float = Form(default=20.0),
    nifty_daily_max_loss_enabled: str = Form(default="false"),
    nifty_daily_max_loss: float = Form(default=100.0),
    pyramiding_enabled: str = Form(default="false"),
    intelligent_pyramiding_enabled: str = Form(default="false"),
    stock_percent_pyramiding_enabled: str = Form(default="false"),
    stock_percent_pyramiding_step: float = Form(default=1.0),
    stock_cost_sl_after_pyramid_enabled: str = Form(default="false"),
    nifty_point_pyramiding_enabled: str = Form(default="false"),
    nifty_point_pyramiding_points: float = Form(default=50.0),
    nifty_middle_round_liquidity_enabled: str = Form(default="false"),
    nifty_trade_bias: str = Form(default="both"),
    nifty_option_trade_mode: str = Form(default="selling"),
    hybrid_buy_gainer_loser_enabled: str = Form(default="true"),
    global_mtm_square_off_enabled: str = Form(default="false"),
    global_mtm_square_off_threshold: float = Form(default=0.0),
    position_max_loss_enabled: str = Form(default="false"),
    position_max_loss: float = Form(default=400.0),
):
    partial_profit_enabled = stock_partial_profit_enabled.strip().lower() in {"1", "true", "yes", "on"}
    trailing_stop_enabled = stock_trailing_stop_enabled.strip().lower() in {"1", "true", "yes", "on"}
    heuristic_early_exit_enabled = stock_heuristic_early_exit_enabled.strip().lower() in {"1", "true", "yes", "on"}
    nifty_trailing_enabled = nifty_trailing_stop_enabled.strip().lower() in {"1", "true", "yes", "on"}
    nifty_early_exit_enabled = nifty_heuristic_early_exit_enabled.strip().lower() in {"1", "true", "yes", "on"}
    nifty_cost_enabled = nifty_cost_sl_enabled.strip().lower() in {"1", "true", "yes", "on"}
    nifty_target_control_enabled = nifty_target_enabled.strip().lower() in {"1", "true", "yes", "on"}
    nifty_target_trail_enabled = nifty_target_trailing_enabled.strip().lower() in {"1", "true", "yes", "on"}
    nifty_daily_loss_enabled = nifty_daily_max_loss_enabled.strip().lower() in {"1", "true", "yes", "on"}
    pyramid_enabled = pyramiding_enabled.strip().lower() in {"1", "true", "yes", "on"}
    intelligent_pyramid_enabled = intelligent_pyramiding_enabled.strip().lower() in {"1", "true", "yes", "on"}
    stock_percent_pyramid_enabled = stock_percent_pyramiding_enabled.strip().lower() in {"1", "true", "yes", "on"}
    stock_cost_after_pyramid_enabled = stock_cost_sl_after_pyramid_enabled.strip().lower() in {"1", "true", "yes", "on"}
    nifty_point_pyramid_enabled = nifty_point_pyramiding_enabled.strip().lower() in {"1", "true", "yes", "on"}
    nifty_middle_round_enabled = nifty_middle_round_liquidity_enabled.strip().lower() in {"1", "true", "yes", "on"}
    hybrid_gainer_loser_enabled = hybrid_buy_gainer_loser_enabled.strip().lower() in {"1", "true", "yes", "on"}
    global_mtm_enabled = global_mtm_square_off_enabled.strip().lower() in {"1", "true", "yes", "on"}
    position_loss_enabled = position_max_loss_enabled.strip().lower() in {"1", "true", "yes", "on"}
    state = await run_in_threadpool(
        engine.save_credentials,
        client_id=client_id,
        access_token=access_token,
        broker_provider=broker_provider,
        zerodha_api_key=zerodha_api_key,
        zerodha_api_secret=zerodha_api_secret,
        zerodha_access_token=zerodha_access_token,
        openai_api_key=openai_api_key,
        openai_model=openai_model,
        deepseek_api_key=deepseek_api_key,
        deepseek_model=deepseek_model,
        full_ai_provider=full_ai_provider,
        operating_mode=operating_mode,
        nifty_order_lots=nifty_order_lots,
        stock_trade_capital=stock_trade_capital,
        stock_execution_mode=stock_execution_mode,
        stock_future_lots=stock_future_lots,
        stock_option_lots=stock_option_lots,
        heuristic_advance_timeframe_minutes=heuristic_advance_timeframe_minutes,
        heuristic_advance_min_2m_turnover=heuristic_advance_min_2m_turnover,
        nifty_expiry_preference=nifty_expiry_preference,
        stock_partial_profit_enabled=partial_profit_enabled,
        stock_trailing_stop_enabled=trailing_stop_enabled,
        stock_heuristic_early_exit_enabled=heuristic_early_exit_enabled,
        nifty_trailing_stop_enabled=nifty_trailing_enabled,
        nifty_heuristic_early_exit_enabled=nifty_early_exit_enabled,
        nifty_cost_sl_enabled=nifty_cost_enabled,
        nifty_cost_sl_points=nifty_cost_sl_points,
        nifty_min_sl_points=nifty_min_sl_points,
        nifty_max_sl_points=nifty_max_sl_points,
        nifty_target_enabled=nifty_target_control_enabled,
        nifty_target_points=nifty_target_points,
        nifty_target_trailing_enabled=nifty_target_trail_enabled,
        nifty_target_trailing_points=nifty_target_trailing_points,
        nifty_daily_max_loss_enabled=nifty_daily_loss_enabled,
        nifty_daily_max_loss=nifty_daily_max_loss,
        pyramiding_enabled=pyramid_enabled,
        intelligent_pyramiding_enabled=intelligent_pyramid_enabled,
        stock_percent_pyramiding_enabled=stock_percent_pyramid_enabled,
        stock_percent_pyramiding_step=stock_percent_pyramiding_step,
        stock_cost_sl_after_pyramid_enabled=stock_cost_after_pyramid_enabled,
        nifty_point_pyramiding_enabled=nifty_point_pyramid_enabled,
        nifty_point_pyramiding_points=nifty_point_pyramiding_points,
        nifty_middle_round_liquidity_enabled=nifty_middle_round_enabled,
        nifty_trade_bias=nifty_trade_bias,
        nifty_option_trade_mode=nifty_option_trade_mode,
        hybrid_buy_gainer_loser_enabled=hybrid_gainer_loser_enabled,
        global_mtm_square_off_enabled=global_mtm_enabled,
        global_mtm_square_off_threshold=global_mtm_square_off_threshold,
        position_max_loss_enabled=position_loss_enabled,
        position_max_loss=position_max_loss,
        return_state=False,
    )
    return {
        "message": "Broker, AI, sizing, expiry, and trading-mode settings saved locally for reuse.",
        "credentials": state,
    }
