# -*- coding: utf-8 -*-
"""
校园知识库问答核心链路：检索 → 阈值判流 → 生成/拒答

双层拒答（对应 JD「降级方案」）：
  第一层（硬拒答）: top-1 检索相似度 < THRESHOLD → 不调 LLM，直接返回转人工话术
  第二层（软拒答）: 分数过线但资料确实答不了 → prompt 约束模型自行承认无依据

用法:
  python rag.py           # 交互问答
  python rag.py --demo    # 跑内置测试题（真题+陷阱题），打印分数分布用于调阈值
"""
import json
import os
import sys
from pathlib import Path

import numpy as np
from dotenv import load_dotenv
from openai import OpenAI

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")

client = OpenAI(
    api_key=os.getenv("SILICONFLOW_API_KEY"),
    base_url="https://api.siliconflow.cn/v1",
)

EMB_MODEL = "Qwen/Qwen3-Embedding-8B"
CHAT_MODEL = "Qwen/Qwen3-8B"

TOP_K = 6          # 送入 LLM 的资料条数（混合排序 + 同文档限额后取前 K）
MAX_PER_DOC = 3    # 同一文档最多占 K 席，保证跨文档覆盖（跨文档综合题靠这个）
LEX_WEIGHT = 0.35  # 词面分权重：hybrid = (1-w)*余弦 + w*词面重叠
# 硬拒答阈值（只作用于余弦分量）。校准过程（2026-09，50 题评测实测）：
#   陷阱题余弦 0.412~0.597，真题余弦最低 0.581（"公寓楼早上几点开门"）
#   → 两者在 0.58~0.60 区间重叠，余弦无法完美分割，阈值是「误拒 vs 编造」的权衡：
#     曾取 0.55 保住低分真题，但"图书馆几点闭馆"型陷阱（0.558）两次实测编造——
#     模型拿宿舍关门时间冒充闭馆时间。编造率是红线 > 真题覆盖率，回滚 0.60：
#     全部陷阱题硬拒答（含 0.596 的最高分陷阱），「公寓楼早上几点开门」记为已知误拒
THRESHOLD = 0.60

REFUSAL_TEXT = "这个问题我不知道，请向辅导员咨询吧^_^"

SYSTEM_PROMPT = f"""你是校园规章制度问答助手，只能根据【参考资料】回答学生问题。

规则：
1. 用参考资料中的原文表述回答，语言简洁，重要数字/日期/条件不要改动。
2. 回答末尾单独一行标注来源，格式：来源：《文档名》 章节/条款号（从资料的标注行摘取，禁止编造出处）。
3. 如果参考资料不足以回答问题，必须原样回复"{REFUSAL_TEXT}"，禁止编造任何数字、日期、条件。
4. 不要使用参考资料以外的知识。"""

# ── 加载向量库（模块导入时执行一次）──
_chunks = [json.loads(l) for l in (ROOT / "data" / "chunks.jsonl").open(encoding="utf-8")]
_mat = np.load(ROOT / "data" / "vectors.npy")  # 已预归一化，点积即余弦相似度


def _lex_overlap(query: str, text: str) -> float:
    """问题与切片的字符 bigram 重叠率（0~1）。中文不分词也能粗测词面命中，
    专门救「数字/条款号型」问题——这类问题语义向量容易跑偏但字面高度吻合"""
    grams = {query[i:i + 2] for i in range(len(query) - 1)}
    grams = {g for g in grams if not g.isspace()}
    if not grams:
        return 0.0
    return sum(1 for g in grams if g in text) / len(grams)


def search(question: str, k: int = TOP_K):
    """混合检索：余弦(语义) + bigram 重叠(词面) 融合排序，再做同文档限额。

    返回 (gate, hits)：
      gate  全局余弦最大值，供阈值判流（与是否入选 top-k 无关）
      hits  按融合分降序的 [{"cos", "lex", "score", "chunk"}, ...]
    """
    resp = client.embeddings.create(model=EMB_MODEL, input=[question])
    q = np.array(resp.data[0].embedding, dtype=np.float32)
    q = q / np.linalg.norm(q)
    cos = _mat @ q
    lex = np.array([_lex_overlap(question, c["text"]) for c in _chunks], dtype=np.float32)
    hybrid = (1 - LEX_WEIGHT) * cos + LEX_WEIGHT * lex

    picked, per_doc = [], {}
    for i in np.argsort(-hybrid):
        d = _chunks[i]["doc"]
        if per_doc.get(d, 0) >= MAX_PER_DOC:
            continue
        picked.append(i)
        per_doc[d] = per_doc.get(d, 0) + 1
        if len(picked) >= k:
            break

    # 全局余弦冠军兜底：若被限额挤出且其文档未满额，替换融合分最低的入选者
    best = int(np.argmax(cos))
    if picked and best not in picked and per_doc.get(_chunks[best]["doc"], 0) < MAX_PER_DOC:
        picked[-1] = best

    hits = [{"cos": float(cos[i]), "lex": float(lex[i]),
             "score": float(hybrid[i]), "chunk": _chunks[i]} for i in picked]
    return float(cos.max()), hits


def answer(question: str) -> dict:
    """完整问答：第一层硬拒答（只看全局余弦）→ 检索拼上下文 → LLM 生成（第二层软拒答在 prompt 内）"""
    top_cos, results = search(question)

    if top_cos < THRESHOLD:  # 第一层：硬拒答，不调用 LLM
        return {"text": REFUSAL_TEXT, "refs": [], "refused": True,
                "hard_refused": True, "score": top_cos}

    context = "\n\n".join(
        f"【资料{i}】《{r['chunk']['doc']}》{(r['chunk']['chapter'] or '').strip()} "
        f"{(r['chunk']['article'] or '').strip()}\n{r['chunk']['text']}"
        for i, r in enumerate(results, 1)
    )
    resp = client.chat.completions.create(
        model=CHAT_MODEL,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"【参考资料】\n{context}\n\n【学生问题】{question}"},
        ],
        temperature=0.3,
        extra_body={"enable_thinking": False},  # Qwen3 关闭思考模式
    )
    text = resp.choices[0].message.content.strip()
    soft_refused = any(
        w in text for w in
        ("我不知道", "找不到依据", "无法确认", "没有找到", "未提及", "没有提及", "无法回答", "不足以回答")
    )
    return {
        "text": text,
        "refs": [r["chunk"] for r in results],
        "refused": soft_refused,
        "hard_refused": False,
        "score": top_cos,
    }


# ── 内置测试题：真题测检索命中，陷阱题（文件里没有的）测硬编 ──
DEMO_QUESTIONS = [
    ("真", "转专业需要什么条件？"),
    ("真", "学校一等奖学金是多少钱？"),
    ("真", "宿舍晚上几点熄灯？"),
    ("真", "旷课多少学时会受到什么处分？"),
    ("真", "图书馆的书到期了怎么续借？"),
    ("真", "休学最长可以休多久？"),
    ("真", "考试作弊会怎么处理？"),
    ("真", "GPA 是怎么计算的？"),
    ("真", "家属可以去学生宿舍过夜吗？"),
    ("真", "大学生医保每年交多少钱？"),
    ("陷阱", "毕业典礼是几月几号举行？"),
    ("陷阱", "学校食堂哪个最好吃？"),
    ("陷阱", "校园网 WiFi 密码是多少？"),
    ("陷阱", "学校允许多少人住一个宿舍？"),
    # 注："新生报到可以迟到一个星期吗"其实是可答题（手册规定请假不超两周），
    # 已从陷阱题移出——设计陷阱题时要先 grep 全文确认答案确实不存在
    ("真", "新生报到不能按期到校，最长可以请多久假？"),
]


def demo():
    """跑测试题，重点看真题/陷阱题的余弦分数分布，用于校准 THRESHOLD"""
    print(f"当前 THRESHOLD = {THRESHOLD}（只作用于余弦分量）\n")
    print(f"{'类型':<4} {'余弦top1':<8} {'top-1命中'}  问题")
    print("-" * 78)
    scores = {"真": [], "陷阱": []}
    for kind, q in DEMO_QUESTIONS:
        top_cos, results = search(q, k=1)
        c = results[0]["chunk"]
        scores[kind].append(top_cos)
        flag = "拒答" if top_cos < THRESHOLD else "过检"
        print(f"{kind:<4} {top_cos:.3f}    {flag}  {q}")
        print(f"{'':<13}↳ {c['doc']} {c['article'] or ''}")
    print("-" * 78)
    for kind in ("真", "陷阱"):
        v = scores[kind]
        print(f"{kind}题分数: min={min(v):.3f} 均值={sum(v)/len(v):.3f} max={max(v):.3f}")
    print("\n校准建议: THRESHOLD 应取在「真题最低分」与「陷阱题最高分」之间（留余量）")


def chat():
    print("校园规章问答助手（输入 q 退出）")
    while True:
        try:
            q = input("\n你: ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not q or q.lower() in ("q", "quit", "exit"):
            break
        r = answer(q)
        print(f"\n助手: {r['text']}")
        print(f"[score={r['score']:.3f} | {'硬拒答' if r['hard_refused'] else 'LLM回答'}]")
        if r["refs"]:
            print("命中来源:")
            for i, c in enumerate(r["refs"], 1):
                print(f"  {i}. 《{c['doc']}》{c['chapter'] or ''} {c['article'] or ''}")


if __name__ == "__main__":
    if "--demo" in sys.argv:
        demo()
    else:
        chat()
