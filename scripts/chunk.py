# -*- coding: utf-8 -*-
"""
切分 data/clean/*.txt → data/chunks.jsonl

切分策略（按文档结构自动选择）：
- 条款体（「第X条」出现 ≥5 次）：一条一 chunk，章标题记入 chapter 元数据
- 编号体（一、/（一）/1./①）：按编号开块；块不足 80 字时向后聚合
- 超长块（>800 字）按句号/分号边界二次切分（约 600 字/片）

每行输出 JSON:
  {"text": 正文, "doc": 文档名, "chapter": 章名或null, "article": 条款号或null, "source": 来源文件}

用法: python scripts/chunk.py
"""
import json
import re
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
CLEAN = ROOT / "data" / "clean"
OUT = ROOT / "data" / "chunks.jsonl"

LONG_LIMIT = 800    # 超过此长度二次切分
TARGET_LEN = 600    # 二次切分目标长度
MIN_MERGE = 80      # 编号体模式下，不足此长度向后续块聚合

ZHANG = re.compile(r"^(第[一二三四五六七八九十百零\d]+章)\s*(.*)")
TIAO = re.compile(r"^(第[一二三四五六七八九十百零\d]+条)\s*(.*)")
GROUP_HEAD = re.compile(r"^([一二三四五六七八九十]+、|[（(][一二三四五六七八九十]+[)）])")
SUB_HEAD = re.compile(r"^(\d+[.、．])")
DEEP_HEAD = re.compile(r"^([（(]\d+[)）]|[①②③④⑤⑥⑦⑧⑨⑩])")


def make_label(p: str) -> str:
    """取段落开头的编号 + 少量标题词，用作 article 标签"""
    m = re.match(r"^(第[一二三四五六七八九十百零\d]+[章节条]"
                 r"|[一二三四五六七八九十]+、"
                 r"|[（(][一二三四五六七八九十\d]+[)）]"
                 r"|\d+[.、．]"
                 r"|[①②③④⑤⑥⑦⑧⑨⑩])", p)
    if not m:
        return ""
    tag = m.group(1)
    rest = p[m.end():].strip()
    return tag + (rest[:12] if rest else "")


def split_long(text: str):
    """超长块按句读边界切成约 TARGET_LEN 的片段"""
    if len(text) <= LONG_LIMIT:
        return [text]
    sentences = re.split(r"(?<=[。；])", text)
    parts, cur = [], ""
    for s in sentences:
        if not s:
            continue
        if len(cur) + len(s) > TARGET_LEN and cur:
            parts.append(cur)
            cur = s
        else:
            cur += s
    if cur:
        parts.append(cur)
    return parts


def chunk_by_tiao(paras, doc, source):
    """条款体：一条一 chunk，跟踪章标题"""
    chunks, cur_article, cur_text, chapter = [], None, None, None

    def flush():
        nonlocal cur_text
        if cur_text:
            pieces = split_long(cur_text)
            for i, piece in enumerate(pieces):
                suffix = f"（{i + 1}/{len(pieces)}）" if len(pieces) > 1 else ""
                chunks.append({
                    "text": piece + suffix,
                    "doc": doc, "chapter": chapter,
                    "article": cur_article, "source": source,
                })
        cur_text = None

    for p in paras:
        m_z, m_t = ZHANG.match(p), TIAO.match(p)
        if m_z:
            chapter = p[:20]
            continue  # 章标题不单独成块
        if m_t:
            flush()
            cur_article, cur_text = m_t.group(1), p
        else:
            if cur_text is None:
                cur_article, cur_text = "总则", p  # 文首制定依据段
            else:
                cur_text += p
    flush()
    return chunks


def chunk_by_items(paras, doc, source):
    """编号体：编号行开块 + 短块聚合

    聚合规则（按编号层级）：
    - 组头（一、 / （一） / 第X章）→ 强制开新块
    - 子项（1. / 1、）→ 仅当当前块是组头块且不足 80 字时并入，
      顶层编号（如 FAQ 的 1、2、3、）之间不互相聚合
    - 深层项（（1） / ①）→ 总是并入当前块（从属级，独立成块无意义）
    - 无编号段 → 并入当前编号块；前后都是无编号段时各自独立成块
    - 后处理：纯标题短块（<15 字，如"四、选课管理"、"国家奖学金"）并入下一块作前缀
    """
    blocks, cur = [], None

    for p in paras:
        if ZHANG.match(p):  # 章标题（如医疗规定的"第一章 …"）当作组头
            cur = {"label": p[:20], "texts": [p], "chapter": p[:20], "type": "group"}
            blocks.append(cur)
        elif GROUP_HEAD.match(p):  # 一、 / （一） → 强制开新块
            cur = {"label": make_label(p), "texts": [p], "chapter": None, "type": "group"}
            blocks.append(cur)
        elif DEEP_HEAD.match(p):  # （1） / ① → 深层从属项，总是并入当前块
            if cur is not None:
                cur["texts"].append(p)
            else:
                cur = {"label": make_label(p), "texts": [p], "chapter": None, "type": "sub"}
                blocks.append(cur)
        elif SUB_HEAD.match(p):  # 1. / 1、
            if (cur is not None and cur["type"] == "group"
                    and sum(len(t) for t in cur["texts"]) < MIN_MERGE):
                cur["texts"].append(p)
            else:
                cur = {"label": make_label(p), "texts": [p], "chapter": None, "type": "sub"}
                blocks.append(cur)
        else:  # 无编号段
            if cur is not None and cur["type"] in ("group", "sub"):
                cur["texts"].append(p)
            else:
                cur = {"label": "", "texts": [p], "chapter": None, "type": "plain"}
                blocks.append(cur)

    # 后处理：短块（纯节标题/条目标题）并入下一块
    merged = []
    for b in blocks:
        if merged and sum(len(t) for t in merged[-1]["texts"]) < 15:
            b["texts"] = merged[-1]["texts"] + b["texts"]
            merged.pop()
        merged.append(b)
    if len(merged) >= 2 and sum(len(t) for t in merged[-1]["texts"]) < 15:
        merged[-2]["texts"].extend(merged[-1]["texts"])
        merged.pop()

    chunks, chapter = [], None
    for b in merged:
        if b["chapter"]:
            chapter = b["chapter"]
        text = "".join(b["texts"])
        for piece in split_long(text):
            chunks.append({
                "text": piece,
                "doc": doc, "chapter": chapter,
                "article": b["label"] or None, "source": source,
            })
    return chunks


def chunk_file(path: Path):
    lines = path.read_text(encoding="utf-8").splitlines()
    title = lines[0][len("TITLE:"):].strip() if lines and lines[0].startswith("TITLE:") else path.stem
    paras = [l.strip() for l in lines[1:] if l.strip()]

    tiao_count = sum(1 for p in paras if TIAO.match(p))
    if tiao_count >= 5:
        chunks = chunk_by_tiao(paras, title, path.name)
        mode = f"条款体({tiao_count}条)"
    else:
        chunks = chunk_by_items(paras, title, path.name)
        mode = "编号体"
    return chunks, mode


def main():
    files = sorted(CLEAN.glob("*.txt"))
    if not files:
        print(f"未找到清洗后文件，请先运行 python scripts/clean.py")
        return

    all_chunks = []
    print(f"{'文件':<55} 模式        块数")
    print("-" * 80)
    for path in files:
        chunks, mode = chunk_file(path)
        all_chunks.extend(chunks)
        sizes = [len(c["text"]) for c in chunks] or [0]
        print(f"{path.name[:52]:<55}{mode:<10}  {len(chunks):>4}  "
              f"(均{int(sum(sizes)/len(sizes))}字, 最长{max(sizes)}字)")

    with OUT.open("w", encoding="utf-8") as f:
        for c in all_chunks:
            f.write(json.dumps(c, ensure_ascii=False) + "\n")

    print("-" * 80)
    print(f"共 {len(files)} 个文档 → {len(all_chunks)} 个 chunk，输出: {OUT}")

    # 抽样展示
    print("\n== 抽样（第 5 / 中间 / 最后一个 chunk）==")
    for i in [4, len(all_chunks) // 2, len(all_chunks) - 1]:
        c = all_chunks[i]
        print(f"\n[{i}] 《{c['doc']}》 {c['chapter'] or ''} {c['article'] or ''}")
        print(f"    {c['text'][:100]}{'...' if len(c['text']) > 100 else ''}")


if __name__ == "__main__":
    main()
