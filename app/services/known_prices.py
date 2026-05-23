"""Fireworks 主流模型已知价格表（人工维护）。

来源：https://fireworks.ai/pricing（截至 2026-05 公开价格）
单位：USD per 1,000,000 tokens

为什么需要：Fireworks 的 /inference/v1/models 端点（OpenAI 兼容标准接口）
不返回价格信息，价格只在网页上。LiteLLM、one-api、new-api 这些项目都是同
样的做法 — 维护一份本地价格表。

数据更新：当 Fireworks 调整定价时，手动更新此文件后重新跑 /admin/models/sync。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ModelPrice:
    input_per_1m: float
    output_per_1m: float
    cached_input_per_1m: float = 0.0
    note: str = ""


# 价格表 — key 是模型 ID 的尾段（即 public_name）
# 用模糊匹配：包含此 key 的字符串都命中
KNOWN_PRICES: dict[str, ModelPrice] = {
    # ========== DeepSeek ==========
    "deepseek-v4-pro":      ModelPrice(1.74, 3.48, 0.174, note="DeepSeek V4 Pro"),
    "deepseek-v4-flash":    ModelPrice(0.14, 0.28, 0.014, note="DeepSeek V4 Flash"),
    "deepseek-r1":          ModelPrice(0.55, 2.19, note="DeepSeek R1"),
    "deepseek-v3":          ModelPrice(0.90, 0.90, note="DeepSeek V3 0324"),

    # ========== Kimi (Moonshot) ==========
    "kimi-k2p6":            ModelPrice(0.55, 2.20, note="Kimi K2.6"),
    "kimi-k2p5":            ModelPrice(0.55, 2.20, note="Kimi K2.5"),
    "kimi-k2":              ModelPrice(0.55, 2.20, note="Kimi K2"),

    # ========== GLM (智谱) ==========
    "glm-5p1":              ModelPrice(0.20, 0.80, note="GLM-5.1"),
    "glm-4p5":              ModelPrice(0.20, 0.80, note="GLM-4.5"),
    "glm-4-32b":            ModelPrice(0.18, 0.55, note="GLM-4 32B"),

    # ========== Qwen ==========
    "qwen3-235b":           ModelPrice(0.22, 0.88, note="Qwen3 235B"),
    "qwen3-32b":            ModelPrice(0.13, 0.39, note="Qwen3 32B"),
    "qwen3-30b":            ModelPrice(0.10, 0.30, note="Qwen3 30B"),
    "qwen3-0-6b":           ModelPrice(0.02, 0.06, note="Qwen3 0.6B"),
    "qwen3-coder":          ModelPrice(0.20, 0.80, note="Qwen3 Coder"),
    "qwen3-embedding-8b":   ModelPrice(0.008, 0.008, note="Qwen3 Embedding 8B"),
    "qwq-32b":              ModelPrice(0.20, 0.80, note="QwQ 32B"),

    # ========== OpenAI OSS ==========
    "gpt-oss-120b":         ModelPrice(0.15, 0.60, note="GPT-OSS 120B"),
    "gpt-oss-20b":          ModelPrice(0.07, 0.30, note="GPT-OSS 20B"),

    # ========== Llama ==========
    "llama-v3p3-70b":       ModelPrice(0.90, 0.90, note="Llama 3.3 70B"),
    "llama-v3p1-405b":      ModelPrice(3.00, 3.00, note="Llama 3.1 405B"),
    "llama-v3p1-70b":       ModelPrice(0.90, 0.90, note="Llama 3.1 70B"),
    "llama-v3p1-8b":        ModelPrice(0.20, 0.20, note="Llama 3.1 8B"),
    "llama4-maverick":      ModelPrice(0.22, 0.88, note="Llama 4 Maverick 400B"),
    "llama4-scout":         ModelPrice(0.15, 0.60, note="Llama 4 Scout 109B"),

    # ========== MiniMax ==========
    "minimax-m2":           ModelPrice(0.30, 1.20, note="MiniMax M2"),

    # ========== Mistral ==========
    "mistral-small-24b":    ModelPrice(0.20, 0.60, note="Mistral Small 24B"),
    "mixtral-8x22b":        ModelPrice(1.20, 1.20, note="Mixtral 8x22B"),
    "mixtral-8x7b":         ModelPrice(0.50, 0.50, note="Mixtral 8x7B"),

    # ========== 图像（按张计费，但本系统按 token 计价时记 0） ==========
    "flux-1-dev":           ModelPrice(0, 0, note="FLUX.1 Dev (按张 $0.0025/step)"),
    "flux-1-schnell":       ModelPrice(0, 0, note="FLUX.1 Schnell (按张 $0.0005/step)"),
    "flux-kontext-pro":     ModelPrice(0, 0, note="FLUX.1 Kontext Pro (按张 $0.04)"),
    "flux-kontext-max":     ModelPrice(0, 0, note="FLUX.1 Kontext Max (按张 $0.08)"),
    "sdxl":                 ModelPrice(0, 0, note="SDXL (按张 $0.0005/step)"),
    "stable-diffusion-3":   ModelPrice(0, 0, note="SD 3.5 Large (按张 $0.04)"),

    # ========== Embeddings ==========
    "nomic-embed":          ModelPrice(0.008, 0, note="Nomic Embed"),
    "bge-large":            ModelPrice(0.008, 0, note="BGE Large EN"),
    "bge-m3":               ModelPrice(0.008, 0, note="BGE M3"),
}


def lookup_price(model_path_or_name: str) -> ModelPrice | None:
    """模糊匹配：模型路径或 public_name 命中 KNOWN_PRICES 任一 key 即返回。

    匹配策略：把 model_path_or_name 转小写后查找；先精确匹配，再前缀/包含匹配。
    """
    if not model_path_or_name:
        return None
    # 取最后一段（去掉 accounts/fireworks/models/ 前缀）
    name = model_path_or_name.rsplit("/", 1)[-1].lower()

    # 精确匹配
    if name in KNOWN_PRICES:
        return KNOWN_PRICES[name]

    # 包含匹配（model 名带版本后缀的情况，如 qwen3-32b-instruct）
    for key, price in KNOWN_PRICES.items():
        if key in name or name in key:
            return price

    return None
