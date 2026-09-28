"""ShopFast storefront. Run: uvicorn storefront.app:app --host 127.0.0.1 --port 8003

A customer-facing page for the demo. It forwards exactly three customer calls to the existing ShopFast API
and returns ShopFast's status code and body unchanged, so every error the page shows is ShopFast's own.
Fault switches (/admin) and runbook actions (/ops) are not forwarded: the storefront cannot change the shop.
Same-origin forwarding also means ShopFast needs no CORS change.
"""

import os
from functools import lru_cache
from pathlib import Path

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool

STATIC = Path(__file__).resolve().parent / "static"
PAGE = STATIC / "index.html"

app = FastAPI(title="ShopFast storefront")
app.mount("/static/images", StaticFiles(directory=STATIC / "images"), name="images")


def shopfast_url() -> str:
    return f"http://{os.getenv('SHOPFAST_HOST', '127.0.0.1')}:{os.getenv('SHOPFAST_PORT', '8001')}"


@lru_cache(maxsize=1)
def _client() -> httpx.Client:
    return httpx.Client(base_url=shopfast_url(), timeout=15.0)


def shopfast_client() -> httpx.Client:
    return _client()


async def _forward(method: str, path: str, request: Request | None = None) -> Response:
    body = await request.body() if request is not None else None
    headers = {"content-type": "application/json"} if body else None
    try:
        upstream = await run_in_threadpool(shopfast_client().request, method, path, content=body, headers=headers)
    except httpx.HTTPError as exc:
        return JSONResponse(status_code=502, content={"error": "ShopFast unreachable", "detail": str(exc)})
    return Response(content=upstream.content, status_code=upstream.status_code,
                    media_type=upstream.headers.get("content-type", "application/json"))


@app.get("/")
def page() -> FileResponse:
    return FileResponse(PAGE, media_type="text/html")


@app.get("/api/products")
async def products() -> Response:
    return await _forward("GET", "/products")


@app.post("/api/cart/items")
async def add_to_cart(request: Request) -> Response:
    return await _forward("POST", "/cart/items", request)


@app.post("/api/checkout")
async def checkout(request: Request) -> Response:
    return await _forward("POST", "/checkout", request)
