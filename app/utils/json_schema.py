"""JSON Schema 消毒：剥掉 Fireworks 上游 RE2 引擎不支持的正则特性。

背景
====
Fireworks 的 tool calling 校验器（多半基于 Go `regexp` / RE2）为了线性时间复杂度，
不支持以下 PCRE 特性：

  - lookaround:   (?=...) (?!...) (?<=...) (?<!...)
  - backreference: \\1 \\2 … \\9

而 Anthropic / OpenAI 客户端常用 PCRE 引擎，工具 schema 里塞这种正则很常见
（典型例子：DNS hostname 校验用 `(?=.{1,253}$)` 限定总长）。

我们的 gateway 之前原样转发 `tools[].function.parameters`，结果上游 400：
    "Regex lookahead (?=...) is not supported in JSON Schema pattern: '...'"

策略
====
对所有进入网关的工具 schema，递归扫所有嵌套的 `pattern` 字段；只要发现不兼容
特性就**丢掉这个 pattern**（schema 其余约束保留），并把丢掉的列表返回供日志。
这样请求能走通；LLM 输出的工具参数失去额外正则校验，但 description 仍能引导
模型输出合理值，而且客户端拿到结果后无论如何都该自己再 validate。

故意保守的设计：宁可漏检（保留一些上游 OK 的 pattern）也不要误杀（剥掉
上游能消化的）。所以我们只匹配明确无支持的 token。
"""

from __future__ import annotations

import re
from typing import Any

# RE2 / Go regexp 不支持的语法 token（substring 匹配即可，这些转义在普通 pattern
# 里几乎不可能"恰好"组合出来，误判概率极低）。
_UNSUPPORTED_LOOKAROUND_TOKENS = ("(?=", "(?!", "(?<=", "(?<!")

# 反向引用 \1 .. \9 — 不在字符类内部时才算反向引用，但字符类里出现 \\N 本身就
# 是 Python regex 中的非法语法（必须用 [0-9]），所以这里粗匹配也行。
_BACKREF_RE = re.compile(r"\\[1-9]")


def detect_unsupported_regex(pattern: Any) -> str | None:
    """检查 pattern 是否含 RE2 不支持的特性。

    返回原因字符串（如 "lookaround '(?='"）；None 表示安全。
    """
    if not isinstance(pattern, str) or not pattern:
        return None
    for tok in _UNSUPPORTED_LOOKAROUND_TOKENS:
        if tok in pattern:
            return f"lookaround '{tok}'"
    if _BACKREF_RE.search(pattern):
        return r"backreference '\N'"
    return None


def sanitize_patterns(schema: Any, stripped: list[str] | None = None) -> list[str]:
    """**原地**递归走 JSON Schema，丢掉所有不兼容的 `pattern` 字段。

    Args:
        schema: dict | list | scalar — 通常是 JSON Schema 根 (dict)。
        stripped: 收集被剥掉的描述信息，便于上层日志。None 表示新建一个内部 list。

    Returns:
        被剥掉的描述列表（同 stripped 参数；用 return 是为了让调用方一行拿到）。
    """
    if stripped is None:
        stripped = []
    _walk(schema, stripped)
    return stripped


def _walk(node: Any, stripped: list[str]) -> None:
    if isinstance(node, dict):
        # 优先处理本节点 pattern（在 properties.X 这层、items 这层等都可能出现）
        p = node.get("pattern")
        if p is not None:
            reason = detect_unsupported_regex(p)
            if reason:
                preview = str(p)[:120]
                stripped.append(f"{reason}: {preview}")
                node.pop("pattern", None)
        # patternProperties 的 key 本身就是 regex，也可能 lookaround
        pp = node.get("patternProperties")
        if isinstance(pp, dict):
            bad_keys = [k for k in pp if detect_unsupported_regex(k)]
            for k in bad_keys:
                stripped.append(f"patternProperties key '{k[:80]}'")
                # 整个 entry 删掉（key 不能改，schema 语义就丢了）
                pp.pop(k, None)
            if not pp:
                node.pop("patternProperties", None)
        # 递归所有值
        for v in node.values():
            _walk(v, stripped)
    elif isinstance(node, list):
        for item in node:
            _walk(item, stripped)


def sanitize_openai_tools(tools: Any) -> tuple[Any, list[str]]:
    """走 OpenAI `tools` 数组 → 每个 `function.parameters` 跑 sanitize_patterns。

    返回 (tools, stripped)。tools 是原对象（in-place 修改）。
    """
    stripped: list[str] = []
    if not isinstance(tools, list):
        return tools, stripped
    for t in tools:
        if not isinstance(t, dict):
            continue
        fn = t.get("function")
        if isinstance(fn, dict):
            params = fn.get("parameters")
            if isinstance(params, dict):
                sanitize_patterns(params, stripped)
        # OpenAI 旧版 function schema（顶层 `function` 旁的 `parameters`）也兼容
        params2 = t.get("parameters")
        if isinstance(params2, dict):
            sanitize_patterns(params2, stripped)
    return tools, stripped


__all__ = [
    "detect_unsupported_regex",
    "sanitize_patterns",
    "sanitize_openai_tools",
]
