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


# --------------------------------------------------------------------------- #
# $ref / $defs inline 解析
# --------------------------------------------------------------------------- #
#
# 背景：Fireworks 服务端 schema resolver 解析 `$ref: '#/$defs/Name'` 时容易出
# `AttributeError("'NoneType' object has no attribute 'lookup'")` —— 它把 tool
# parameters 单独取出来时丢了 $defs 表，或只认 draft-07 的 `definitions` 不认
# 2019-09+ 的 `$defs`。网关层把所有 $ref 提前展开，上游就看到扁平 schema，
# 用不上它有问题的 resolver。
#
# 处理边界：
#   - 只解析 local pointer `#/$defs/X` 和 `#/definitions/X`
#   - 外部 ref（http://... 或非 # 开头）保留不动
#   - 循环引用（A→B→A）降级为 {}，避免无限展开
#   - 悬空引用（target 不存在）降级为 {}，免得上游同样的 resolver bug 复现
#   - $ref 同级的兄弟字段保留（modern spec 允许，旧版按 $ref 单独子句处理）

_LOCAL_REF_PREFIXES = ("#/$defs/", "#/definitions/")


def inline_refs(schema: Any) -> tuple[Any, list[str]]:
    """把 schema 中所有 local `$ref` 内联展开。

    Args:
        schema: dict — JSON Schema 根（通常是 `tools[].function.parameters`）

    Returns:
        (new_schema, notes) — new_schema 是新对象（不修改入参），notes 收集
        循环/悬空/无法解析的 ref 描述，给调用方打日志。
    """
    notes: list[str] = []
    if not isinstance(schema, dict):
        return schema, notes

    # 收集 definitions 表（$defs + 老 definitions 合并；$defs 优先）
    defs = {}
    raw_defs = schema.get("definitions")
    if isinstance(raw_defs, dict):
        defs.update(raw_defs)
    raw_new_defs = schema.get("$defs")
    if isinstance(raw_new_defs, dict):
        defs.update(raw_new_defs)

    # 没 ref 也没 defs 就直接返回（深拷贝避免上层 in-place 改）
    # 但还是要走一遍 _resolve 以防 $defs 内部嵌 ref；快速通道只在两个表都空时跳过
    if not defs and not _contains_ref(schema):
        return schema, notes

    def _resolve(node: Any, stack: tuple[str, ...]) -> Any:
        if isinstance(node, dict):
            ref = node.get("$ref")
            if isinstance(ref, str):
                target_name = _local_ref_name(ref)
                if target_name is None:
                    # 外部 ref / 不支持格式 — 原样保留
                    return {k: _resolve(v, stack) for k, v in node.items()}
                if target_name in stack:
                    notes.append(f"circular ref '#/$defs/{target_name}' → empty")
                    siblings = {k: _resolve(v, stack) for k, v in node.items() if k != "$ref"}
                    return siblings or {}
                target = defs.get(target_name)
                if target is None:
                    notes.append(f"dangling ref '{ref}' → empty")
                    siblings = {k: _resolve(v, stack) for k, v in node.items() if k != "$ref"}
                    return siblings or {}
                # 递归展开目标（带 cycle 检测）
                resolved = _resolve(target, stack + (target_name,))
                if not isinstance(resolved, dict):
                    return resolved
                # 同级兄弟字段优先级覆盖 ref 解析出来的（按 JSON Schema 2019+ 语义）
                siblings = {k: _resolve(v, stack) for k, v in node.items() if k != "$ref"}
                return {**resolved, **siblings}
            return {k: _resolve(v, stack) for k, v in node.items() if k not in ("$defs", "definitions")}
        if isinstance(node, list):
            return [_resolve(item, stack) for item in node]
        return node

    new_schema = _resolve(schema, stack=())
    return new_schema, notes


def _local_ref_name(ref: str) -> str | None:
    for prefix in _LOCAL_REF_PREFIXES:
        if ref.startswith(prefix):
            return ref[len(prefix):]
    return None


def _contains_ref(node: Any) -> bool:
    """快速预扫：node 子树是否含 $ref。避免没 ref 时还跑递归。"""
    if isinstance(node, dict):
        if "$ref" in node:
            return True
        return any(_contains_ref(v) for v in node.values())
    if isinstance(node, list):
        return any(_contains_ref(item) for item in node)
    return False


def sanitize_openai_tools(tools: Any) -> tuple[Any, list[str]]:
    """走 OpenAI `tools` 数组 → 每个 `function.parameters` 跑 schema 消毒。

    消毒包括：
      1. inline_refs：展开 $ref / $defs（Fireworks resolver bug 兜底）
      2. sanitize_patterns：剥 lookahead/lookbehind/backref（RE2 不支持）

    返回 (tools, notes)。tools 是原对象（in-place 替换 parameters）。
    """
    notes: list[str] = []
    if not isinstance(tools, list):
        return tools, notes

    def _process_params_holder(holder: dict, key: str) -> None:
        params = holder.get(key)
        if not isinstance(params, dict):
            return
        # 1. 先 inline ref（先后顺序：ref 展开 → pattern 消毒）
        new_params, ref_notes = inline_refs(params)
        if ref_notes:
            notes.extend(ref_notes)
        if new_params is not params:
            holder[key] = new_params
            params = new_params
        # 2. 再扫 pattern
        sanitize_patterns(params, notes)

    for t in tools:
        if not isinstance(t, dict):
            continue
        fn = t.get("function")
        if isinstance(fn, dict):
            _process_params_holder(fn, "parameters")
        # OpenAI 旧版 function schema（顶层 `function` 旁的 `parameters`）也兼容
        if "parameters" in t and not isinstance(t.get("function"), dict):
            _process_params_holder(t, "parameters")
    return tools, notes


__all__ = [
    "detect_unsupported_regex",
    "sanitize_patterns",
    "inline_refs",
    "sanitize_openai_tools",
]
