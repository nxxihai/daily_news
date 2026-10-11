"""发布层：把当日 markdown 早报渲染成 HTML 单页，发布到 GitHub Pages。

run_daily.py 在推送前调用 publish_page()：
    ① render_html()    markdown → 完整 HTML（CSS 内联，无外链依赖，手机端友好）
    ② 写 docs/index.html（每天覆盖 → 页面永远是最新一期）
    ③ git 提交并推送到远端 → GitHub Pages 自动更新

GitHub Pages 一次性设置：仓库 Settings → Pages → Source 选 "Deploy from a branch"，
分支 main、目录 /docs。设置好后地址固定为 config.SITE_URL：
https://nxxihai.github.io/daily_news/

设计要点：
- 零新增依赖：markdown 解析只针对本项目自己的输出格式（【分类】+ 编号要点 + 介绍行），
  不引入 markdown 库，避免本地/云端缺包把整条流水线带崩。
- 失败不抛异常：任何一步出错都返回 (False, 原因)，由 run_daily.py 降级
  （不附网页链接、照常发早报），不会因为发布失败而断更。
"""

import html
import os
import re
import subprocess
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from config import SITE_URL

BJT = timezone(timedelta(hours=8))

BASE_DIR = Path(__file__).resolve().parent
PAGE_REL = "docs/index.html"                      # 相对仓库根目录（git 路径固定用 /）
PAGE_FILE = BASE_DIR / "docs" / "index.html"

GIT_TIMEOUT = 60                                  # 单条 git 命令超时（秒），防止计划任务卡死
PUSH_RETRY_DELAY = 5                              # git push 失败后的重试等待（秒）
BOT_NAME = "github-actions[bot]"
BOT_EMAIL = "41898282+github-actions[bot]@users.noreply.github.com"

# 分类配色（未在表里的分类用默认蓝）
SECTION_THEME = {
    "国际": "#2563eb",
    "国内": "#dc2626",
    "热点": "#7c3aed",
}

# ================= 样式（内联，无外部请求，兼容微信内置浏览器） =================
CSS = """
:root{
  --bg:#f2f4f7; --card:#fff; --text:#14181f; --muted:#65707e; --line:#e4e8ee;
  --accent:#2563eb; --radius:16px;
  --shadow:0 1px 2px rgba(16,24,40,.04), 0 10px 30px -14px rgba(16,24,40,.22);
}
@media (prefers-color-scheme: dark){
  :root{
    --bg:#0d1014; --card:#161a20; --text:#e6e9ee; --muted:#98a2b0; --line:#242a33;
    --shadow:0 1px 2px rgba(0,0,0,.5), 0 10px 30px -14px rgba(0,0,0,.75);
  }
}
*{box-sizing:border-box}
html{-webkit-text-size-adjust:100%}
body{
  margin:0; background:var(--bg); color:var(--text);
  font:16px/1.8 -apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC",
       "Hiragino Sans GB","Microsoft YaHei",Roboto,"Helvetica Neue",Arial,sans-serif;
}
a{color:var(--accent); text-decoration:none}
a:hover{text-decoration:underline}
.wrap{max-width:760px; margin:0 auto; padding:30px 16px 60px}
.hero{margin:0 0 24px}
.kicker{
  display:inline-block; margin:0 0 10px; padding:4px 12px; border-radius:999px;
  background:rgba(37,99,235,.12); color:var(--accent);
  font-size:12px; font-weight:600; letter-spacing:.1em;
}
h1.date{margin:0; font-size:31px; line-height:1.3; font-weight:800; letter-spacing:-.01em}
.meta{margin:10px 0 0; color:var(--muted); font-size:13px}
.sec{
  background:var(--card); border:1px solid var(--line); border-radius:var(--radius);
  box-shadow:var(--shadow); padding:16px 16px 4px; margin:0 0 18px;
}
.sec-head{
  display:flex; align-items:center; gap:9px; margin:0 0 12px;
  font-size:17px; font-weight:700; letter-spacing:.02em;
}
.dot{width:10px; height:10px; border-radius:50%; background:var(--sec,var(--accent)); flex:0 0 auto}
.count{
  margin-left:auto; padding:2px 9px; border-radius:999px; background:var(--bg);
  color:var(--muted); font-size:12px; font-weight:500;
}
.list{list-style:none; margin:0; padding:0}
.item{display:flex; gap:11px; padding:13px 0; border-top:1px solid var(--line)}
.item:first-child{border-top:0}
.item.brief{padding:11px 0}
.item.brief .num{opacity:.55}
.item.brief .head{font-size:15.5px; font-weight:600}
.num{
  flex:0 0 24px; height:24px; margin-top:4px; border-radius:8px;
  background:rgba(128,128,128,.14); color:var(--sec,var(--accent));
  font-size:13px; font-weight:700; display:flex; align-items:center; justify-content:center;
}
.bd{min-width:0}
.head{margin:0; font-size:16.5px; font-weight:700; line-height:1.6}
.desc{margin:6px 0 0; color:var(--muted); font-size:14.5px; line-height:1.75}
.notice{
  background:var(--card); border:1px dashed var(--line); border-radius:var(--radius);
  padding:22px 18px; color:var(--muted); text-align:center; margin:0 0 18px;
}
.foot{margin:26px 0 0; color:var(--muted); font-size:12.5px; text-align:center; line-height:2}
.foot a{color:var(--muted); text-decoration:underline}
/* 听新闻：标题下方紧跟的原生播放器（吸顶，滚动时也在）；没有 MP3 时整块不渲染 */
.player{position:sticky; top:0; z-index:9; margin:0 0 18px; padding:9px 12px;
  display:flex; align-items:center; gap:10px;
  background:var(--card); border:1px solid var(--line); border-radius:14px;
  box-shadow:var(--shadow)}
.plabel{flex:0 0 auto; color:var(--accent); font-size:13px; font-weight:600; white-space:nowrap}
.player audio{flex:1 1 auto; width:100%; min-width:0; height:36px}
"""

# 听新闻：浏览器内置语音合成（免费、无需 API/音频文件）。
# 单独放成普通字符串，避免与 HTML 模板的 f-string 花括号冲突。
AUDIO_HTML = """
<div class="player">
  <span class="plabel">听新闻</span>
  <audio controls preload="none" src="{src}">
    您的浏览器不支持音频播放，<a href="{src}">点此下载 MP3</a>。
  </audio>
</div>
"""


# ================= markdown → HTML =================
_SEC_RE = re.compile(r"^\s*【(.+?)】\s*$")
_ITEM_RE = re.compile(r"^\s*(?:\d+[.、]|[-*・])\s*(.+?)\s*$")
_BOLD_RE = re.compile(r"\*\*(.+?)\*\*")
_LINK_RE = re.compile(r"\[([^\]\n]+)\]\((https?://[^\s)]+)\)")


def _inline(text):
    """行内 markdown → HTML：先转义，再只恢复 **加粗** 与 [文字](链接)。"""
    out = html.escape(text, quote=False)
    out = _BOLD_RE.sub(r"<strong>\1</strong>", out)
    return _LINK_RE.sub(
        r'<a href="\2" target="_blank" rel="noopener">\1</a>', out
    )


def parse_digest(markdown):
    """解析早报 markdown → [{"name": 分类, "items": [{"head": 要点, "desc": 介绍}]}]。

    只认本项目自己的格式（【分类】+「数字. **要点**」+ 介绍行），
    识别不了的行按普通段落兜底，保证页面不会丢内容。
    """
    sections = []
    cur = None
    item = None

    def _ensure_section():
        nonlocal cur
        if cur is None:
            cur = {"name": "", "items": []}
            sections.append(cur)
        return cur

    for raw in markdown.splitlines():
        line = raw.strip()
        if not line:
            item = None                      # 空行 = 当前条目结束
            continue
        m = _SEC_RE.match(line)
        if m:
            cur = {"name": m.group(1), "items": []}
            sections.append(cur)
            item = None
            continue
        m = _ITEM_RE.match(line)
        if m:
            # 要点行原文是「1. **要点**」，去掉加粗标记（HTML 里由样式负责加粗）
            item = {"head": _BOLD_RE.sub(r"\1", m.group(1)).strip(), "desc": ""}
            _ensure_section()["items"].append(item)
            continue
        if item is not None:
            item["desc"] += line             # 介绍可能被折成多行
        else:
            _ensure_section()["items"].append({"head": "", "desc": line})
    return [s for s in sections if s["items"]]


def _render_sections(sections):
    """结构化数据 → 分类卡片 HTML。"""
    blocks = []
    for sec in sections:
        name = sec["name"] or "要闻"
        # 有介绍的排在前面：页面从上到下是"带背景的重点 → 一句话简讯"，
        # 避免"有介绍/没介绍"交错造成的参差观感
        items = [it for it in sec["items"] if it["desc"]] + [
            it for it in sec["items"] if not it["desc"]
        ]
        lis = []
        num = 0
        for it in items:
            head = it["head"]
            if head:
                num += 1
                badge = f'<span class="num">{num}</span>'
                head_html = f'<p class="head">{_inline(head)}</p>'
            else:
                badge = ""
                head_html = ""
            desc_html = f'<p class="desc">{_inline(it["desc"])}</p>' if it["desc"] else ""
            # 无介绍的条目用 brief 样式：视觉上明确是"一句话简讯"，不是缺内容
            cls = "item" if it["desc"] else "item brief"
            lis.append(
                f'<li class="{cls}">{badge}<div class="bd">{head_html}{desc_html}</div></li>'
            )
        blocks.append(
            f'<section class="sec" style="--sec:{SECTION_THEME.get(name, "#2563eb")}">'
            f'<h2 class="sec-head"><span class="dot"></span>{_inline(name)}'
            f'<span class="count">{len(sec["items"])} 条</span></h2>'
            f'<ol class="list">{"".join(lis)}</ol></section>'
        )
    return "".join(blocks)


def _repo_url(site_url):
    """从 GitHub Pages 地址反推仓库地址，推不出来返回空串。"""
    m = re.match(r"https?://([^.]+)\.github\.io/([^/]+)/?", site_url or "")
    return f"https://github.com/{m.group(1)}/{m.group(2)}" if m else ""


def render_html(title, markdown, generated_at=None, engine="llm", audio_src=""):
    """生成完整 HTML 文档（单文件、内联样式）。

    - engine="rule" 时页面上标注"规则精简版"，便于一眼看出 AI 通道是否可用
    - audio_src 非空时插入原生音频播放器（MP3，微信内置浏览器也能播）
    """
    now = generated_at or datetime.now(BJT)
    m = re.search(r"\d{4}-\d{2}-\d{2}", title or "")
    date_text = m.group(0) if m else now.strftime("%Y-%m-%d")
    stamp = now.strftime("%Y-%m-%d %H:%M")
    engine_note = (
        "AI 提炼自公开新闻源" if engine != "rule" else "规则精简版（AI 通道不可用）"
    )

    # 仓库路径 docs/audio/latest.mp3 在站点上对应 audio/latest.mp3（Pages 根 = docs/）；
    # 文件名固定，必须带 ?v=日期 破缓存，否则微信/浏览器会播到旧音频
    audio_url = audio_src[len("docs/"):] if audio_src.startswith("docs/") else audio_src
    audio_block = (
        AUDIO_HTML.format(src=f"{audio_url}?v={date_text}") if audio_url else ""
    )

    sections = parse_digest(markdown or "")
    body = _render_sections(sections) or (
        '<div class="notice">本期内容尚未生成，页面会在每次早报推送后自动更新。</div>'
    )

    repo = _repo_url(SITE_URL)
    foot_links = (
        f'<p><a href="{repo}">GitHub 仓库</a> · '
        f'<a href="{repo}/commits/main/{PAGE_REL}">历史版本</a></p>'
        if repo
        else ""
    )

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{html.escape(title or "每日新闻早报", quote=True)}</title>
<meta name="description" content="{html.escape(f"每日新闻早报 {date_text}：国际、国内、热点要闻，AI 提炼自公开新闻源。", quote=True)}">
<meta name="theme-color" content="#2563eb">
<style>{CSS}</style>
</head>
<body>
<div class="wrap">
  <header class="hero">
    <p class="kicker">每日新闻早报</p>
    <h1 class="date">{html.escape(date_text)}</h1>
    <p class="meta">更新于 {stamp}（北京时间） · {engine_note}</p>
  </header>
{audio_block}
  {body}
  <footer class="foot">
    <p>本页每天推送后自动更新，永远是最新一期。</p>
    {foot_links}
  </footer>
</div>
</body>
</html>
"""


# ================= 写文件 =================
def write_page(html_text):
    """把 HTML 写到 docs/index.html，返回文件路径。"""
    PAGE_FILE.parent.mkdir(parents=True, exist_ok=True)
    PAGE_FILE.write_text(html_text, encoding="utf-8")
    return PAGE_FILE


# ================= git 提交推送 =================
def _git(args, env=None):
    """执行 git 子命令：返回 CompletedProcess；找不到 git / 超时返回 None。"""
    try:
        return subprocess.run(
            ["git", *args],
            cwd=str(BASE_DIR),
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=GIT_TIMEOUT,
        )
    except (OSError, subprocess.SubprocessError):
        return None


def _err(result):
    """把 git 结果整理成一句可读的错误说明。"""
    if result is None:
        return "git 命令超时或不可用"
    text = (result.stderr or result.stdout or "").strip().replace("\n", " ")
    return (text[:200] or f"返回码 {result.returncode}")


def _ident_args():
    """云端 checkout 的容器没有 git 身份配置时补上（只作用于本次命令，不改配置）。"""
    args = []
    for key, val in (("user.name", BOT_NAME), ("user.email", BOT_EMAIL)):
        r = _git(["config", "--get", key])
        if r is None or r.returncode != 0 or not r.stdout.strip():
            args += ["-c", f"{key}={val}"]
    return args


def _current_branch():
    """当前分支名：云端优先用 GITHUB_REF_NAME，本地取 HEAD，最后兜底 main。"""
    name = os.environ.get("GITHUB_REF_NAME") or os.environ.get("GITHUB_HEAD_REF")
    if name:
        return name
    r = _git(["symbolic-ref", "--quiet", "--short", "HEAD"])
    if r is not None and r.returncode == 0 and r.stdout.strip():
        return r.stdout.strip()
    return "main"


def _push(ref, branch):
    """推送 ref 到 origin/<branch>，失败等几秒再试一次。

    实测 GitHub 连接偶发抖动（本地代理/加速器切换时），一次失败就放弃会导致
    当天的页面漏发，所以给第二次机会。
    """
    last = None
    for attempt in range(2):
        r = _git(["push", "origin", f"{ref}:refs/heads/{branch}"])
        if r is not None and r.returncode == 0:
            return True, ""
        last = r
        if attempt == 0:
            time.sleep(PUSH_RETRY_DELAY)
    return False, _err(last)


def _commit_on_head(paths, message, branch):
    """常规路径：在当前 HEAD 上提交（云端与「本地不落后」时走这里）。"""
    if _git(["add", "--", *paths]) is None:
        return False, "git add 超时"
    staged = _git(["diff", "--cached", "--quiet", "--", *paths])
    if staged is not None and staged.returncode == 0:
        return True, "页面内容与上次相同，跳过提交"
    commit = _git([*_ident_args(), "commit", "-m", message, "--", *paths])
    if commit is None or commit.returncode != 0:
        return False, f"git commit 失败：{_err(commit)}"
    ok, err = _push("HEAD", branch)
    if not ok:
        return False, f"git push 失败（已重试）：{err}"
    return True, f"已提交并推送到 origin/{branch}"


def _commit_on_remote(paths, message, branch):
    """兜底路径：本地落后于远端时，基于 origin/<branch> 生成一个只改页面的提交再推送。

    目的：本地仓库长期不 pull（早报机器人一直在往 seen.json/页面写提交）时，
    直接在当前 HEAD 上提交会因 non-fast-forward 被拒绝。这里用一个临时索引
    拼出「父提交 = origin/<branch>」的提交直接推上去，全程不动本地分支和工作区，
    避免 rebase/stash 把用户没提交的改动弄乱。
    """
    index_file = BASE_DIR / ".git" / "publish-index"
    # 上次被中断可能留下索引/锁文件，先清掉，避免 git add 报 index.lock 已存在
    index_file.unlink(missing_ok=True)
    Path(f"{index_file}.lock").unlink(missing_ok=True)

    env = {**os.environ, "GIT_INDEX_FILE": str(index_file)}
    try:
        # 用临时索引：先把远端最新提交读进来，再把页面文件按正常规则加进去
        # （走 git add 而非手工写 blob，CRLF/.gitattributes 的处理才和常规路径一致）
        for args, label in (
            (["read-tree", f"origin/{branch}"], "git read-tree 失败"),
            (["add", "--", *paths], "git add 失败"),
        ):
            r = _git(args, env=env)
            if r is None or r.returncode != 0:
                return False, f"{label}：{_err(r)}"

        unchanged = _git(
            ["diff", "--cached", "--quiet", f"origin/{branch}", "--", *paths], env=env
        )
        if unchanged is not None and unchanged.returncode == 0:
            return True, "页面内容与远端一致，跳过提交"

        tree = _git(["write-tree"], env=env)
        if tree is None or tree.returncode != 0:
            return False, f"git write-tree 失败：{_err(tree)}"

        commit = _git([
            *_ident_args(), "commit-tree", tree.stdout.strip(),
            "-p", f"origin/{branch}", "-m", message,
        ])
        if commit is None or commit.returncode != 0:
            return False, f"git commit-tree 失败：{_err(commit)}"

        ok, err = _push(commit.stdout.strip(), branch)
        if not ok:
            return False, f"git push 失败（已重试）：{err}"
    finally:
        index_file.unlink(missing_ok=True)
        Path(f"{index_file}.lock").unlink(missing_ok=True)
    return True, f"已推送到 origin/{branch}（本地落后，基于远端生成提交，未动本地分支）"


def git_publish(paths=(PAGE_REL,), message=None):
    """提交并推送页面文件。返回 (ok, 说明)，失败不抛异常。"""
    paths = tuple(paths)
    if not (BASE_DIR / ".git").exists():
        return False, "当前目录不是 git 仓库，跳过提交"
    if _git(["--version"]) is None:
        return False, "未找到 git 命令，跳过提交"

    branch = _current_branch()
    message = message or f"docs: 更新早报网页 {datetime.now(BJT).strftime('%Y-%m-%d')}"

    _git(["fetch", "--quiet", "origin", branch])       # 失败（离线）也继续，交给 push 报错
    behind = _git(["rev-list", "--count", f"HEAD..origin/{branch}"])
    stale = (
        behind is not None
        and behind.returncode == 0
        and behind.stdout.strip() not in ("", "0")
    )
    if stale:
        ok, msg = _commit_on_remote(paths, message, branch)
    else:
        ok, msg = _commit_on_head(paths, message, branch)
    return ok, msg


# ================= 对外入口 =================
def publish_page(title, markdown, commit=True, generated_at=None, engine="llm",
                 audio_src=""):
    """渲染并发布当日网页。返回 (ok, 说明)。

    commit=False（dry-run）时只生成 docs/index.html 供本地预览，不提交不推送。
    engine="rule" 表示本次是规则兜底（页面会标注"规则精简版"）。
    audio_src 非空时页面带播放器，并把语音文件与页面一起提交（仓库相对路径）。
    """
    try:
        page = render_html(
            title, markdown, generated_at=generated_at, engine=engine, audio_src=audio_src
        )
        write_page(page)
    except OSError as exc:
        return False, f"写入 {PAGE_REL} 失败：{exc}"
    print(f"[publish] 已生成 {PAGE_REL}（{len(page)} 字符）")
    if not commit:
        return True, f"dry-run：仅生成 {PAGE_REL}，未提交"
    paths = [PAGE_REL] + ([audio_src] if audio_src else [])
    # 提交信息里带上"用的哪个模型/是规则版"，日后 git log 就能回溯某天为什么质量差
    m = re.search(r"\d{4}-\d{2}-\d{2}", title or "")
    date_text = m.group(0) if m else datetime.now(BJT).strftime("%Y-%m-%d")
    detail = f"（{engine}）" if engine else ""
    ok, msg = git_publish(
        paths=tuple(paths), message=f"docs: 更新早报网页 {date_text}{detail}"
    )
    if ok:
        return True, f"{msg} → 网页地址 {SITE_URL}"
    return False, f"网页未发布：{msg}（本次推送退回纯文字版）"


if __name__ == "__main__":
    demo = (
        "【国际】\n\n"
        "1. **示例要点一**\n这是示例介绍，用来在本地预览页面样式。\n\n"
        "2. **示例要点二**\n"
        "【国内】\n\n"
        "1. **示例要点三**\n这是示例介绍。\n"
    )
    print(publish_page("每日新闻早报 预览", demo, commit=False))
