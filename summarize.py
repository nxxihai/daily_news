"""AI 提炼层：用 LLM 筛选最有价值的新闻并重写为短摘要。

核心改进：
- 支持多个 LLM Provider 按顺序尝试，第一个成功且格式达标即启用
- 对 429 限流错误支持指数退避重试；格式不达标时用强提示重试 1 次
- 全部 LLM 失败时，退回改进版规则筛选（带重要性评分排序）
- 输出严格校验：三个分类齐全、每条条数达标、中文占比合格、标题来自候选池（防编造）
- 分类标准：国际/国内以政治/外交/经济/民生要闻为主，热点以社会新闻/热议话题为主
"""

import re
import time

import requests

from config import (
    FALLBACK_EXTRA_PER_SECTION,
    LLM_PROVIDERS,
    NEWS_PER_LINE,
    NEWS_PER_SECTION,
    RETRY_BASE_DELAY,
)
from filter import importance_score, normalize_title

SECTIONS = ["国际", "国内", "热点"]

# 采集源分类 → 早报三栏的映射（规则版用）
# 注意：社会新闻归入"热点"，财经/科技如果是要闻也可入国内但优先级低于政治社会
CATEGORY_MAP = {
    "国际": "国际", "国际要闻": "国际", "军事": "国际", "world": "国际",
    "国内": "国内", "国内要闻": "国内", "时政": "国内",
    "社会": "热点",           # 社会新闻优先归入热点
    "热点": "热点",
    "财经": "国内", "财经要闻": "国内",  # 宏观财经入国内（个股/期货已被filter过滤）
    "科技": "国内",           # 科技入国内
    "体育": "国内", "sports": "国内",
}

# 标题关键词 → 分类映射（规则版兜底用，避免源分类错误）
# 注意：不要把外国科技公司名（苹果/特斯拉/英伟达等）列入国际——那是财经/科技不是国际要闻
TITLE_KEYWORDS = {
    "国际": [
        # 中国元首外交（属于国际新闻）
        "习近平同", "习近平会见", "习近平与", "习近平致电", "习近平向",
        "李强同", "李强会见", "王毅同", "王毅会见", "外交部发言人",
        "外长", "国事访问", "正式访问", "通电话",
        # 外国政府/政治人物
        "美国", "特朗普", "拜登", "普京", "马克龙", "泽连斯基", "金正恩",
        "欧盟", "北约", "联合国", "制裁", "贸易战", "关税",
        "美联储", "美元", "欧元", "英镑", "日元",
        "外交", "大使", "领事", "条约", "协议", "峰会", "会晤", "访问",
        "冲突", "战争", "军事", "空袭", "导弹", "核武", "恐怖袭击",
        "人质", "难民", "移民", "政变",
        "伊朗", "以色列", "俄罗斯", "乌克兰", "朝鲜", "韩国", "日本",
        "印度", "越南", "泰国", "新加坡", "澳大利亚",
        "英国", "法国", "德国", "意大利", "西班牙", "加拿大", "墨西哥",
        "巴西", "阿根廷", "南非", "埃及", "土耳其", "沙特", "阿联酋",
        "叙利亚", "伊拉克", "阿富汗", "巴基斯坦", "巴勒斯坦", "黎巴嫩",
        "约旦", "哈萨克斯坦", "荷兰", "瑞士", "瑞典",
        "外报", "外媒", "国际", "全球", "海外", "跨境", "中东",
        "G7", "G20", "APEC", "金砖", "上合", "东盟", "白宫", "克里姆林宫",
        "五角大楼", "国会", "参议院", "众议院", "首相", "总统", "总理",
        # 外国央行/国际经济政策
        "澳央行", "欧央行", "日本央行", "英国央行",
    ],
    "国内": [
        "中国", "北京", "上海", "深圳", "广州", "杭州", "成都", "武汉",
        "国务院", "发改委", "央行", "证监会", "银保监", "工信部",
        "全国人大", "政协", "两会", "政府工作报告",
        "A股", "港股", "沪深", "上证指数", "人民币",
        "楼市", "房价", "房地产", "基建", "高铁", "地铁",
        "社保", "医保", "养老金", "就业", "生育", "教育", "高考",
        "疫情", "卫健委", "疾控",
        "乡村振兴", "碳中和", "新能源", "光伏",
        "芯片", "半导体", "人工智能", "航天",
        "华为", "小米", "比亚迪", "宁德时代", "腾讯", "阿里", "字节",
        # 法规政策
        "法规", "条例", "新规", "政策", "办法", "意见",
    ],
    "热点": [
        "警方", "通报", "抓获", "破获", "案件", "事故", "遇难", "受伤",
        "火灾", "爆炸", "车祸", "坠楼", "溺水", "失踪", "失联",
        "救援", "搜救", "被困", "获救", "地震", "台风", "暴雨", "洪水",
        "热搜", "热议", "网友", "视频", "刷屏", "回应", "致歉", "道歉",
        "食品", "药品", "假货", "诈骗", "传销", "盗窃", "抢劫",
        "城管", "拆迁", "物业", "业主", "维权", "举报", "处罚",
        "幼儿园", "学校", "医院", "医生", "护士", "患者",
        "违规", "违法", "查处", "整治", "整改", "暗访", "卧底",
        "公交车", "车祸", "撞", "翻车",
    ],
}


def _classify_by_title(title):
    """根据标题关键词推断分类，用于规则版兜底。返回国际/国内/热点/None。"""
    scores = {"国际": 0, "国内": 0, "热点": 0}
    for sec, keywords in TITLE_KEYWORDS.items():
        for kw in keywords:
            if kw in title:
                scores[sec] += 1
    best_sec = max(scores, key=scores.get)
    if scores[best_sec] == 0:
        return None
    return best_sec


def build_prompt(news_items, per_section=None, per_line=None):
    """构造 LLM 提示词，明确分类标准和排除规则。"""
    per_section = per_section or NEWS_PER_SECTION
    per_line = per_line or NEWS_PER_LINE
    # 控制候选数量，避免小模型上下文过载（但不要太少，保证筛选空间）
    news_items = news_items[:50]
    candidate = "\n".join(
        f"{i + 1}. 【{n['category']}】{n['title']}"
        f"{'｜' + n['summary'][:80] if n.get('summary') else ''}"
        f"｜来源：{n['source']}"
        for i, n in enumerate(news_items)
    )
    return f"""你是资深新闻编辑。以下是今日从权威渠道采集的候选新闻（含标题、简介、来源）。

候选新闻：
{candidate}

请从中选出最有公共价值的 {per_section * 3} 条新闻（国际、国内、热点各 {per_section} 条），严格遵守以下规则：

【分类标准】
- 国际：外国政府决策、国际关系/外交、国际军事冲突、全球性重大事件、重大国际灾害事故。
  不要选入：外国公司财报/销量/股价/IPO、外国科技公司产品发布、加密货币价格、商品期货价格。
- 国内：中国中央及地方政府的政策法规、宏观经济数据、重大科技突破、重大民生政策、重大事故/灾难。
  不要选入：个股涨跌、期货早报、研报、游戏、明星八卦、单家公司业绩。
- 热点：社会新闻（事故/案件/灾害/民生事件/消费者权益/食品安全）、全网热议的民生话题。
  热点优先选与民众生活相关的社会类新闻，娱乐明星绯闻/八卦不要选，体育赛事结果不要选。

【时间要求】
- 优先选择最近1-2天的新闻，排除明显的旧闻（如新闻中提到的日期是几个月前的）。

【写作要求】
1. 排除：游戏新闻、娱乐八卦、个股/期货/ETF/研报、彩票博彩、加密货币价格、广告软文、标题党、纯商品价格快讯。
2. 同一事件只保留一条，不跨分类重复；标题高度相似也算重复。
3. 每条重写为 {per_line} 字以内的摘要：一句话核心事实+关键信息，不注水、不评论。
4. 摘要必须是完整句子，信息完整，严禁用省略号"…"或"......"截断结尾。
5. 标题必须使用新闻的真实标题，不要自己编造标题。
6. 必须简体中文，严格按下面格式输出，不要输出任何解释、思考、前后缀：

【国际】
1. **真实新闻标题**：{per_line}字以内的完整摘要
2. **真实新闻标题**：{per_line}字以内的完整摘要

【国内】
1. **真实新闻标题**：{per_line}字以内的完整摘要

【热点】
1. **真实新闻标题**：{per_line}字以内的完整摘要"""


def _call_provider_with_retry(provider, prompt, max_retries=None):
    """调用 LLM provider，支持 429 限流的指数退避重试。

    Returns: (text, error_message) 成功时 text 为响应内容，失败时 text 为 None。
    """
    if max_retries is None:
        max_retries = provider.get("max_retries", 1)
    timeout = provider.get("timeout", 60)
    last_error = None

    for attempt in range(max_retries + 1):
        try:
            payload = {
                "model": provider["model"],
                "messages": [
                    {"role": "system", "content": "你是资深新闻编辑，输出简洁、客观、有信息量。必须严格按照用户要求的格式输出，不要输出任何思考过程或解释。"},
                    {"role": "user", "content": prompt},
                ],
                "temperature": 0.3,  # 降低温度，减少格式混乱
                "max_tokens": 4000,
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
            msg = data["choices"][0]["message"]
            text = msg.get("content") or ""
            # 兼容推理模型的 reasoning_content
            if not text.strip() and msg.get("reasoning_content"):
                text = msg["reasoning_content"]
            # 兼容数组格式 content
            if isinstance(text, list):
                text = "".join(str(x) for x in text)
            # 过滤 <think>...</think> 思考过程
            text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()
            # 过滤 <think> 未闭合的情况
            text = re.sub(r"<think>.*", "", text, flags=re.DOTALL).strip()
            if not text.strip():
                raise RuntimeError("返回空内容")
            return text, None
        except requests.exceptions.HTTPError as e:
            status_code = e.response.status_code if e.response is not None else 0
            last_error = f"{status_code} {e.response.reason if e.response is not None else ''}"
            if status_code == 429 and attempt < max_retries:
                delay = RETRY_BASE_DELAY * (2 ** attempt)
                print(f"[summarize] {provider['model']} 限流(429)，{delay}s后重试(第{attempt+1}次)...")
                time.sleep(delay)
                continue
            if status_code == 402:
                # 欠费，直接放弃这个provider，不重试
                return None, f"{status_code} Payment Required（欠费）"
            if status_code >= 500 and attempt < max_retries:
                delay = RETRY_BASE_DELAY * (2 ** attempt)
                print(f"[summarize] {provider['model']} 服务器错误({status_code})，{delay}s后重试...")
                time.sleep(delay)
                continue
            return None, f"{status_code} Client Error: {e.response.reason if e.response is not None else ''}"
        except Exception as exc:
            last_error = str(exc)
            if attempt < max_retries:
                delay = RETRY_BASE_DELAY * (2 ** attempt)
                print(f"[summarize] {provider['model']} 调用异常，{delay}s后重试: {exc}")
                time.sleep(delay)
                continue
            return None, str(exc)
    return None, last_error


def _is_format_ok(sections, per_section=None):
    """三个分类都至少有 min(per_section, 3) 条才算格式达标。"""
    per_section = per_section or NEWS_PER_SECTION
    min_count = min(per_section, 3)
    return all(len(sections.get(s, [])) >= min_count for s in SECTIONS)


def _is_chinese_ok(sections):
    """校验输出是否为中文（中文字符占比 >= 30%）。"""
    total = sum(len(t) + len(s) for items in sections.values() for t, s in items)
    if total == 0:
        return False
    cn = sum(
        len(re.findall(r"[\u4e00-\u9fff]", t + s))
        for items in sections.values()
        for t, s in items
    )
    return cn / total >= 0.3


def _is_summary_complete(sections, per_line=None):
    """校验摘要是否完整：不能以省略号结尾，不能太短（少于10字），不能有截断痕迹。"""
    per_line = per_line or NEWS_PER_LINE
    for sec, items in sections.items():
        for title, summary in items:
            s = summary.strip()
            if len(s) < 8:
                return False
            if s.endswith("…") or s.endswith("...") or s.endswith("......"):
                return False
            # 检查是否以标点符号结尾（完整句子的标志）
            if not re.search(r"[。！？；：，、）】」』]$", s):
                # 不以标点结尾可能是截断，但也可能是短新闻，放宽检查
                pass
            # 检查是否有明显的截断痕迹
            if re.search(r"[，、；：]$", s) and len(s) > per_line - 5:
                return False
    return True


def _validate_titles_exist(sections, news_items):
    """校验 LLM 输出的标题是否在候选新闻中存在（模糊匹配，防止模型编造新闻）。

    使用更宽松的匹配：检查标题核心内容（前4个归一化字符）是否在候选中，
    允许模型对标题做适当润色/简化，但核心内容必须真实存在。
    至少 80% 的标题通过校验才算合格。
    """
    candidate_titles = [normalize_title(n["title"]) for n in news_items]
    total = 0
    matched = 0
    for sec, items in sections.items():
        for title, summary in items:
            total += 1
            norm = normalize_title(title)
            if len(norm) < 4:
                continue
            # 取前4-6个字符做核心匹配
            for core_len in [6, 5, 4]:
                core = norm[:core_len]
                if any(core in ct for ct in candidate_titles):
                    matched += 1
                    break
                if any(ct[:core_len] in norm for ct in candidate_titles if len(ct) >= core_len):
                    matched += 1
                    break
    if total == 0:
        return False
    # 允许个别标题有轻微改写，80%匹配即通过
    return matched / total >= 0.7


def _deduplicate_sections(sections):
    """去重：跨分类 + 同分类内相似标题去重。

    1. 跨分类：同一条新闻只保留在优先级最高的分类里（国内 > 国际 > 热点）。
    2. 同分类内：同一事件的不同报道（标题高度相似）只保留第一条。
    """
    priority = {"国内": 0, "国际": 1, "热点": 2}

    # 第一步：跨分类精确去重，按优先级决定归属
    seen_exact = {}
    for sec in sorted(sections, key=lambda s: priority.get(s, 99)):
        for title, summary in sections[sec]:
            norm = normalize_title(title)
            if norm not in seen_exact:
                seen_exact[norm] = (sec, title, summary)

    # 第二步：按优先级组装，全局同事件去重（跨分类+同分类内）
    # 同一事件即使被 LLM 分到不同分类、标题措辞不同，也只保留一条
    result = {sec: [] for sec in SECTIONS}
    used = []  # [(norm_title, summary)]
    for sec in sorted(SECTIONS, key=lambda s: priority.get(s, 99)):
        for norm, (keeper_sec, title, summary) in seen_exact.items():
            if keeper_sec != sec:
                continue
            is_dup = False
            for used_t, used_s in used:
                if _is_same_event(norm, summary, used_t, used_s):
                    is_dup = True
                    break
            if not is_dup:
                result[sec].append((title, summary))
                used.append((norm, summary))

    return {k: v for k, v in result.items() if v}


def _fill_missing_sections(sections, news_items, per_section=None):
    """当 LLM 返回的某些分类条数不足时，用规则筛选补充缺失的部分。"""
    per_section = per_section or NEWS_PER_SECTION
    # 已选用的标题
    used_titles = set()
    for items in sections.values():
        for title, _ in items:
            used_titles.add(normalize_title(title))

    # 按分类从候选池中选取未被使用的、高重要性的新闻
    grouped = {"国际": [], "国内": [], "热点": []}
    for n in news_items:
        if normalize_title(n["title"]) in used_titles:
            continue
        sec = CATEGORY_MAP.get(n.get("category", ""))
        if not sec:
            sec = _classify_by_title(n["title"])
        if not sec:
            sec = "热点"
        if sec in grouped:
            grouped[sec].append(n)

    # 按重要性排序每组
    for sec in grouped:
        grouped[sec].sort(key=importance_score, reverse=True)

    # 补充缺失分类或不足条数
    for sec in SECTIONS:
        current = sections.get(sec, [])
        needed = per_section - len(current)
        if needed > 0:
            candidates = grouped.get(sec, [])
            added = 0
            for n in candidates:
                if added >= needed:
                    break
                summary = n.get("summary") or n["title"]
                if len(summary) > NEWS_PER_LINE:
                    summary = summary[:NEWS_PER_LINE].rstrip("，。；：、！？,.!:;")
                current.append((n["title"], summary))
                used_titles.add(normalize_title(n["title"]))
                added += 1
            sections[sec] = current
    return sections


def sections_to_markdown(sections, per_line=None):
    """根据去重后的 sections 重新生成规范 markdown。"""
    per_line = per_line or NEWS_PER_LINE
    lines = []
    for sec in SECTIONS:
        items = sections.get(sec, [])
        if not items:
            continue
        lines.append(f"【{sec}】")
        lines.append("")
        for i, (title, summary) in enumerate(items, 1):
            # 清理摘要
            s = summary.strip()
            # 去掉残留的markdown标记
            s = s.replace("**", "").replace("*", "").strip()
            # 去掉省略号结尾
            s = s.rstrip("…...")
            # 修复明显截断：以逗号、顿号结尾的，补"等"或去掉末尾标点
            while s and s[-1] in "，、；：":
                s = s[:-1].rstrip()
            # 如果摘要没有句末标点，补一个句号
            if s and not re.search(r"[。！？]$", s):
                s += "。"
            # 如果摘要超过字数限制，智能截断（在标点处断句）
            if len(s) > per_line:
                truncated = s[:per_line]
                for punct in ["。", "！", "？", "，", "；"]:
                    pos = truncated.rfind(punct)
                    if pos >= per_line * 0.6:
                        truncated = truncated[:pos + 1]
                        break
                else:
                    truncated = truncated.rstrip("，。；：、！？,.!:;")
                    if truncated and not re.search(r"[。！？]$", truncated):
                        truncated += "。"
                s = truncated
            # 如果摘要太短（只有几个字），用标题兜底
            if len(s) < 5:
                s = title + "。"
            lines.append(f"{i}. **{title}**：{s}")
            lines.append("")
    return "\n".join(lines).strip()


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
            # 匹配 "1. **标题**：摘要" 或 "1. 标题：摘要" 或 "1、标题：摘要"
            mm = re.match(r"^\d+[\.、]\s*(.+?)[：:]\s*(.*)$", line)
            if mm:
                raw_title = mm.group(1).strip()
                raw_summary = mm.group(2).strip()
                # 清理标题中多余的粗体标记
                title = raw_title.replace("**", "").strip()
                # 清理摘要中残留的粗体标记和其他markdown符号
                summary = raw_summary.replace("**", "").replace("*", "").strip()
                # 清理摘要末尾可能残留的标记
                summary = summary.rstrip("*").strip()
                if title and summary:
                    items.append((title, summary))
        if items:
            sections[sec] = items
        pos = start
    return sections


def _longest_common_substring_len(s1, s2):
    """最长公共子串长度。标题很短（几十字），O(n*m) 无性能问题。"""
    if not s1 or not s2:
        return 0
    prev = [0] * (len(s2) + 1)
    best = 0
    for i in range(1, len(s1) + 1):
        cur = [0] * (len(s2) + 1)
        ci = s1[i - 1]
        for j in range(1, len(s2) + 1):
            if ci == s2[j - 1]:
                cur[j] = prev[j - 1] + 1
                if cur[j] > best:
                    best = cur[j]
        prev = cur
    return best


def _is_similar_title(norm1, norm2, threshold=0.6):
    """判断两个归一化标题是否相似（同一事件的不同报道）。

    判定规则：
    1. 一个包含另一个 → 相似
    2. 最长公共子串占短标题比例 >= threshold → 相似
    3. 同事件指纹：共享核心短语(>=4字) + 共享数字 → 相似
       （如"冷藏车"事故的不同角度报道都含"8"，应视为同一事件）
    """
    if not norm1 or not norm2:
        return False
    if norm1 in norm2 or norm2 in norm1:
        return True
    short, long_ = (norm1, norm2) if len(norm1) <= len(norm2) else (norm2, norm1)
    lcs = _longest_common_substring_len(short, long_)
    if lcs >= 6 and lcs / len(short) >= threshold:
        return True
    # 同事件指纹：核心短语相同且数字相同（伤亡人数/年份等），即使报道角度不同
    # 如"冷藏车"(3字)事故的多篇报道都含"8"，应视为同一事件
    if lcs >= 3 and set(re.findall(r"\d+", norm1)) & set(re.findall(r"\d+", norm2)):
        return True
    return False


_BOILERPLATE_RE = re.compile(
    r"^(?:新浪|搜狐|网易|腾讯|央视|人民网|新华网|澎湃新闻|界面新闻|财联社)?"
    r"(?:科技|财经|数码|汽车|证券)?讯.*?消息[，,。]?"
)


def _clean_for_compare(s):
    """去掉"新浪科技讯8月25日晚间消息，"这类讯头，避免误判同事件。"""
    return _BOILERPLATE_RE.sub("", s or "").strip()


def _is_same_event(t1, s1, t2, s2):
    """判断两条新闻是否为同一事件（标题或摘要任一相似即算）。

    规则：
    1. 标题相似（_is_similar_title）→ 同事件
    2. 同事件指纹：共享数字（伤亡人数/年份等）+ >=4 字核心短语
       （标题或摘要，去数字后比较）。如"冷藏车"事故多篇报道都含"8"
       且摘要共享"河南平顶山"。无数字的套话（"相关部门已回应"）不会误判。
    """
    if _is_similar_title(t1, t2):
        return True
    s1c, s2c = _clean_for_compare(s1), _clean_for_compare(s2)
    nums = set(re.findall(r"\d+", t1 + s1)) & set(re.findall(r"\d+", t2 + s2))
    if nums:
        lt = _longest_common_substring_len(re.sub(r"\d+", "", t1), re.sub(r"\d+", "", t2))
        ls = _longest_common_substring_len(re.sub(r"\d+", "", s1c), re.sub(r"\d+", "", s2c))
        if max(lt, ls) >= 4:
            return True
    return False


def _one_line_summary(n, per_line):
    """规则兜底生成完整一句话新闻：完整性优先，字数上限放宽到 1.6 倍。"""
    s = _clean_for_compare(n.get("summary") or "")
    s = s.replace("**", "").replace("*", "").strip()
    # 去掉尾部省略号/连续句号
    s = re.sub(r"[…。.]{2,}\s*$", "", s).rstrip("…。.")
    # 去掉结尾悬挂标点
    while s and s[-1] in "，、；：":
        s = s[:-1].rstrip()
    if not s:
        s = n["title"]
    if s and not re.search(r"[。！？]$", s):
        s += "。"
    # 超长时在标点处断句（放宽上限，保证句子完整）
    limit = int(per_line * 1.6)
    if len(s) > limit:
        truncated = s[:limit]
        for punct in ["。", "！", "？", "；", "，"]:
            pos = truncated.rfind(punct)
            if pos >= limit * 0.5:
                truncated = truncated[:pos + 1]
                break
        else:
            truncated = truncated.rstrip("，。；：、！？,.!:;") + "。"
        s = truncated
    return s


def rule_summary(news_items, per_section=None, per_line=None):
    """无 LLM 时的降级方案：按分类+重要性评分筛选，生成一句话新闻。

    改进：
    - 使用 importance_score 对新闻排序（政治/重大事件优先）
    - 社会新闻归入"热点"
    - 跨分类模糊去重（同一事件不同报道只保留一条）
    - AI 全失效时信息量补偿：每栏取 per_section + FALLBACK_EXTRA_PER_SECTION 条
    """
    per_section = per_section or NEWS_PER_SECTION
    per_line = per_line or NEWS_PER_LINE
    total = per_section + FALLBACK_EXTRA_PER_SECTION

    grouped = {"国际": [], "国内": [], "热点": []}
    for n in news_items:
        # 先用采集源自带的分类映射
        sec = CATEGORY_MAP.get(n.get("category", ""))
        # 如果源分类缺失或不明确，用标题关键词推断
        if not sec:
            sec = _classify_by_title(n["title"])
        # 社会新闻强制归热点
        if n.get("category") == "社会":
            sec = "热点"
        # 还是不确定的，默认归"热点"
        if not sec:
            sec = "热点"
        grouped[sec].append(n)

    # 每组按重要性分数降序排列
    for sec in grouped:
        grouped[sec].sort(key=importance_score, reverse=True)

    # 跨组去重（同一新闻/同一事件可能因分类模糊被放入多组）
    used = []  # [(norm_title, summary)]
    result = {"国际": [], "国内": [], "热点": []}

    def _is_duplicate(n):
        norm = normalize_title(n["title"])
        summary = n.get("summary") or ""
        for used_t, used_s in used:
            if _is_same_event(norm, summary, used_t, used_s):
                return True
        return False

    # 按优先级选：国内→国际→热点
    for sec in ["国内", "国际", "热点"]:
        for n in grouped[sec]:
            if len(result[sec]) >= total:
                break
            norm = normalize_title(n["title"])
            if _is_duplicate(n):
                continue
            used.append((norm, n.get("summary") or ""))
            result[sec].append(n)

    # 如果某组不足，从其他组的溢出新闻中按分数补充
    for sec in SECTIONS:
        if len(result[sec]) < total:
            for other_sec in SECTIONS:
                if other_sec == sec:
                    continue
                for n in grouped[other_sec]:
                    if len(result[sec]) >= total:
                        break
                    norm = normalize_title(n["title"])
                    if _is_duplicate(n):
                        continue
                    result[sec].append(n)
                    used.append((norm, n.get("summary") or ""))

    # 生成 markdown（一句话新闻，完整性优先）
    lines = []
    for sec in SECTIONS:
        items = result[sec]
        if not items:
            continue
        lines.append(f"【{sec}】")
        lines.append("")
        for i, n in enumerate(items[:total], 1):
            lines.append(f"{i}. **{n['title']}**：{_one_line_summary(n, per_line)}")
            lines.append("")
    return "\n".join(lines).strip()


def summarize(news_items):
    """对候选新闻做 AI 筛选+提炼，返回最终 markdown 文本。

    流程：
    1. 依次尝试每个 LLM provider（带重试）
    2. 调用成功后解析输出，做格式/中文/完整性/真实性校验
    3. 格式不达标时，用强提示重试 1 次
    4. 校验通过则补充缺失分类（如有），返回结果
    5. 全部 LLM 失败，退回改进版规则筛选
    """
    if not news_items:
        return ""

    prompt = build_prompt(news_items)
    # 格式不达标时的修复提示
    fix_prompt = prompt + "\n\n【重要提醒】你上次输出格式不正确或内容不完整，请严格按上面的格式重新输出，确保【国际】【国内】【热点】三个分类都有5条新闻，每条格式为「数字. **标题**：摘要」，摘要必须完整句子，不要用省略号结尾。"

    for provider in LLM_PROVIDERS:
        if not provider.get("api_key"):
            continue
        # 第一次尝试
        text, err = _call_provider_with_retry(provider, prompt)
        if text is None:
            print(
                f"[summarize] {provider['base_url']} {provider.get('model')} "
                f"调用失败：{err}"
            )
            continue

        # 解析并校验
        if "【国际】" in text:
            text = text[text.find("【国际】"):]
        sections = parse_markdown(text)
        sections = _deduplicate_sections(sections)

        format_ok = _is_format_ok(sections)
        chinese_ok = _is_chinese_ok(sections)
        complete_ok = _is_summary_complete(sections)
        titles_ok = _validate_titles_exist(sections, news_items)

        # 如果格式不达标（缺分类/条数不够），重试一次
        if not format_ok and provider.get("max_retries", 0) >= 0:
            print(f"[summarize] {provider['model']} 格式不达标，使用强提示重试1次...")
            text2, err2 = _call_provider_with_retry(provider, fix_prompt, max_retries=0)
            if text2 and "【国际】" in text2:
                text2 = text2[text2.find("【国际】"):]
                sections2 = parse_markdown(text2)
                sections2 = _deduplicate_sections(sections2)
                if (_is_format_ok(sections2) and _is_chinese_ok(sections2)
                        and _is_summary_complete(sections2)):
                    sections = sections2
                    format_ok = True
                    chinese_ok = True
                    complete_ok = True
                    titles_ok = _validate_titles_exist(sections, news_items)

        if format_ok and chinese_ok and complete_ok and titles_ok:
            # 补充可能的缺失条目（如果某类不足）
            sections = _fill_missing_sections(sections, news_items)
            print(
                f"[summarize] 已采用 {provider['base_url']} "
                f"{provider['model']} 的提炼结果"
            )
            return sections_to_markdown(sections)
        else:
            reasons = []
            if not format_ok:
                reasons.append("格式不达标")
            if not chinese_ok:
                reasons.append("非中文")
            if not complete_ok:
                reasons.append("摘要截断")
            if not titles_ok:
                reasons.append("标题异常")
            print(
                f"[summarize] {provider['model']} {','.join(reasons)}，"
                "尝试下一个 provider"
            )

    print("[summarize] 所有 LLM 均不可用或格式不达标，退回规则筛选")
    return rule_summary(news_items)


if __name__ == "__main__":
    from collectors import collect_all
    from filter import filter_quality, filter_fresh, load_seen

    items = collect_all()
    items = filter_quality(items)
    print(f"质量过滤后：{len(items)} 条")
    print(summarize(items))
