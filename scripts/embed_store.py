# -*- coding: utf-8 -*-
"""
向量化入库：data/chunks.jsonl → data/vectors.npy

- 调用硅基流动 Qwen3-Embedding-8B，分批嵌入（每批 16 条）
- 429/超时自动重试（指数退避，最多 3 次）
- 向量以 float32 存盘（约 900 条 × 4096 维 ≈ 14MB）

用法: python scripts/embed_store.py
"""
import json
import os
import sys
import time
from pathlib import Path

import numpy as np
from dotenv import load_dotenv
from openai import OpenAI

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
CHUNKS = ROOT / "data" / "chunks.jsonl"
VECTORS = ROOT / "data" / "vectors.npy"

MODEL = "Qwen/Qwen3-Embedding-8B"
BATCH = 16
RETRY = 3

load_dotenv()
client = OpenAI(
    api_key=os.getenv("SILICONFLOW_API_KEY"),
    base_url="https://api.siliconflow.cn/v1",
)


def embed_batch(texts):
    """嵌入一批文本，失败时指数退避重试"""
    for attempt in range(RETRY):
        try:
            resp = client.embeddings.create(model=MODEL, input=texts)
            return [d.embedding for d in resp.data]
        except Exception as e:
            wait = 2 ** attempt
            print(f"    批次失败: {e.__class__.__name__}: {e}，{wait}s 后重试 ({attempt + 1}/{RETRY})")
            time.sleep(wait)
    raise RuntimeError("嵌入连续失败，请检查网络/余额/限流")


def main():
    chunks = [json.loads(l) for l in CHUNKS.open(encoding="utf-8")]
    texts = [c["text"] for c in chunks]
    print(f"共 {len(texts)} 条待嵌入，模型 {MODEL}，每批 {BATCH} 条")

    t0 = time.time()
    vectors = []
    for i in range(0, len(texts), BATCH):
        batch = texts[i:i + BATCH]
        vectors.extend(embed_batch(batch))
        done = min(i + BATCH, len(texts))
        print(f"  进度 {done}/{len(texts)}  ({time.time() - t0:.0f}s)", end="\r")

    mat = np.array(vectors, dtype=np.float32)
    # 预归一化后存盘：检索时直接点积 = 余弦相似度
    mat = mat / np.linalg.norm(mat, axis=1, keepdims=True)
    np.save(VECTORS, mat)
    print(f"\n完成: {mat.shape[0]} 条 × {mat.shape[1]} 维，耗时 {time.time() - t0:.0f}s，输出 {VECTORS}")


if __name__ == "__main__":
    main()
