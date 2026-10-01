# -*- coding: utf-8 -*-
"""
评测脚本：跑 data/eval_questions.jsonl 全量 50 题，输出分层指标 + Markdown 报告

题型与判定:
  direct (25) / multi (10) 真题 → 应回答且命中关键词；doc 字段为标准出处（判定检索命中/引用准确）
  trap   (15) 陷阱题 → 应拒答；未拒答 = 编造（红线指标）

指标口径:
  编造率       = 陷阱题未拒答 / 陷阱题数                     ← 红线，越低越好
  拒答率       = 陷阱题拒答 / 陷阱题数（分硬拒答/软拒答两层）
  误拒率       = 真题被拒答 / 真题数
  检索命中率   = 真题 top-k 中含标准出处 / 真题数（拒答题补测检索，避免混淆两层问题）
  答案正确率   = 真题回答且命中关键词 / 真题数（端到端）
  关键词命中率 = 命中关键词的回答 / 实际回答数（回答质量）
  引用准确率   = 回答末尾《来源》与标准出处一致 / 实际回答数（引用可溯源）

用法: python scripts/eval.py
输出: 终端报告 + data/eval_results.jsonl（逐题明细）+ data/eval_report.md（人读报告）
"""
import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))  # 让 eval.py 能 import 根目录的 rag

import rag  # noqa: E402  (导入时加载向量库，一次)

EVAL_FILE = ROOT / "data" / "eval_questions.jsonl"
RESULT_FILE = ROOT / "data" / "eval_results.jsonl"
REPORT_FILE = ROOT / "data" / "eval_report.md"

TYPE_NAMES = {"direct": "直接可答", "multi": "跨文档", "trap": "陷阱题"}
CITE_RE = re.compile(r"来源[：:]\s*《([^》]+)》")


def as_docs(v):
    """doc 字段兼容字符串和数组"""
    if not v:
        return []
    return [v] if isinstance(v, str) else list(v)


def judge(item: dict, result: dict, ref_docs: list):
    """返回 (判定, 补充字段)：pass / 漏答 / 编造 / 关键词缺失"""
    if item["type"] == "trap":
        return ("pass" if result["refused"] else "编造"), {}
    if result["refused"]:
        return "漏答", {}
    hit = any(m in result["text"] for m in item["must"])
    m = CITE_RE.search(result["text"])
    cited_doc = m.group(1) if m else None
    return ("pass" if hit else "关键词缺失"), {
        "cited_doc": cited_doc,
        "cite_hit": cited_doc in ref_docs if cited_doc else False,
    }


def main():
    questions = [json.loads(l) for l in EVAL_FILE.open(encoding="utf-8")]
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"共 {len(questions)} 题 | THRESHOLD={rag.THRESHOLD} TOP_K={rag.TOP_K} | {ts}\n")

    rows = []
    t0 = time.time()
    for i, item in enumerate(questions, 1):
        ref_docs = as_docs(item.get("doc"))
        try:
            result = rag.answer(item["q"])
        except Exception as e:
            result = {"text": f"<调用失败: {e}>", "refused": True,
                      "hard_refused": True, "score": -1.0, "refs": []}
        verdict, extra = judge(item, result, ref_docs)

        # 拒答题 refs 为空 → 补测一次检索，把「检索层」和「判流层」的问题分开统计
        ret_docs = [c["doc"] for c in result["refs"]]
        if not ret_docs and item["type"] != "trap":
            _, hits = rag.search(item["q"])
            ret_docs = [h["chunk"]["doc"] for h in hits]

        rows.append({**item, "answer": result["text"], "score": round(result["score"], 3),
                     "hard_refused": result["hard_refused"], "refused": result["refused"],
                     "top_docs": ret_docs[:3],
                     "retrieval_hit": any(d in ref_docs for d in ret_docs),
                     "verdict": verdict, **extra})
        mark = "✓" if verdict == "pass" else f"✗{verdict}"
        print(f"[{i:>2}/{len(questions)}] {mark:<8} {item['id']} {result['score']:.3f} "
              f"{item['q'][:22]}  ({time.time() - t0:.0f}s)")

    with RESULT_FILE.open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    # ── 指标汇总 ──
    agg = lambda t: [r for r in rows if r["type"] == t]  # noqa: E731
    trap, real = agg("trap"), agg("direct") + agg("multi")
    n_real, n_trap, n_ans = len(real), len(trap), sum(not r["refused"] for r in real)

    trap_refused = sum(r["refused"] for r in trap)
    trap_hard = sum(r["refused"] and r["hard_refused"] for r in trap)
    real_refused = sum(r["refused"] for r in real)
    kw_hit = sum(r["verdict"] == "pass" for r in real)
    ret_hit = sum(r["retrieval_hit"] for r in real)
    cite_hit = sum(r.get("cite_hit", False) for r in real if not r["refused"])
    no_cite = sum(r.get("cited_doc") is None for r in real if not r["refused"])

    metrics = [
        ("陷阱题编造率", (n_trap - trap_refused) / n_trap, f"{n_trap - trap_refused}/{n_trap} 未拒答", True),
        ("陷阱题拒答率", trap_refused / n_trap, f"硬拒答 {trap_hard} + 软拒答 {trap_refused - trap_hard}", False),
        ("真题误拒率", real_refused / n_real, f"{real_refused}/{n_real} 被拒答（含已知误拒 D08）", False),
        ("检索命中率", ret_hit / n_real, f"{ret_hit}/{n_real} 标准出处进入 top-{rag.TOP_K}", False),
        ("答案正确率", kw_hit / n_real, f"{kw_hit}/{n_real} 端到端（回答且命中关键词）", True),
        ("关键词命中率", kw_hit / n_ans if n_ans else 0, f"{kw_hit}/{n_ans} 已回答中命中", False),
        ("引用准确率", cite_hit / n_ans if n_ans else 0, f"{cite_hit}/{n_ans} 引用与标准出处一致（{no_cite} 题未标注）", False),
    ]

    print("\n" + "=" * 60)
    print("评测汇总")
    print("=" * 60)
    for name, val, detail, star in metrics:
        print(f"{'★ ' if star else '  '}{name:<8} {val:>7.1%}   {detail}")
    for t in ("direct", "multi", "trap"):
        sub = agg(t)
        ok = sum(r["verdict"] == "pass" for r in sub)
        print(f"  {TYPE_NAMES[t]}({len(sub):>2}题)  {'拒答率' if t == 'trap' else '合格率'}: "
              f"{ok / len(sub):5.1%}  ({ok}/{len(sub)})")

    write_report(ts, rows, agg, metrics)
    print(f"\n明细: {RESULT_FILE}\n报告: {REPORT_FILE}")


def write_report(ts, rows, agg, metrics):
    """生成 Markdown 人读报告：配置 → 指标 → 逐题明细 → 未通过详情"""
    fails = [r for r in rows if r["verdict"] != "pass"]
    lines = [
        "# 校园规章问答助手 · 评测报告",
        "",
        f"- 时间：{ts}",
        f"- 配置：THRESHOLD={rag.THRESHOLD} · TOP_K={rag.TOP_K} · "
        f"MAX_PER_DOC={rag.MAX_PER_DOC} · LEX_WEIGHT={rag.LEX_WEIGHT}",
        f"- 语料：{len(rag._chunks)} 切片 · 嵌入 {rag.EMB_MODEL} · 生成 {rag.CHAT_MODEL}",
        f"- 题量：直接可答 {len(agg('direct'))} + 跨文档 {len(agg('multi'))} + "
        f"陷阱 {len(agg('trap'))} = {len(rows)}",
        "",
        "## 指标总览",
        "",
        "| 指标 | 数值 | 口径 |",
        "|---|---|---|",
    ]
    for name, val, detail, _ in metrics:
        lines.append(f"| {name} | **{val:.1%}** | {detail} |")

    lines += ["", "## 分题型", "", "| 题型 | 数量 | 合格/拒答 | 比例 |", "|---|---|---|---|"]
    for t in ("direct", "multi", "trap"):
        sub = agg(t)
        ok = sum(r["verdict"] == "pass" for r in sub)
        lines.append(f"| {TYPE_NAMES[t]} | {len(sub)} | {ok} | {ok / len(sub):.1%} |")

    lines += ["", "## 逐题明细", "", "| ID | 判定 | 分数 | 问题 | 标准出处 | 模型引用 |",
              "|---|---|---|---|---|---|"]
    marks = {"pass": "✅", "漏答": "⚠️ 漏答", "编造": "❌ 编造", "关键词缺失": "❌ 关键词缺失"}
    for r in rows:
        docs = "／".join(as_docs(r.get("doc"))) or "-"
        cited = r.get("cited_doc") or ("（拒答）" if r["refused"] else "（未标注）")
        lines.append(f"| {r['id']} | {marks[r['verdict']]} | {r['score']:.3f} | "
                     f"{r['q'][:28]} | {docs[:38]} | {cited} |")

    if fails:
        lines += ["", "## 未通过题目详情", ""]
        for r in fails:
            layer = "硬拒答" if r["hard_refused"] else ("软拒答" if r["refused"] else "LLM回答")
            lines += [f"### {r['id']}　{r['q']}",
                      f"- 判定：{r['verdict']} · 分数 {r['score']:.3f} · {layer}",
                      f"- 检索：{'、'.join(r['top_docs']) or '-'}",
                      "", "```", r["answer"], "```", ""]
    REPORT_FILE.write_text("\n".join(lines), encoding="utf-8")


if __name__ == "__main__":
    main()
