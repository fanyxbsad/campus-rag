import os
from dotenv import load_dotenv
from openai import OpenAI

# 读取 .env 里的密钥（必须放在最前面）
load_dotenv()

# 所有调用都走这一个 client
client = OpenAI(
    api_key=os.getenv("SILICONFLOW_API_KEY"),
    base_url="https://api.siliconflow.cn/v1",   # 指向硅基流动
)

# ── 测试 1：对话模型（注意关闭 Qwen3 的思考模式）──
resp = client.chat.completions.create(
    model="Qwen/Qwen3-8B",
    messages=[{"role": "user", "content": "用一句话介绍你自己"}],
    extra_body={"enable_thinking": False},
)
print("【对话测试】", resp.choices[0].message.content)

# ── 测试 2：向量模型 ──
emb = client.embeddings.create(
    model="Qwen/Qwen3-Embedding-8B",
    input=["转专业需要什么条件"],
)
vec = emb.data[0].embedding
print("【嵌入测试】向量维度:", len(vec))
print("【嵌入测试】前 5 维:", [round(x, 4) for x in vec[:5]])