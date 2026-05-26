import os
import time
import warnings
import logging
from contextlib import asynccontextmanager
from typing import Any

import joblib
import numpy as np
from fastapi import FastAPI, HTTPException, Request, Response
from pydantic import BaseModel, field_validator
from prometheus_client import (
    Counter, Histogram, Gauge,
    generate_latest, CONTENT_TYPE_LATEST,
    REGISTRY,
)
from pythonjsonlogger import jsonlogger

from features import extract, to_list

def _setup_logging() -> logging.Logger:
    logger = logging.getLogger("dga_detector")
    logger.setLevel(os.getenv("LOG_LEVEL", "INFO").upper())
    handler = logging.StreamHandler()
    formatter = jsonlogger.JsonFormatter(
        fmt="%(asctime)s %(levelname)s %(name)s %(message)s",
        rename_fields={"asctime": "ts", "levelname": "level", "name": "logger"},
    )
    handler.setFormatter(formatter)
    logger.addHandler(handler)

    logging.getLogger("uvicorn.access").handlers = []
    return logger

log = _setup_logging()

warnings.filterwarnings("ignore", message="X does not have valid feature names")

MODEL_PATH      = os.getenv("MODEL_PATH", "model.pkl")
DGA_THRESHOLD   = float(os.getenv("DGA_THRESHOLD", "0.30"))
SERVICE_VERSION = os.getenv("SERVICE_VERSION", "5.0.0")

prom_requests_total = Counter(
    "dga_requests_total",
    "Total batch requests to /predict",
    ["status"],
)
prom_domains_total = Counter(
    "dga_domains_total",
    "Total domains processed",
    ["result"],
)
prom_batch_size = Histogram(
    "dga_batch_size",
    "Incoming batch size",
    buckets=[10, 50, 100, 200, 300, 500, 1000, 5000],
)
prom_latency = Histogram(
    "dga_request_duration_seconds",
    "Total request processing time (sec)",
    buckets=[.001, .005, .010, .025, .050, .100, .250, .500, 1.0],
)
prom_inference_latency = Histogram(
    "dga_inference_duration_seconds",
    "Pure LightGBM inference time (sec)",
    buckets=[.0001, .0005, .001, .005, .010, .025, .050],
)
prom_model_loaded = Gauge(
    "dga_model_loaded",
    "1 if model is loaded, 0 otherwise",
)
prom_threshold = Gauge(
    "dga_threshold",
    "Current DGA classification threshold",
)
prom_threshold.set(DGA_THRESHOLD)


_model = None
_model_loaded_at: float = 0.0


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _model, _model_loaded_at
    log.info("loading model", extra={"path": MODEL_PATH})
    try:
        _model = joblib.load(MODEL_PATH)
        _model_loaded_at = time.time()
        prom_model_loaded.set(1)
        log.info("model loaded", extra={"path": MODEL_PATH, "threshold": DGA_THRESHOLD})
    except Exception as e:
        prom_model_loaded.set(0)
        log.error("model load failed", extra={"error": str(e)})
        raise
    yield
    log.info("shutdown")
    prom_model_loaded.set(0)


app = FastAPI(
    title="DGA Detector",
    version=SERVICE_VERSION,
    lifespan=lifespan,
)


class LookupRequest(BaseModel):
    object: str

    @field_validator("object")
    @classmethod
    def not_empty(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("object cannot be empty")
        return v.lower()


class CategoryContext(BaseModel):
    score: float
    confidence: int
    dga_type: str
    entropy: float
    norm_entropy: float
    digit_ratio: float
    vowel_ratio: float
    unique_char_ratio: float
    bigram_hit_ratio: float
    consonant_run_max: int
    digit_run_max: int
    hyphen_count: int
    hyphen_ratio: float
    is_hex_pattern: int
    ends_with_digits: int


class Category(BaseModel):
    category: str
    detectedIndicator: str
    context: CategoryContext


class LookupResponse(BaseModel):
    object: str
    result: str
    categories: list[Category]


def _detect_dga_type(feats) -> tuple[str, str]:
    if feats.is_hex_pattern or feats.digit_ratio > 0.4:
        return "DGA-Domain-HexBased", "hex_based"
    if feats.length > 16 and feats.vowel_ratio > 0.2:
        return "DGA-Domain-WordSalad", "word_salad"
    return "DGA-Domain", "unknown"


def _build_response(domain: str, score: float, feats) -> LookupResponse:
    is_dga = score >= DGA_THRESHOLD
    categories: list[Category] = []
    if is_dga:
        category_label, dga_type = _detect_dga_type(feats)
        categories.append(Category(
            category=category_label,
            detectedIndicator=domain,
            context=CategoryContext(
                score=round(float(score), 4),
                confidence=min(100, int(score * 100)),
                dga_type=dga_type,
                entropy=round(float(feats.entropy), 4),
                norm_entropy=round(float(feats.norm_entropy), 4),
                digit_ratio=round(float(feats.digit_ratio), 4),
                vowel_ratio=round(float(feats.vowel_ratio), 4),
                unique_char_ratio=round(float(feats.unique_char_ratio), 4),
                bigram_hit_ratio=round(float(feats.bigram_hit_ratio), 4),
                consonant_run_max=int(feats.consonant_run_max),
                digit_run_max=int(feats.digit_run_max),
                hyphen_count=int(feats.hyphen_count),
                hyphen_ratio=round(float(feats.hyphen_ratio), 4),
                is_hex_pattern=int(feats.is_hex_pattern),
                ends_with_digits=int(feats.ends_with_digits),
            ),
        ))
    return LookupResponse(
        object=domain,
        result="detected" if is_dga else "not detected",
        categories=categories,
    )


def _predict_batch(domains: list[str]) -> list[LookupResponse]:
    feats_list = [extract(d) for d in domains]
    X = np.array([to_list(f) for f in feats_list], dtype=np.float32)

    t0 = time.perf_counter()
    scores = _model.predict_proba(X)[:, 1]
    prom_inference_latency.observe(time.perf_counter() - t0)

    results = [
        _build_response(domain, score, feats)
        for domain, score, feats in zip(domains, scores, feats_list)
    ]

    dga_count   = sum(1 for r in results if r.result == "detected")
    clean_count = len(results) - dga_count
    prom_domains_total.labels(result="detected").inc(dga_count)
    prom_domains_total.labels(result="clean").inc(clean_count)

    return results


@app.post("/api/1.3/General/lookup", response_model=list[LookupResponse])
@app.post("/api/1.1/lookup", response_model=list[LookupResponse])
@app.post("/predict", response_model=list[LookupResponse])
async def predict(items: list[LookupRequest], request: Request) -> list[dict[str, Any]]:
    if not items:
        prom_requests_total.labels(status="error").inc()
        raise HTTPException(status_code=422, detail="Empty batch")
    if len(items) > 100_000:
        prom_requests_total.labels(status="error").inc()
        raise HTTPException(status_code=413, detail="Batch too large (max 100000)")
    if _model is None:
        prom_requests_total.labels(status="error").inc()
        raise HTTPException(status_code=503, detail="Model not loaded")

    prom_batch_size.observe(len(items))

    t0 = time.perf_counter()
    domains = [item.object for item in items]
    results = _predict_batch(domains)
    elapsed_sec = time.perf_counter() - t0

    prom_latency.observe(elapsed_sec)
    prom_requests_total.labels(status="success").inc()

    dga_count = sum(1 for r in results if r.result == "detected")
    log.info("predict", extra={
        "batch_size": len(items),
        "dga_count": dga_count,
        "elapsed_ms": round(elapsed_sec * 1000, 2),
        "us_per_domain": round(elapsed_sec / len(items) * 1e6, 1),
        "client": request.client.host if request.client else "unknown",
    })

    return [r.model_dump() for r in results]


@app.get("/health")
async def health():
    if _model is None:
        raise HTTPException(status_code=503, detail="Model not loaded")
    return {
        "status": "ok",
        "threshold": DGA_THRESHOLD,
        "version": SERVICE_VERSION,
        "model_age_seconds": round(time.time() - _model_loaded_at),
    }


@app.get("/metrics")
async def metrics():
    return Response(
        content=generate_latest(REGISTRY),
        media_type=CONTENT_TYPE_LATEST,
    )