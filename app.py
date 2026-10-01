# -*- coding: utf-8 -*-
"""
Web 服务：把 rag.answer() 包装成 HTTP API，并托管前端聊天页

用法:
  venv/Scripts/python.exe app.py     →  浏览器打开 http://127.0.0.1:8000

接口:
  GET  /api/health   健康检查（向量库规模 / 阈值）
  POST /api/ask      {"question": "..."} → 回答 + 引用 + 拒答标记
"""
import sys
from pathlib import Path

import uvicorn
from starlette.applications import Starlette
from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import rag  # noqa: E402  (导入时加载向量库，只执行一次)

STATIC_DIR = ROOT / "static"


async def index(request: Request):
    return FileResponse(STATIC_DIR / "index.html")


async def health(request: Request):
    return JSONResponse({
        "ok": True,
        "chunks": len(rag._chunks),
        "threshold": rag.THRESHOLD,
        "top_k": rag.TOP_K,
    })


async def ask(request: Request):
    data = await request.json()
    question = (data.get("question") or "").strip()
    if not question:
        return JSONResponse({"error": "问题不能为空"}, status_code=400)
    try:
        # rag.answer 内部是同步网络调用，丢线程池避免阻塞事件循环
        r = await run_in_threadpool(rag.answer, question)
    except Exception as e:
        return JSONResponse({"error": f"后端调用失败: {e}"}, status_code=502)
    return JSONResponse({
        "answer": r["text"],
        "refused": r["refused"],
        "hard_refused": r["hard_refused"],
        "score": round(r["score"], 3),
        "refs": [
            {"doc": c["doc"], "chapter": (c["chapter"] or "").strip(),
             "article": (c["article"] or "").strip()}
            for c in r["refs"]
        ],
    })


app = Starlette(routes=[
    Route("/", index),
    Route("/api/health", health),
    Route("/api/ask", ask, methods=["POST"]),
    Mount("/static", StaticFiles(directory=STATIC_DIR), name="static"),
])

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8000)
