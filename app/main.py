from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.config import get_settings
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
app.mount("/static", StaticFiles(directory=app_dir / "static"), name="static")
templates = Jinja2Templates(directory=str(app_dir / "templates"))
engine = SimulationEngine(settings)


@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={"title": settings.app_title, "static_version": static_version},
    )


@app.get("/api/state")
async def get_state():
    return await run_in_threadpool(engine.get_state)


@app.get("/api/settings/credentials")
async def get_credentials():
    return await run_in_threadpool(engine.get_credential_summary)


@app.get("/api/health/ai")
async def get_ai_health():
    return await run_in_threadpool(engine.ai_service.health)


@app.on_event("shutdown")
async def shutdown_event():
    engine.disconnect_live_feed()


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


@app.post("/api/simulation/step")
async def step_simulation(steps: int = Form(default=1)):
    if steps < 1 or steps > 30:
        raise HTTPException(status_code=400, detail="steps must be between 1 and 30")
    return await run_in_threadpool(engine.step, steps=steps)


@app.post("/api/simulation/today")
async def simulate_today(
    client_id: str = Form(default=""),
    access_token: str = Form(default=""),
):
    try:
        state = await run_in_threadpool(engine.simulate_today_session, client_id=client_id, access_token=access_token)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "message": (
            f"Simulated today's closed 1-minute session candles for {state.instrument.label} "
            f"with quantity {state.instrument.lot_size}."
        ),
        "state": state,
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
        state = await run_in_threadpool(engine.connect_live_feed, client_id=client_id, access_token=access_token)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"message": f"Connecting to Dhan live feed for {state.instrument.label}.", "state": state}


@app.post("/api/live/sync-history")
async def sync_live_history(
    client_id: str = Form(default=""),
    access_token: str = Form(default=""),
):
    try:
        state = await run_in_threadpool(engine.sync_dhan_context, client_id=client_id, access_token=access_token)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"message": f"Synced previous-day and today intraday 1-minute candles for {state.instrument.label} from Dhan.", "state": state}


@app.post("/api/live/disconnect")
async def disconnect_live_feed():
    state = await run_in_threadpool(engine.disconnect_live_feed)
    return {"message": "Disconnected Dhan live feed.", "state": state}


@app.post("/api/settings/credentials")
async def save_credentials(
    client_id: str = Form(default=""),
    access_token: str = Form(default=""),
    openai_api_key: str = Form(default=""),
    openai_model: str = Form(default=""),
    deepseek_api_key: str = Form(default=""),
    deepseek_model: str = Form(default=""),
    full_ai_provider: str = Form(default=""),
    operating_mode: str = Form(default=""),
):
    if not any(
        value.strip()
        for value in (
            client_id,
            access_token,
            openai_api_key,
            openai_model,
            deepseek_api_key,
            deepseek_model,
            full_ai_provider,
            operating_mode,
        )
    ):
        raise HTTPException(status_code=400, detail="Enter at least one setting to save")
    state = await run_in_threadpool(
        engine.save_credentials,
        client_id=client_id,
        access_token=access_token,
        openai_api_key=openai_api_key,
        openai_model=openai_model,
        deepseek_api_key=deepseek_api_key,
        deepseek_model=deepseek_model,
        full_ai_provider=full_ai_provider,
        operating_mode=operating_mode,
    )
    return {"message": "Dhan, OpenAI, DeepSeek, and trading-mode settings saved locally for reuse.", "state": state}
