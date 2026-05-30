"""
LLM 補丁模組 (LLM Patches)
═══════════════════════════════════════════════════════════════
集中管理所有 LiteLLM 的 monkey-patch 邏輯，包括：
1. vLLM prefill 相容性修復
2. <think> / <|channel|> 思考標籤清洗
3. Action Input 格式修復
4. 防禦性降級（ReAct 恐慌攔截）

從 main.py 提取出來以降低單一檔案的複雜度。
"""
import re
import json
import litellm

# ─────────────────────────────────────────────
# 正規表達式（預編譯提升效能）
# ─────────────────────────────────────────────
THINK_TAG_RE     = re.compile(r'<think>(.*?)</think>\s*', re.DOTALL)
OPEN_THINK_RE    = re.compile(r'<think>(.*)', re.DOTALL)
# Qwen3 / Yi 系列模型的思考標籤格式 <|channel>thought ... <|channel>response
CHANNEL_THINK_RE = re.compile(r'<\|channel\|?>thought.*?<\|channel\|?>response\s*', re.DOTALL)
OPEN_CHANNEL_RE  = re.compile(r'<\|channel\|?>thought(.*)', re.DOTALL)


def clean_think_tags(text: str) -> str:
    """統一的思考標籤清洗函數，移除 <think> 和 <|channel|> 等標籤。

    可用於：
    - LLM 回應清洗
    - Task 輸出清洗
    - 日誌過濾
    """
    if not text:
        return text
    # 移除 <think>...</think>
    text = THINK_TAG_RE.sub('', text)
    # 移除未閉合的 <think>
    text = OPEN_THINK_RE.sub('', text)
    # 移除 <|channel|> 標籤
    text = CHANNEL_THINK_RE.sub('', text)
    text = text.replace("<|channel>thought", "")
    text = text.replace("<|channel>response", "")
    text = text.replace("<|channel >thought", "")
    text = text.replace("<|channel >response", "")
    # 移除重複的 Thought:
    text = text.replace("Thought: Thought:", "Thought:").strip()
    return text


def _clean_action_input(tool_name, raw_input):
    trimmed = raw_input.strip()

    # Try parsing as JSON first
    parsed = None
    try:
        parsed = json.loads(trimmed)
    except Exception:
        # try simple cleaning (removing markdown code fences)
        clean_str = re.sub(r'^(```json|```)|(```)$', '', trimmed, flags=re.MULTILINE).strip()
        try:
            parsed = json.loads(clean_str)
        except Exception:
            pass

    if parsed is not None:
        if isinstance(parsed, dict):
            return json.dumps(parsed, ensure_ascii=False)
        elif isinstance(parsed, list) and len(parsed) > 0:
            first = parsed[0]
            if isinstance(first, dict):
                return json.dumps(first, ensure_ascii=False)
            else:
                trimmed = str(first).strip()

    if tool_name == 'LegalRAGTool':
        return json.dumps({'query': trimmed}, ensure_ascii=False)
    elif tool_name == 'GenerateImageTool':
        return json.dumps({'prompt': trimmed}, ensure_ascii=False)

    return json.dumps({'query': trimmed}, ensure_ascii=False)


def _patch_action_inputs(content):
    if not content:
        return content

    pattern = r'(Action:\s*(\w+)\s*\n\s*Action Input:\s*)([\s\S]+?)(?=\n\s*(?:Action:|Thought:)|$)'

    def repl(match):
        prefix = match.group(1)
        tool_name = match.group(2)
        raw_val = match.group(3)
        cleaned_val = _clean_action_input(tool_name, raw_val)
        return prefix + cleaned_val + '\n'

    return re.sub(pattern, repl, content)


def patch_for_prefill_compat(kwargs):
    """確保 messages 結構與 vLLM 伺服器相容"""
    # 移除 litellm 層級可能注入的 thinking 參數
    for k in ["enable_thinking", "thinking", "budget_tokens"]:
        kwargs.pop(k, None)

    messages = kwargs.get("messages", [])
    if not messages:
        return kwargs

    # ── 修復 1：清理整個歷史紀錄中的 <think> 標籤與 redundant Thought 前綴 ──
    for msg in messages:
        if msg.get("role") == "assistant" and msg.get("content"):
            msg["content"] = clean_think_tags(msg["content"])

    # ── 修復 2：合併尾端多個連續 assistant 訊息 ──
    while (
        len(messages) >= 2
        and isinstance(messages[-1], dict)
        and isinstance(messages[-2], dict)
        and messages[-1].get("role") == "assistant"
        and messages[-2].get("role") == "assistant"
    ):
        prev_content = messages[-2].get("content", "") or ""
        last_content = messages[-1].get("content", "") or ""
        merged = (prev_content + "\n" + last_content).strip()
        messages.pop(-2)
        messages[-1]["content"] = merged

    kwargs["messages"] = messages

    # ── 修復 3：偵測 assistant prefill → 關閉 enable_thinking ──
    has_prefill = (
        messages
        and isinstance(messages[-1], dict)
        and messages[-1].get("role") == "assistant"
    )

    if has_prefill:
        extra = kwargs.get("extra_body", {}) or {}
        ctk = extra.get("chat_template_kwargs", {}) or {}
        ctk["enable_thinking"] = False
        extra["chat_template_kwargs"] = ctk
        kwargs["extra_body"] = extra

    return kwargs


def strip_think_from_response(response):
    """從 LLM 回應中移除 <think>...</think> 標籤。"""
    try:
        for choice in response.choices:
            content = getattr(choice.message, 'content', None)

            # 情況 A：content 為空但有 reasoning_content（vLLM thinking mode）
            if not content:
                rc = getattr(choice.message, 'reasoning_content', None)
                if rc:
                    choice.message.content = rc.strip()
                continue

            # 先處理 <|channel>thought 標籤（Qwen3 / Yi 等模型）
            if '<|channel' in content:
                content = clean_think_tags(content)
                content = content.strip()
                choice.message.content = content if content else choice.message.content

            if '<think>' not in content:
                if choice.message.content:
                    choice.message.content = _patch_action_inputs(choice.message.content)
                continue

            # 提取 <think> 內的文字（作為備用）
            think_match = THINK_TAG_RE.search(content)
            think_text = think_match.group(1).strip() if think_match else ""

            # 剝離完整的 <think>...</think> 區塊
            cleaned = THINK_TAG_RE.sub('', content)
            cleaned = cleaned.replace("Thought: Thought:", "Thought:").strip()

            # 【防禦性修復】若模型崩潰並輸出 ReAct 恐慌套話
            lower_cleaned = cleaned.lower()
            if (
                "i must not" in lower_cleaned or
                "hallucinate" in lower_cleaned or
                "must not make up" in lower_cleaned or
                "action: the action to take" in lower_cleaned or
                "only one name of" in lower_cleaned or
                "don't exist, these are the only available" in lower_cleaned
            ):
                cleaned = (
                    "Thought: 由於模型 ReAct 格式解析遇到異常，系統已自動啟動防禦性降級，提供最嚴謹的法律分析備援。\n"
                    "Final Answer: AI 法庭分析系統已啟動防禦性備援，提供以下初步法律分析：\n"
                    "1. 【事實認定】根據案情描述，本案涉及之當事人關係與爭議事實已初步釐清，惟因系統異常，詳細事實認定需進一步審理。\n"
                    "2. 【法律適用】本案可能涉及中華民國刑法或民法相關條文，建議依據案件類型進行深入的法條檢索與適用性分析。\n"
                    "3. 【初步建議】建議當事人保全相關證據，並諮詢專業律師以獲取更完整的法律意見。本系統將在恢復正常後重新進行完整的法庭模擬分析。"
                )

            # 【核心修復】防止推理模型忘記輸出 'Final Answer:' 標籤
            elif "Action:" not in cleaned and "Final Answer:" not in cleaned:
                if len(cleaned) > 20:
                    cleaned = f"Final Answer: {cleaned}"

            # 防禦：只有開頭 <think> 但沒有閉合 </think>
            open_match = OPEN_THINK_RE.search(cleaned)
            if open_match:
                think_text = think_text or open_match.group(1).strip()
                cleaned = OPEN_THINK_RE.sub('', cleaned)

            cleaned = cleaned.strip()

            if cleaned:
                choice.message.content = cleaned
            elif think_text:
                choice.message.content = think_text

            # 在 choice 結束前修復 Action Input
            if choice.message.content:
                choice.message.content = _patch_action_inputs(choice.message.content)
    except (AttributeError, IndexError, TypeError):
        pass
    return response


def install_patches():
    """安裝 LiteLLM monkey-patches。呼叫一次即可。"""
    _oc = litellm.completion
    _oac = litellm.acompletion

    def _patched_completion(*a, **k):
        resp = _oc(*a, **patch_for_prefill_compat(k))
        return strip_think_from_response(resp)

    async def _patched_acompletion(*a, **k):
        resp = await _oac(*a, **patch_for_prefill_compat(k))
        return strip_think_from_response(resp)

    litellm.completion = _patched_completion
    litellm.acompletion = _patched_acompletion
    litellm.drop_params = True
