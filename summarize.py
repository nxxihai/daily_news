"""AI 提炼层：用 LLM 筛选最有价值的新闻并重写为短摘要。

- 支持多个 LLM Provider 按顺序尝试（OpenRouter 免费模型 / 本地 Ollama / DeepSeek），
  第一个成功即启用，全部失败则退回纯规则筛选。
- 输出固定 markdown 格式：
    【国际】
    1. **标题**：摘要
    【国内】
    ...
    【热点】
    ...
"""

import re

import requests

from config import LLM_PROVIDERS, NEWS_PER_LINE, NEWS_PER_SECTION

SECTIONS = ["国际", "国内", "热点"]

# 采集源的分类 → 早报三栏的映射（规则版用）
CATEGORY_MAP = {
    "国际": "国际", "国际要闻": "国际", "军事": "国际",
    "国内": "国内", "国内要闻": "国内", "财经": "国内",
    "财经要闻": "国内", "科技": "国内", "社会": "国内",
    "热点": "热点",
}


def build_prompt(news_items, per_section=None, per_line=None):
    per_section = per_section or NEWS_PER_SECTION
    per_line = per_line or NEWS_PER_LINE
    # 免费小模型上下文和输出能力有限，避免 90+ 条过载导致输出模板或截断
    news_items = news_items[:40]
    candidate = "\n".join(
        f"{i + 1}. 【{n['category']}】{n['title']}"
        f"{'｜' + n['summary'][:60] if n.get('summary') else ''}"
        f"｜来源：{n['source']}"
        for i, n in enumerate(news_items)
    )
    return f"""你是资深新闻编辑。以下是今日从权威渠道采集的候选新闻（含标题、简介、来源）。

候选新闻：
{candidate}

请从中选出最有公共价值和信息量的 {per_section * 3} 条新闻（国际 {per_section} 条、国内 {per_section} 条、热点 {per_section} 条），并遵守：
1. 排除娱乐花边、猎奇、标题党、低信息量内容；同一事件只保留一条。
2. 每条重写为 {per_line} 字左右的摘要：一句话核心事实 + 一句背景或影响，不注水、不评论。
3. 国际指外国及中国对外事务；国内指中国内政民生；热点指全网热议话题。
4. 必须使用简体中文；严格按下面 markdown 格式输出，只输出正式内容，不要输出任何解释、思考过程、前后缀：

【国际】
1. **真实新闻标题**：60字左右的摘要（一句话核心事实 + 一句背景或影响）
2. **真实新闻标题**：60字左右的摘要

【国内】
1. **真实新闻标题**：60字左右的摘要

【热点】
1. **真实新闻标题**：60字左右的摘要"""


def _call_provider(provider, prompt):
    """调用单个 LLM provider（OpenAI 兼容接口），返回响应文本。失败抛出异常。

    用 requests 直连而不是 openai 库：超时行为完全可控，
    任何 provider 网络挂起都会在 connect(5s) / read(timeout) 内快速失败，
    不会让整个任务卡死。
    """
    timeout = provider.get("timeout", 60)
    payload = {
        "model": provider["model"],
        "messages": [
            {"role": "system", "content": "你是资深新闻编辑，输出简洁、客观、有信息量。不要输出思考过程，只输出最终结果。"},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.6,
        "max_tokens": 4000,  # 推理模型思考过程也占用 token，需留足空间
    }
    if provider.get("extra_body"):
        payload.update(provider["extra_body"])

    headers = {
        "Authorization": f"Bearer {provider['api_key']}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://github.com/",
        "X-Title": "Daily News",
    }
    url = provider["base_url"].rstrip("/") + "/chat/completions"
    resp = requests.post(url, json=payload, headers=headers, timeout=(5, timeout))
    resp.raise_for_status()
    data = resp.json()
    text = data["choices"][0]["message"]["content"] or ""
    # 过滤推理模型可能输出的 <think>...</think> 思考过程
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()
    if not text.strip():
        raise RuntimeError("返回空内容")
    return text


def _is_format_ok(sections):
    """三个分类都解析出内容才算格式达标。"""
    return all(len(sections.get(s, [])) >= 1 for s in SECTIONS)


def _is_chinese_ok(sections):
    """校验输出是否为中文。

    部分免费模型（如 OpenRouter 的 GLM）可能输出英文摘要，
    只查格式会漏掉，需再检查中文字符占比。
    """
    total = sum(len(t) + len(s) for items in sections.values() for t, s in items)
    if total == 0:
        return False
    cn = sum(
        len(re.findall(r"[\u4e00-\u9fff]", t + s))
        for items in sections.values()
        for t, s in items
    )
    return cn / total >= 0.3


def normalize_markdown(text):
    """排版规范化：分类标题、每条新闻之间用空行隔开。

    Server酱/微信 渲染 markdown 时单个换行会合并为空格，导致内容挤在一起，
    必须用空行让每个条目成为独立段落。
    """
    out = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        out.append(line)
        out.append("")
    return "\n".join(out).strip()


def parse_markdown(text):
    """解析 LLM 输出的 markdown，返回 {分类: [(标题, 摘要)]}。"""
    sections = {}
    pos = 0
    while pos < len(text):
        m = re.search(r"【(国际|国内|热点)】", text[pos:])
        if not m:
            break
        sec = m.group(1)
        start = pos + m.end()
        next_m = re.search(r"【(国际|国内|热点)】", text[start:])
        end = start + next_m.start() if next_m else len(text)
        items = []
        for line in text[start:end].splitlines():
            line = line.strip()
            # 匹配 "1. **标题**：摘要" 或 "1. 标题：摘要"
            mm = re.match(r"^\d+[\.、]\s*\*?\*?(.+?)\*?\*?[：:]\s*(.*)$", line)
            if mm:
                items.append((mm.group(1).strip(), mm.group(2).strip()))
        if items:
            sections[sec] = items
        pos = start
    return sections


def rule_summary(news_items, per_section=None, per_line=None):
    """无 LLM 时的降级方案：按分类直接取标题，做简单裁剪。"""
    per_section = per_section or NEWS_PER_SECTION
    per_line = per_line or NEWS_PER_LINE
    grouped = {sec: [] for sec in SECTIONS}
    for n in news_items:
        sec = CATEGORY_MAP.get(n["category"])
        if sec and len(grouped[sec]) < per_section:
            grouped[sec].append(n)
    lines = []
    for sec in SECTIONS:
        if not grouped[sec]:
            continue
        lines.append(f"【{sec}】")
        lines.append("")
        for i, n in enumerate(grouped[sec], 1):
            summary = n.get("summary") or ""
            if len(summary) > per_line:
                summary = summary[:per_line] + "…"
            lines.append(f"{i}. **{n['title']}**：{summary}")
            lines.append("")
    return "\n".join(lines).strip()


def summarize(news_items):
    """对候选新闻做 AI 筛选+提炼，返回最终 markdown 文本。

    依次尝试每个 LLM provider：调用成功且输出格式达标即采用；
    否则继续尝试下一个，全部不可用再退回规则筛选。
    """
    if not news_items:
        return ""

    prompt = build_prompt(news_items)
    for provider in LLM_PROVIDERS:
        if not provider.get("api_key"):
            continue
        try:
            text = _call_provider(provider, prompt)
            # 若模型前面有思考过程/前缀，只取正式输出部分
            if "【国际】" in text:
                text = text[text.find("【国际】"):]
            sections = parse_markdown(text)
            if _is_format_ok(sections):
                if _is_chinese_ok(sections):
                    print(
                        f"[summarize] 已采用 {provider['base_url']} "
                        f"{provider['model']} 的提炼结果"
                    )
                    return normalize_markdown(text)
                print(
                    f"[summarize] {provider['model']} 输出非中文，"
                    "尝试下一个 provider"
                )
            else:
                print(
                    f"[summarize] {provider['model']} 输出格式不达标，"
                    "尝试下一个 provider"
                )
        except Exception as exc:
            print(
                f"[summarize] {provider['base_url']} {provider.get('model')} "
                f"调用失败：{exc}"
            )

    print("[summarize] 所有 LLM 均不可用或格式不达标，退回规则筛选")
    return rule_summary(news_items)


if __name__ == "__main__":
    from collectors import collect_all
    from filter import filter_fresh, is_gossip, load_seen

    items = collect_all()
    items = [n for n in items if not is_gossip(n["title"])]
    print(summarize(items))
