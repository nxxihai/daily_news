# 每日新闻早报

每天定时采集多家新闻源，经「质量过滤 → AI 提炼/规则兜底 → 后处理校验」后：
生成网页版（GitHub Pages，见 <https://nxxihai.github.io/daily_news/>）并推送到微信（Server酱）。

## 快速使用

```bash
pip install -r requirements.txt

# 试跑（不推送，仅打印成稿；同时生成本地 docs/index.html 供预览）
python run_daily.py --dry-run

# 正常推送：发网页版 + 微信推送
python run_daily.py
```

> 含中文的调用建议写成 .py 脚本执行（Windows PowerShell 传中文参数易乱码）。

## 文件分工

| 文件 | 职责 |
| --- | --- |
| `collectors.py` | 采集：澎湃新闻、中新网 RSS、新浪新闻、微博热搜、百度热搜、联合早报、聚合数据（需 `JUHE_KEY`）、天行数据（末位备用） |
| `filter.py` | 第一道质量闸：低质/旧闻过滤（娱乐八卦、游戏、体育、个股期货、旧日期） |
| `summarize.py` | 核心提炼：LLM 筛选重写（失败自动降级到规则版）+ 后处理校验 |
| `publish.py` | 网页版：markdown → HTML（`docs/index.html`）→ git 提交推送，GitHub Pages 自动发布 |
| `sender.py` | Server酱 推送（`SEND_KEY`）+ PushPlus 备用 |
| `run_daily.py` | 主流程编排 + Windows 计划任务入口 |
| `setup_schedule.py` | 注册每日 08:00 计划任务 |
| `docs/index.html` | 网页版首页（脚本每天覆盖，永远是最新一期） |
| `tests/` | 回归测试（`python -m unittest discover -s tests -v`） |
| `config.py` | LLM provider / 阈值 / 网页地址 `SITE_URL`（**不存放任何 key**） |
| `local_config.py` | 本地私密 key（已在 `.gitignore`，不提交）；模板见 `local_config.example.py` |

## 网页版（GitHub Pages）

每次运行 `run_daily.py` 都会：

1. 把当天 markdown 渲染成单文件 HTML（CSS 内联、无外链、适配微信内置浏览器、深色模式）
2. 覆盖写入 `docs/index.html` 并提交推送到仓库（提交信息 `docs: 更新早报网页 YYYY-MM-DD`）
3. GitHub Pages 自动发布 → 固定地址 <https://nxxihai.github.io/daily_news/>（永远是最新一期）
4. 微信推送的**正文第一行**即为这个网页链接，方便点开看排版版

一次性设置（只需做一次）：仓库 **Settings → Pages → Source** 选
`Deploy from a branch`，分支 **main**、目录 **/docs**，保存后等约 1 分钟生效。
地址想改就改 `config.py` 里的 `SITE_URL`（同时要跟 Pages 地址一致）。

发布失败不会影响早报：任何一步出错都会打印原因并**降级成纯文字推送**（不附链接，避免点到旧页面）。
本地仓库长期没 `git pull`（早报机器人一直在提交）时，脚本会基于远端最新提交生成一个只改页面的
提交再推送，全程不动本地分支和工作区，所以本地计划任务也不会因为"落后"而推不上去。

## AI 提炼失败时的行为（丢弃 + 重来）

云端 provider 偶发超时/限流（实测有整点全挂、整篇退化成规则版的情况）。流程会：

1. **丢弃**退回规则版的结果，等 45 秒重来，最多尝试 3 次
   （`config.LLM_MAX_ATTEMPTS` / `LLM_RETRY_DELAY` 可调），日志里能看到
   `AI 提炼第 1/3 次失败（本次结果已丢弃），45 秒后重试…`
2. 3 次都失败才退回"规则精简版"，**推送标题会带「（精简版）」**，页面 meta 也会标注
3. 想知道当天到底用了什么通道：看 `git log` 里页面提交的信息，如
   `docs: 更新早报网页 2026-10-12（deepseek-chat）` 或 `（rule）`

想改成"AI 不可用就当天不推（宁可断更也不发低质内容）"：把 `config.PUSH_RULE_FALLBACK`
设为 `False`，或在环境变量/Secrets 里设 `PUSH_RULE_FALLBACK=0`。

## 本机网络备忘（Steam++ / Watt Toolkit）

- Python 请求报 `CERTIFICATE_VERIFY_FAILED`（LLM 通道、新浪源集体 SSL 失败）：
  它的系统代理会做 HTTPS 中间人，本机证书库不认它的自签 CA →
  在 `local_config.py` 里设 `BYPASS_SYSTEM_PROXY = True`（本机脚本直连即可）
- `git push` 报 `Failed to connect to github.com port 443` / `Connection was reset`：
  它把 github.com 指到本地反代、而反代偶发不响应 →
  先重启 Steam++ 的"网络加速"（停止→启动）；急用时让 git 直接走它的代理端口：
  `git -c http.proxy=http://127.0.0.1:26561 push`

## 密钥管理（仓库是公开的）

GitHub Pages 免费版要求仓库公开，所以 **所有 key 一律不写进代码**，只在两处：

| 场景 | 放在哪 |
| --- | --- |
| 本地手动 / 计划任务 | `local_config.py`（已 gitignore，复制 `local_config.example.py` 填值） |
| 云端 GitHub Actions | 仓库 Settings → Secrets and variables → Actions（同名环境变量） |

读取顺序：**环境变量 → `local_config.py` → 空串**（没配就自动跳过该功能，不会报错）。
`config.py` 里只留 `_secret("XXX")` 调用，clone 下来的人看不到任何 key。

> 安全提示：换成公开仓库前，请到对应官网**重置**曾提交过的 key（历史提交里仍留着旧值，
> 删代码不等于失效）——天行数据换 key、Server酱重置 SendKey，然后同步更新 GitHub Secrets
> 和本地 `local_config.py`。

## 质量规则维护位置（重要）

早报成稿质量主要受下面几处规则影响，日常调优改这些地方即可：

1. **源头拦截** `filter.py`
   - `LOW_VALUE_PATTERNS` / `instant_filter` / `GOSSIP_KEYWORDS` / `INVESTMENT_KEYWORDS`：
     个股/股东增减持、公司财季财报、网络热梗、游戏娱乐等一律在源头滤掉。
2. **AI 写作纪律** `summarize.py` → `build_prompt()`
   - 要求每条摘要必须写出标题之外的事实，禁止复述标题；同一热点主线只保留一条。
3. **后处理校验** `summarize.py`
   - `_is_title_repeat()`：识别「摘要=标题换个说法」的灌水（字符复用率 >=75% 且篇幅接近）。
   - `_POST_FILTER_BAN`：LLM 输出/规则补充的禁止词清单（财报、股市、企业软广等）。
   - `_MAINLINE_REGIONS` / `_MAINLINE_DOMAINS`：同地区+同动作域（军事/外交/灾害）的新闻
     收敛为一条，避免美伊冲突等主线连续刷屏。
   - `_fill_missing_sections()`：栏内不足时用规则补充；无简介但标题完整(>=9字)的重大新闻
     以「标题单独成行」方式补入（如联合早报源），宁缺毋滥只针对残缺短标题。
   - `_rich_summary()`：判断条目是否携带可用的实质简介（非空、非复述、>=15字）。
4. **展示** `summarize.py` → `sections_to_markdown()`
   - 摘要为空/为标题复述时输出纯标题行，避免「标题：标题」重复观感。

## AI 通道与降级策略

通道按顺序尝试，前一个不可用自动换下一个（`config.py` 的 `LLM_PROVIDERS`）：

1. **DeepSeek 官方**（推荐付费）：约 1-2 元/百万 tokens，每天一次成本可忽略，格式遵循稳定。
   注册 <https://platform.deepseek.com> → 创建 key → 设环境变量 `DEEPSEEK_API_KEY`
   （或直接填进 `config.py` 里 `os.environ.get("DEEPSEEK_API_KEY") or ""`）。
2. FreeLLMAPI / 自建 OpenAI 兼容接口（配了才启用）
3. 魔搭 ModelScope：Step-3.7-Flash、Qwen3-8B（免费，国内直连，偶尔超时会自动重试一次）
4. 智谱：glm-4-flash、glm-4.5-flash（免费）
5. OpenRouter 免费模型（需自建 `OPENROUTER_API_KEY`；旧写死的 key 已失效被移除）
6. 本地 Ollama（需本机启动 Ollama 并 pull 模型）

单个模型不稳定时不必换架构，项目已内置三层保护：

- **格式不达标**：用强提示重试 1 次（明确要求摘要写具体事实、禁止复述标题）
- **部分结果采用**：模型漏输出某栏（如国际栏 0 条）时，不再整篇丢弃，
  而是重新分类 + 后处理 + 规则补齐缺失栏位，比直接退回全规则版更稳
- **规则兜底**：所有通道都失败时走 `rule_summary()`，输出 24 条「标题+一句话」，
  同样经过低质过滤、同主线收敛、半句拦截，保证不会断更

> 提示：免费模型可用性波动大（SSL/超时/429/400 都遇到过）。想要每天稳定成稿，
> 建议配一个 DeepSeek（或智谱）的付费 key，成本极低。

## 回归测试

```bash
python -m unittest discover -s tests -v
```

`tests/test_quality_guard_2026.py` 是针对 2026-09-02 推送质量问题的回归用例（个股增持、
外国财季财报、网络热梗、摘要复述、同主线刷屏等），新增调优后请确保其通过。
