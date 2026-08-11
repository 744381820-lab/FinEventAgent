from __future__ import annotations

import json
from typing import Any, Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .agent.config import settings
from .agent.llm import llm_client
from .agent.orchestrator import analyze_stream, refine_stream
from .agent.schemas import EventInput

app = FastAPI(title="FinEventAgent", version="1.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class AnalyzeRequest(BaseModel):
    company_name: str = "宁德时代"
    stock_code: str = "sz300750"
    event_description: str
    event_type: str = "财报披露"
    position_ratio: float = 5.0
    cost_basis: float = 0.0
    investment_horizon: str = "medium"
    risk_tolerance: str = "medium"


class RefineRequest(BaseModel):
    session_id: str
    targets: list[str] = Field(default_factory=lambda: ["l4"])
    instruction: str = ""
    # 持仓参数覆盖（驱动 L4）
    position_ratio: Optional[float] = None
    cost_basis: Optional[float] = None
    investment_horizon: Optional[str] = None
    risk_tolerance: Optional[str] = None
    # 估值参数覆盖（驱动 L3）
    valuation_overrides: dict[str, Any] = Field(default_factory=dict)


@app.get("/api/health")
async def health():
    status = settings.public_status()
    return {
        "status": "ok",
        "mode": settings.effective_mode,
        "jinmen_configured": bool(settings.jinmen_mcp_key),
        "llm_configured": bool(settings.llm_api_key),
        "llm_circuit_open": llm_client.circuit_open,
        "extractor": "rule" if (llm_client.circuit_open or not llm_client.feature_enabled("event_extract")) else "llm",
        "search_provider": settings.sentiment_cfg["web_search_provider"] if settings.sentiment_cfg["web_search_enabled"] else None,
        "features": ["analyze", "refine", "l1_l4"],
        "config": status,
    }


@app.post("/api/analyze")
async def analyze(req: AnalyzeRequest):
    event = EventInput(**req.model_dump())

    async def event_generator():
        async for chunk in analyze_stream(event):
            yield f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.post("/api/refine")
async def refine(req: RefineRequest):
    """局部重算：基于已有会话，只重跑指定层级。"""
    event_overrides: dict[str, Any] = {}
    if req.position_ratio is not None:
        event_overrides["position_ratio"] = req.position_ratio
    if req.cost_basis is not None:
        event_overrides["cost_basis"] = req.cost_basis
    if req.investment_horizon is not None:
        event_overrides["investment_horizon"] = req.investment_horizon
    if req.risk_tolerance is not None:
        event_overrides["risk_tolerance"] = req.risk_tolerance

    async def event_generator():
        async for chunk in refine_stream(
            session_id=req.session_id,
            targets=req.targets,
            event_overrides=event_overrides or None,
            valuation_overrides=req.valuation_overrides or None,
            instruction=req.instruction,
        ):
            yield f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# 静态文件
app.mount("/", StaticFiles(directory="demo/static", html=True), name="static")
