# AHNS 项目接手说明

更新时间：2026-05-08

本项目用于生成每日市场分析图、海外/全球基金模型估算表、安全版公开发布图、海外基金节假日累计观察图、节后补更新观察图，以及面向小白的科普说明图。国内基金收益预估业务线已停用，但 A 股/港股/韩国行情能力仍保留用于海外/全球基金持仓估算。默认运行环境为：

```powershell
& F:\anaconda\envs\py310\python.exe <script.py>
```

## 操作约束

- 禁止批量删除文件或目录。
- 不要使用 `del /s`、`rd /s`、`rmdir /s`、`Remove-Item -Recurse`、`rm -rf`。
- 需要删除文件时，只能一次删除一个明确路径的文件，例如：
  ```powershell
  Remove-Item "C:\path\to\file.txt"
  ```
- 如果需要批量删除文件，应停止操作，并请用户手动删除。
- 尽量只改用户点名的文件；不要改 `main.py`，除非用户明确要求。
- 不要把 `cache/`、`output/`、`__pycache__/` 视为源码依据；它们是运行产物或缓存。
- 不要把真实 QQ 邮箱 SMTP 授权码写入可提交源码；本地使用 `tools/email_local_config.py` 或环境变量。

## 当前工作流

推荐总入口：

```powershell
& F:\anaconda\envs\py310\python.exe .\git_main.py
```

运行前自检：

```powershell
& F:\anaconda\envs\py310\python.exe .\check_project.py
```

预演不发邮件：

```powershell
& F:\anaconda\envs\py310\python.exe .\git_main.py --no-send
```

`git_main.py` 的运行顺序由 `tools/configs/workflow_configs.py` 维护。想调整每日运行脚本、脚本顺序、失败后是否中断、某一步生成的图片是否进入邮件候选，优先改这个配置文件，不要直接改总入口主逻辑。

当前默认运行顺序：

1. `kepu/first_pic.py`
2. `main.py`
3. `safe_fund.py`
4. `safe_holidays.py`
5. `holidays.py`
6. `sum_holidays.py`
7. `kepu/kepu_sum_holidays.py`
8. `kepu/kepu_xiane.py`

`git_main.py` 会扫描 `output/` 中本次新生成或更新的图片，并通过 `tools/email_send.py` 发送邮件。邮件发送保留“正文内嵌图片 + 附件图片”的方式；发送前会打印图片数量、单张大小和总大小。

`check_project.py` 是只读体检工具：检查 Python 环境、关键目录、`cache/mark.jpg`、核心缓存、邮箱配置、依赖导入、Git 状态和总入口配置。它不联网、不拉行情、不出图、不写缓存、不发邮件、不删除文件、不提交 Git。

## 关键文件

- `git_main.py`：项目总控入口，顺序运行全部脚本，收集本次图片并发送邮件；支持 `--no-send` 和 `--receiver`。
- `check_project.py`：运行前自检入口，只检查不修改，用于确认环境、缓存、依赖、邮箱配置和流程配置是否基本正常。
- `main.py`：主计算入口，生成市场 RSI 图、海外/全球基金详细估算图，并写入 `cache/fund_estimate_return_cache.json`。
- `fund_estimate_breakdown.py`：只读缓存的估算拆解工具；运行后可手工输入基金代码和估值日期，打印完整持仓贡献表，也支持 `--latest` 和 `--save-txt`。
- `safe_fund.py`：只读基金估算缓存，生成安全版海外/全球每日基金估算图。
- `safe_holidays.py`：只读缓存，生成安全版海外节假日累计观察图。
- `holidays.py`：只读缓存，生成详细版海外节假日累计观察图。
- `sum_holidays.py`：只读缓存，生成节后第 1 / 第 2 个 A 股交易日的海外基金补更新观察图。
- `kepu/first_pic.py`：生成“基金预估图怎么看？”科普首图。
- `kepu/kepu_sum_holidays.py`：生成节后海外基金补更新规则科普图，仅节后第 1 / 第 2 个 A 股交易日出图。
- `kepu/kepu_xiane.py`：每天生成海外基金限额科普图；限额表格图仅北京时间周日生成。
- `tools/email_send.py`：QQ 邮箱发送模块；环境变量优先，其次读取未跟踪的本地配置文件。
- `tools/get_top10_holdings.py`：基金估算、持仓读取、锚点行情收益、缓存写入、基金表格绘图的核心实现。
- `tools/fund_estimator.py`：历史兼容模块，动态转发到 `tools/get_top10_holdings.py`；新增核心逻辑优先看 `tools/get_top10_holdings.py`。
- `tools/fund_history_io.py`：海外基金历史缓存读取、交易日识别、区间累计和累计表格绘图。
- `tools/paths.py`：集中维护 `cache/`、`output/` 和常用缓存/输出图片路径。
- `tools/safe_display.py`：safe 图脱敏、居中 logo 水印和“鱼师AHNS”品牌文字水印工具。
- `tools/configs/`：集中维护常改配置，包括基金池、代理基金、证券映射、RSI 配置、交易日历参数和总入口运行流程。
- `tools/rsi_data.py` / `stock_analysis.py`：市场指数、ETF 行情分析和 RSI 图表。

## 配置维护入口

- `tools/configs/fund_universe_configs.py`：海外/全球基金池和历史国内基金池。新增、删除基金代码优先改这里；基金代码请写 6 位字符串，避免前导 0 丢失。
- `tools/configs/fund_proxy_configs.py`：代理型基金配置和海外有效披露持仓增强系数。
- `tools/configs/security_mappings.py`：美股 / 韩国证券代码映射；韩国六位数字代码需要配合名称别名匹配，避免误判 A 股。
- `tools/configs/rsi_configs.py`：市场 RSI 图标的配置。
- `tools/configs/market_calendar_configs.py`：市场交易日历名称、收盘缓冲、韩国节假日置零策略。
- `tools/configs/workflow_configs.py`：`git_main.py` 每日运行流程。新增脚本时复制一项并改 `name` / `script`；想让某一步只生成不发邮件，改 `collect_images=False`。

旧入口会尽量保留兼容，例如 `tools/fund_universe.py` 仍可导入 `HAIWAI_FUND_CODES`，但真实配置已移动到 `tools/configs/fund_universe_configs.py`。

## 输出图片

- 科普首图：`output/first_pic.png`
- RSI / 市场图：
  - `output/nasdaq_analysis.png`
  - `output/nasdaq.png`
  - `output/honglidibo_analysis.png`
  - `output/honglidibo.png`
  - `output/shangzheng_analysis.png`
  - `output/shangzheng.png`
- 基金详细估算图：
  - `output/haiwai_fund.png`（详细版当前在主流程中暂不输出，旧文件可能仍在本地）
- safe 每日图：
  - `output/safe_haiwai_fund.png`
- 海外节假日累计图：
  - `output/haiwai_holidays.png`
  - `output/safe_holidays.png`
- 节后补更新观察图：
  - `output/sum_holidays.png`（详细版已停用，后续不再新生成/覆盖）
  - `output/safe_sum_holidays.png`
- 科普图：
  - `output/kepu_sum_holidays.png`
  - `output/kepu_xiane.png`
  - `output/xiane.png`

旧的 `output/guonei*.png` 文件可能仍在本地目录中，但后续主流程不再生成或加入邮件。不要为了清理旧输出而批量删除文件。

Matplotlib 表格和 RSI 图默认使用 `180 DPI`，用于降低图片体积并保持手机端清晰度。`kepu/` 下科普图是 Pillow 固定像素图，保存时使用 PNG 无损压缩，不靠 DPI 控制尺寸。

## 邮件与 GitHub Actions

- `tools/email_send.py` 不保存真实授权码；本地真实配置放在未跟踪的 `tools/email_local_config.py`。
- 环境变量优先级高于本地配置文件：
  - `QQ_EMAIL_ACCOUNT`
  - `QQ_EMAIL_AUTH_CODE`
  - `QQ_EMAIL_RECEIVER` 可选，缺失时默认发送给 `QQ_EMAIL_ACCOUNT`
- GitHub Repository secrets 只有在 workflow 中显式映射成环境变量才会生效。
- 公开仓库提交前必须确认源码中没有真实 SMTP 授权码。
- 当前 SMTP timeout 默认 `120s`。如果 SMTP 登录正常但发送失败，常见原因是邮件体积较大、网络较慢或服务端中途断开。

## safe 系列现状

- `safe_fund.py`：
  - 只读取 `cache/fund_estimate_return_cache.json`。
  - 只读取 `market_group == "overseas"` 的最新缓存。
  - 不显示基金代码；基金名称脱敏；保留模型观察限购信息列，便于公开图解释限购状态。
  - 基金名称使用 `tools.safe_display.mask_fund_name()` 脱敏。
  - 海外图保留 benchmark footer。
  - 输出保持基金预估表格风格，并叠加 `cache/mark.jpg` 居中淡 logo 和 85 号非加粗斜向“鱼师AHNS”文字水印。
- `safe_holidays.py`：
  - 自动判断 A 股是否休市：优先 AkShare A 股交易日历，失败时用本地国内行情缓存兜底。
  - 只读取 `main.py` 已写入的海外基金和 benchmark 缓存。
  - 满足条件才出图；否则只打印原因，不生成新图。
- `sum_holidays.py`：
  - 只读取缓存，不拉行情、不重新计算持仓、不写缓存。
  - 只生成 `output/safe_sum_holidays.png`，不再生成或覆盖详细版 `output/sum_holidays.png`。
  - 普通周六周日不属于节假日累计收益场景。
  - 节后第 1 个 A 股交易日：读取节前最后一个 A 股交易日对应的海外基金估值日，生成单日观察图。
  - 节后第 2 个 A 股交易日：累计节前最后估值日之后到缓存中最新海外估值日的实际存在记录。
  - 节后第 3 个 A 股交易日起：不生成图，回归 `main.py` / `safe_fund.py` 的普通每日节奏。

## 计算口径摘要

- 普通持仓型基金：读取公开披露的季度前十大持仓，按持仓权重和证券涨跌幅估算。
- 代理型基金：若基金在 `DEFAULT_FUND_PROXY_MAP` 中，使用相关 ETF / 指数代理资产和配置权重估算。
- 海外 / QDII 基金：使用统一 `valuation_anchor_date` 作为估值锚点；北京时间运行日记录为 `run_date_bj`。
- 估值锚点由 US/CN/HK/KR 中最近一个已确认完整交易日决定；各市场再分别判断该锚点是 `traded/closed/pending/missing/stale`。
- 所有海外/全球基金估算只使用完整日线，不使用 A 股、港股或韩国盘中实时行情。
- 如果某市场在锚点日休市，该市场持仓贡献为 0；如果应开盘但行情缺失或 stale，也贡献 0，并将基金记录标记为 partial/stale，后续可被更完整数据覆盖。
- A 股、港股日线优先使用新浪接口：A 股优先 `ak.stock_zh_a_daily` / `ak.fund_etf_hist_sina`，港股优先 `ak.stock_hk_daily`；东方财富接口只作为兜底。
- 普通海外股票持仓型基金保留“有效持仓增强 + 纳斯达克100补偿仓位 + 100% 权重封顶”口径；补偿仓位只使用锚点日纳斯达克100完整日线。
- 区间累计收益使用复利：
  `累计 = (prod(1 + 每日估算收益率 / 100) - 1) * 100`
- 同一基金、同一 `valuation_anchor_date` 只计入一次；优先数据质量更高、`complete` 和更高 `completeness_score` 的记录。
- 海外六位数股票代码可能会被识别为 A 股；当前按用户选择不修复，允许失败后走纳斯达克100补偿口径。

## 缓存策略

- `security_return_cache.json`：
  - 新锚点 key 为 `SECURITY:{market}:{ticker}:{valuation_anchor_date}`。
  - `traded` / `closed` 可长期缓存；`pending` / `missing` / `stale` 短期缓存，后续允许重试。
  - 已有 `traded` 记录不被 `pending` / `missing` / `stale` 覆盖。
  - 小时桶缓存保留 15 天。
  - 普通证券日缓存保留 30 天。
  - 指数行情缓存保留 300 天。
  - 无法解析日期的缓存项保留，避免误删有效缓存。
- `fund_estimate_return_cache.json`：
  - 只缓存海外/全球基金，不再缓存国内基金。
  - 基金 key 为 `overseas:{fund_code}:{valuation_anchor_date}`，同时保留兼容字段 `valuation_date`。
  - 覆盖规则由数据质量驱动，不再使用 15:30 冻结逻辑。
  - `records` 和 `benchmark_records` 保留最近 300 天。
  - 按 `valuation_anchor_date` / 兼容字段 `valuation_date` 裁剪；缺失时回退 `run_date_bj`。
- `fund_holdings_cache.json` 和 `fund_purchase_limit_cache.json` 按基金代码覆盖或按既有策略更新，不做批量删除。

## 抖音发布注意

- safe 图降低风险，但不能保证账号一定不被误判。
- 发文避免“推荐、买入、卖出、加仓、跟投、稳赚、私信、进群、课程、带单、领取资料”等表达。
- 文案建议保持：
  `个人公开数据建模复盘，不收费、不荐基、不带单、不拉群，不构成任何投资建议。非实时净值，最终以基金公司公告为准。`
- 第一张建议放 `kepu/first_pic.py` 生成的说明图，后续再放 safe 系列估算图或科普图。
- 不建议公开展示完整基金代码、强排序榜单或过强红绿刺激。

## 常用验证命令

全项目编译：

```powershell
$files = @('.\git_main.py','.\check_project.py','.\main.py','.\fund_estimate_breakdown.py','.\safe_fund.py','.\safe_holidays.py','.\holidays.py','.\sum_holidays.py','.\stock_analysis.py','.\kepu\first_pic.py','.\kepu\kepu_sum_holidays.py','.\kepu\kepu_xiane.py') + (Get-ChildItem .\tools -File -Filter *.py | ForEach-Object { $_.FullName }) + (Get-ChildItem .\tools\configs -File -Filter *.py | ForEach-Object { $_.FullName }); & F:\anaconda\envs\py310\python.exe -m py_compile @files
```

运行前自检：

```powershell
& F:\anaconda\envs\py310\python.exe .\check_project.py
```

总入口预演：

```powershell
& F:\anaconda\envs\py310\python.exe .\git_main.py --no-send
```

单独生成常用图：

```powershell
& F:\anaconda\envs\py310\python.exe .\main.py
& F:\anaconda\envs\py310\python.exe .\kepu\first_pic.py
& F:\anaconda\envs\py310\python.exe .\safe_fund.py
& F:\anaconda\envs\py310\python.exe .\safe_holidays.py
& F:\anaconda\envs\py310\python.exe .\fund_estimate_breakdown.py
```

节后补更新测试：

```powershell
& F:\anaconda\envs\py310\python.exe .\sum_holidays.py --today 2026-05-06
& F:\anaconda\envs\py310\python.exe .\sum_holidays.py --today 2026-05-07
& F:\anaconda\envs\py310\python.exe .\sum_holidays.py --today 2026-05-08
```

科普图测试：

```powershell
& F:\anaconda\envs\py310\python.exe .\kepu\kepu_sum_holidays.py --today 2026-05-06
& F:\anaconda\envs\py310\python.exe .\kepu\kepu_xiane.py --today 2026-05-08
& F:\anaconda\envs\py310\python.exe .\kepu\kepu_xiane.py --today 2026-05-10
```

## 最近完成的改动

- 新增 `git_main.py` 总控入口，支持全流程运行、图片收集、邮件发送和 `--no-send` 预演。
- `first_pic.py` 已迁移到 `kepu/first_pic.py`，输出 `output/first_pic.png`。
- 新增 `sum_holidays.py`，用于节后第 1 / 第 2 个 A 股交易日的海外基金补更新观察图。
- 新增 `kepu/kepu_sum_holidays.py`，用于解释节后海外基金预估收益率的更新节奏。
- 新增 `kepu/kepu_xiane.py`，每天生成海外基金限额科普图，并在北京时间周日生成海外基金限额表。
- `tools/get_top10_holdings.py` 已加入锚点行情缓存裁剪、基金估算缓存元数据字段写入和质量驱动覆盖逻辑。
- 表格类图片和 RSI 图默认降为 `180 DPI`；科普图使用 PNG 无损压缩保存。
- `tools/email_send.py` 支持环境变量和本地未跟踪配置文件，SMTP timeout 默认 `120s`。
- `main.py` 已增加 `main()` 入口保护，导入该文件不会自动拉行情或生成图片。
- 海外/全球基金估算已重构为统一 `valuation_anchor_date` 锚点口径；国内基金估算业务线停用，但 CN/HK/KR 行情能力保留。
- 新增 `pandas_market_calendars`，使用 US/CN/HK/KR 交易日历并对行情 `trade_date` 做二次校验。
- A 股和港股日线优先级调整为新浪接口优先，东方财富接口仅作为兜底。
- 新增 `fund_estimate_breakdown.py`，可交互输入基金代码和估值日期，打印完整持仓收益拆解，并可保存 txt。
- 新增 `tools/configs/` 配置目录，已迁移 RSI 配置、证券映射、代理基金配置、交易日历参数、基金池和总入口运行流程。
- 新增 `tools/paths.py` 集中维护常用路径；safe 系列水印流程统一封装到 `tools.safe_display.apply_safe_public_watermarks()`。
- 新增 `check_project.py` 运行前自检工具，只检查不修改。
- `sum_holidays.py` 后续只生成 `output/safe_sum_holidays.png`，不再生成详细版 `output/sum_holidays.png`。
- `kepu/kepu_xiane.py` 每天生成科普图，只有北京时间周日生成限额表格图。
