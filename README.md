# 校园规章问答助手

基于 RAG 的校务规章问答系统：**宁可拒答，不可编造**。学生提问秒级返回带条款级引用的准确答案；知识库外的问题直接拒答并引导转人工，而不是编一个答案。

技术栈：Python · Qwen3-8B（生成）· Qwen3-Embedding-8B（向量化）· SiliconFlow API · starlette

## 评测结果（50 题分层评测集）

| 指标 | 数值 | 说明 |
|---|---|---|
| 陷阱题编造率 | **0.0%** | 红线指标，15 道知识库外问题全部拒答 |
| 答案正确率 | 97.1% | 端到端，唯一失败为设计内已知误拒 |
| 检索命中率 | 100% | 标准出处进入 top-6 |
| 引用准确率 | 100% | 回答末尾《来源》与标准出处一致 |
| 真题误拒率 | 2.9% | 阈值校准的已知取舍，报告留痕 |

评测集构成：直接可答 25 + 跨文档综合 10 + 陷阱 15，详见 [data/eval_report.md](data/eval_report.md)。

## 架构

```
raw 文件 → 清洗/匿名化 → 条款级切分 → 向量化(4096维)
                                          ↓
用户问题 → 混合检索(0.65语义+0.35词面) → 阈值判流(0.60) ─┬─ 不过线 → 硬拒答(零LLM调用)
                                                        └─ 过线 → top-6 拼上下文 → Qwen3-8B 生成
                                                                  └─ 资料不足 → 软拒答(prompt约束)
```

## 关键设计

- **双层拒答**：硬拒答（检索置信度低于阈值，不调 LLM）+ 软拒答（prompt 约束模型自评无依据），陷阱题全部在第一层拦截；
- **阈值校准**：实测陷阱题分数 0.412~0.597、真题最低 0.581，两者重叠无完美分割线；曾取 0.55 导致陷阱题编造（拿宿舍关门时间冒充闭馆时间），按"编造率 > 覆盖率"回滚 0.60，1 道误拒换 0% 编造，决策过程完整留痕；
- **混合检索**：字符 bigram 词面分专救"数字/条款号型"问题的语义漂移；同文档最多占 3 席保障跨文档题的多源覆盖；
- **语料治理**：多校来源文件统一匿名化（校名/简称/文号缩写/拼音网址），修复 PDF 压平表格与页码残留；
- **评测先行**：50 题评测集 + 7 项指标自动生成 Markdown 报告，语料/参数变更后全量回归。

## 快速开始

```bash
git clone https://github.com/fanyxbsad/campus-rag.git
cd campus-rag
python -m venv venv && venv\Scripts\activate      # Windows
pip install -r requirements.txt

# 配置 API 密钥（SiliconFlow https://siliconflow.cn）
echo SILICONFLOW_API_KEY=你的密钥 > .env

python scripts/embed_store.py   # 重新生成向量库 data/vectors.npy（首次必跑）
python app.py                   # 启动服务 http://127.0.0.1:8000
python rag.py --demo            # 或命令行跑内置测试题
python scripts/eval.py          # 全量评测，输出 data/eval_report.md
```

> 语料说明：`data/raw/` 为多校公开规章原文（含真实校名，未入库），故不上传；仓库内 `data/clean/` 为匿名化后语料，`data/chunks.jsonl` 为切分结果（946 切片）。

## 文档

- [PRD.md](PRD.md) — 产品需求文档（背景/指标体系/关键决策/风险/迭代规划）
- [data/eval_report.md](data/eval_report.md) — 50 题评测报告（逐题明细）

## 目录结构

```
campus-rag/
├── rag.py               # 核心链路：检索 → 判流 → 生成/拒答
├── app.py               # HTTP 服务 + 聊天前端
├── scripts/
│   ├── clean.py         # 清洗 + 匿名化 + 表格修复
│   ├── chunk.py         # 条款体/编号体自适应切分
│   ├── embed_store.py   # 向量化入库
│   └── eval.py          # 评测 + 报告生成
└── data/
    ├── clean/           # 匿名化语料（38 份）
    ├── chunks.jsonl     # 切片（946，含章节/条款元数据）
    ├── eval_questions.jsonl / eval_report.md / eval_results.jsonl
    └── ...
```
