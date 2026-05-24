"""JSON Schema 消毒：把上游不能消化的 schema 特性提前清洗掉。

Fireworks（以及大多数 RE2 系 / Go regexp）和 Anthropic / OpenAI 客户端常用的
PCRE 引擎能力不同，导致下面这些客户端合法的 schema 在上游会爆：

  - lookaround:        (?=)  (?!)  (?<=)  (?<!)
  - backreference:     \\1 .. \\9
  - atomic group:      (?>...)
  - possessive qty:    *+  ++  ?+
  - inline comment:    (?#...)

另外 Fireworks 的 `$ref` resolver 在解析 `#/$defs/...` 时会出 NoneType.lookup
的服务端 bug（resolver 没拿到 definitions 表），所以本模块也负责把所有 local
`$ref` 提前 inline 展开。

设计原则
========
* 单次递归遍历同时处理 `$ref` 展开和 `pattern` 检查 — 大 schema 不走两遍
* 输出新对象（不破坏原 schema，方便上层 retry / fallback）
* 遇到无法处理的（外部 ref、复杂自引用）保守保留，宁可漏检不要误杀
* notes 带 category 信息，便于 metrics 聚合分类
"""

from __future__ import annotations

import re
from typing import Any

# --------------------------------------------------------------------------- #
# 不支持的正则特性检测
# --------------------------------------------------------------------------- #

# Substring 匹配即可 — 这些 token 在普通 pattern 里几乎不可能"恰好"组合出来
# （需要的 escape 都被 `\` 前缀阻断），误判率极低。
_UNSUPPORTED_TOKENS: dict[str, tuple[str, str]] = {
    # token : (category, human_reason)
    "(?=":  ("lookaround", "lookahead '(?='"),
    "(?!":  ("lookaround", "negative lookahead '(?!'"),
    "(?<=": ("lookaround", "lookbehind '(?<='"),
    "(?<!": ("lookaround", "negative lookbehind '(?<!'"),
    "(?>":  ("atomic_group", "atomic group '(?>'"),
    "(?#":  ("comment", "inline comment '(?#'"),
}

# \1 .. \9 反向引用 — 在字符类 [...] 里它本身就是非法语法（必须写 [0-9]），
# 所以粗粒度匹配安全。
_BACKREF_RE = re.compile(r"\\[1-9]")

# 占有量词：*+ ++ ?+。技术上 {n,m}+ 也是，但极少见，忽略以避免和 {n,m} 的
# 普通 quantifier 重叠 false-positive。
_POSSESSIVE_QUANTIFIER_RE = re.compile(r"[*+?]\+")


def detect_unsupported_regex(pattern: Any) -> tuple[str, str] | None:
    """检查 pattern 是否含 RE2 不支持的特性。

    返回 (category, reason) 元组；None 表示安全。
    category 用于 metrics 聚合（lookaround / atomic_group / comment / backref /
    possessive_quantifier）。
    """
    if not isinstance(pattern, str) or not pattern:
        return None
    for tok, (cat, reason) in _UNSUPPORTED_TOKENS.items():
        if tok in pattern:
            return cat, reason
    if _BACKREF_RE.search(pattern):
        return "backref", r"backreference '\N'"
    if _POSSESSIVE_QUANTIFIER_RE.search(pattern):
        return "possessive_quantifier", "possessive quantifier '*+/++/?+'"
    return None


# --------------------------------------------------------------------------- #
# $ref / $defs inline 解析
# --------------------------------------------------------------------------- #
#
# 单次递归同时处理 $ref 展开 + pattern 消毒，避免两遍 walk 浪费 CPU。
# 返回 (new_schema, notes)，notes 每项形如 "category: detail" 便于解析归类。

_LOCAL_REF_PREFIXES = ("#/$defs/", "#/definitions/")


def _local_ref_name(ref: str) -> str | None:
    for prefix in _LOCAL_REF_PREFIXES:
        if ref.startswith(prefix):
            return ref[len(prefix):]
    return None


def _contains_anything_to_sanitize(node: Any) -> bool:
    """快速预扫：node 子树是否有任何需要处理的东西（$ref / pattern / $defs）。

    没有就走 fast-path 直接返回原对象，避免递归构造新 dict。
    """
    if isinstance(node, dict):
        if "$ref" in node or "$defs" in node or "definitions" in node:
            return True
        if "pattern" in node or "patternProperties" in node:
            return True
        return any(_contains_anything_to_sanitize(v) for v in node.values())
    if isinstance(node, list):
        return any(_contains_anything_to_sanitize(item) for item in node)
    return False


def sanitize_schema(schema: Any) -> tuple[Any, list[str]]:
    """**核心 API**：对一个 JSON Schema 做完整消毒。

    单次递归同时：
      1. 展开所有 local `$ref`（#/$defs/X 和 #/definitions/X）
      2. 剥掉 RE2 不支持的 `pattern` 字段
      3. 清理 `patternProperties` 中 key 含不支持特性的 entry
      4. 处理循环引用（降级 {}）和悬空引用（降级 {}）

    输入：通常是 `tools[].function.parameters` 或 `response_format.json_schema.schema`
    输出：(new_schema, notes)。new_schema 是新对象（不修改入参）。

    notes 每项形如 `"ref_inline: SelectedScreenInstance"` /
    `"lookaround: lookahead '(?='"` / `"ref_circular: A"` 等，
    第一段是 category（给 metrics 聚合用），冒号后是详情。
    """
    notes: list[str] = []
    if not isinstance(schema, dict):
        return schema, notes
    if not _contains_anything_to_sanitize(schema):
        return schema, notes

    # 收集顶层 $defs / definitions 表
    defs: dict[str, Any] = {}
    raw_defs = schema.get("definitions")
    if isinstance(raw_defs, dict):
        defs.update(raw_defs)
    raw_new_defs = schema.get("$defs")
    if isinstance(raw_new_defs, dict):
        defs.update(raw_new_defs)

    new_schema = _walk(schema, defs, notes, stack=())
    return new_schema, notes


def _walk(node: Any, defs: dict, notes: list[str], stack: tuple[str, ...]) -> Any:
    if isinstance(node, dict):
        # 1) $ref 展开
        ref = node.get("$ref")
        if isinstance(ref, str):
            target_name = _local_ref_name(ref)
            if target_name is None:
                # 外部 ref / 不支持格式 — 原样保留，但递归其它字段
                return {k: _walk(v, defs, notes, stack)
                        for k, v in node.items() if k not in ("$defs", "definitions")}
            if target_name in stack:
                notes.append(f"ref_circular: {target_name}")
                siblings = {k: _walk(v, defs, notes, stack)
                            for k, v in node.items()
                            if k not in ("$ref", "$defs", "definitions")}
                return siblings or {}
            target = defs.get(target_name)
            if target is None:
                notes.append(f"ref_dangling: {ref}")
                siblings = {k: _walk(v, defs, notes, stack)
                            for k, v in node.items()
                            if k not in ("$ref", "$defs", "definitions")}
                return siblings or {}
            # 成功展开
            notes.append(f"ref_inline: {target_name}")
            resolved = _walk(target, defs, notes, stack + (target_name,))
            siblings = {k: _walk(v, defs, notes, stack)
                        for k, v in node.items()
                        if k not in ("$ref", "$defs", "definitions")}
            if isinstance(resolved, dict):
                return {**resolved, **siblings}
            return resolved

        # 2) 普通字典节点：构造新 dict，过滤 $defs/definitions，处理 pattern / patternProperties
        out: dict[str, Any] = {}
        for k, v in node.items():
            if k in ("$defs", "definitions"):
                continue  # 顶层和嵌套 $defs 都干掉
            if k == "pattern" and isinstance(v, str):
                detection = detect_unsupported_regex(v)
                if detection is not None:
                    cat, reason = detection
                    preview = v[:120]
                    notes.append(f"{cat}: {reason} — {preview}")
                    continue  # 跳过这个 pattern，相当于丢字段
            if k == "patternProperties" and isinstance(v, dict):
                cleaned_pp: dict[str, Any] = {}
                for pp_key, pp_val in v.items():
                    if isinstance(pp_key, str) and detect_unsupported_regex(pp_key):
                        notes.append(f"pattern_properties_key: '{pp_key[:80]}'")
                        continue
                    cleaned_pp[pp_key] = _walk(pp_val, defs, notes, stack)
                if cleaned_pp:
                    out[k] = cleaned_pp
                continue
            out[k] = _walk(v, defs, notes, stack)
        return out
    if isinstance(node, list):
        return [_walk(item, defs, notes, stack) for item in node]
    return node


# --------------------------------------------------------------------------- #
# 兼容性 API：保留旧函数名（外部测试 / 老代码可能直接调）
# --------------------------------------------------------------------------- #


def inline_refs(schema: Any) -> tuple[Any, list[str]]:
    """旧 API — 现在转调 sanitize_schema。

    历史上 inline_refs 只做 ref 展开不做 pattern 消毒；现在统一到 sanitize_schema
    一次性都做。如果你只想要 ref 展开行为，请直接用 sanitize_schema。
    """
    return sanitize_schema(schema)


def sanitize_patterns(schema: Any, stripped: list[str] | None = None) -> list[str]:
    """旧 API — 原地剥 unsupported pattern。保留供向后兼容。

    新代码请用 sanitize_schema（返回新对象更安全 + 同时处理 $ref）。
    """
    if stripped is None:
        stripped = []
    if not isinstance(schema, dict) and not isinstance(schema, list):
        return stripped
    _strip_patterns_inplace(schema, stripped)
    return stripped


def _strip_patterns_inplace(node: Any, notes: list[str]) -> None:
    if isinstance(node, dict):
        p = node.get("pattern")
        if isinstance(p, str):
            det = detect_unsupported_regex(p)
            if det is not None:
                cat, reason = det
                notes.append(f"{cat}: {reason} — {p[:120]}")
                node.pop("pattern", None)
        pp = node.get("patternProperties")
        if isinstance(pp, dict):
            bad = [k for k in pp if isinstance(k, str) and detect_unsupported_regex(k)]
            for k in bad:
                notes.append(f"pattern_properties_key: '{k[:80]}'")
                pp.pop(k, None)
            if not pp:
                node.pop("patternProperties", None)
        for v in node.values():
            _strip_patterns_inplace(v, notes)
    elif isinstance(node, list):
        for item in node:
            _strip_patterns_inplace(item, notes)


# --------------------------------------------------------------------------- #
# 高层接入点
# --------------------------------------------------------------------------- #


def sanitize_openai_tools(tools: Any) -> tuple[Any, list[str]]:
    """OpenAI tools 数组消毒：每个 function.parameters 跑一遍 sanitize_schema。

    返回 (tools, notes)。tools 是原对象（替换 parameters 字段）。
    """
    notes: list[str] = []
    if not isinstance(tools, list):
        return tools, notes

    def _process(holder: dict, key: str) -> None:
        params = holder.get(key)
        if not isinstance(params, dict):
            return
        new_params, sub_notes = sanitize_schema(params)
        if sub_notes:
            notes.extend(sub_notes)
        if new_params is not params:
            holder[key] = new_params

    for t in tools:
        if not isinstance(t, dict):
            continue
        fn = t.get("function")
        if isinstance(fn, dict):
            _process(fn, "parameters")
        elif "parameters" in t:
            # 老版 OpenAI function schema
            _process(t, "parameters")
    return tools, notes


def sanitize_response_format(body: dict) -> list[str]:
    """OpenAI structured outputs：消毒 `response_format.json_schema.schema`。

    形态：
      {"response_format": {"type": "json_schema", "json_schema": {"schema": {...}}}}

    或 Anthropic 客户端发到 OpenAI 路径的偶发别名（少见，保险起见也处理）。

    返回 notes（in-place 替换 schema 字段）。
    """
    notes: list[str] = []
    if not isinstance(body, dict):
        return notes
    rf = body.get("response_format")
    if not isinstance(rf, dict):
        return notes
    js = rf.get("json_schema")
    if not isinstance(js, dict):
        return notes
    schema = js.get("schema")
    if not isinstance(schema, dict):
        return notes
    new_schema, sub_notes = sanitize_schema(schema)
    if sub_notes:
        notes.extend(sub_notes)
    if new_schema is not schema:
        js["schema"] = new_schema
    return notes


# --------------------------------------------------------------------------- #
# Notes → metrics category 聚合
# --------------------------------------------------------------------------- #


def aggregate_categories(notes: list[str]) -> dict[str, int]:
    """把 sanitize_* 函数返回的 notes 列表按 category 聚合成计数器。

    格式：'category: detail' → {category: count}
    用于日志 extra 字段 + admin stats 端点 + Dashboard 展示。
    """
    counts: dict[str, int] = {}
    for n in notes:
        cat = n.split(":", 1)[0].strip() if ":" in n else "other"
        counts[cat] = counts.get(cat, 0) + 1
    return counts


__all__ = [
    "detect_unsupported_regex",
    "sanitize_schema",
    "sanitize_openai_tools",
    "sanitize_response_format",
    "aggregate_categories",
    # 兼容旧 API
    "inline_refs",
    "sanitize_patterns",
]
