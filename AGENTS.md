# AHNS 项目接手说明

更新时间：2026-05-03

本项目用于生成每日市场分析图、基金模型估算表、安全版公开发布图、海外基金节假日累计观察图、节后补更新观察图，以及面向小白的科普说明图。默认运行环境为：

```powershell
& C:\Users\weili\.conda\envs\py310\python.exe <script.py>
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
& C:\Users\weili\.conda\envs\py310\python.exe .\git_main.py
```

预演不发邮件：

```powershell
& C:\Users\weili\.conda\envs\py310\python.exe .\git_main.py --no-send
```

`git_main.py` 当前运行顺序：

1. `kepu/first_pic.py`
2. `main.py`
3. `safe_fund.py`
4. `safe_holidays.py`
5. `holidays.py`
6. `sum_holidays.py`
7. `kepu/kepu_sum_holidays.py`
8. `kepu/kepu_xiane.py`

`git_main.py` 会扫描 `output/` 中本次新生成或更新的图片，并通过 `tools/email_send.py` 发送邮件。邮件发送保留“正文内嵌图片 + 附件图片”的方式；发送前会打印图片数量、单张大小和总大小。

## 关键文件

- `git_main.py`：项目总控入口，顺序运行全部脚本，收集本次图片并发送邮件；支持 `--no-send` 和 `--receiver`。
- `main.py`：主计算入口，生成市场 RSI 图、海外/国内基金详细估算图，并写入 `cache/fund_estimate_return_cache.json`。
- `safe_fund.py`：只读基金估算缓存，生成安全版每日基金估算图。
- `safe_holidays.py`：只读缓存，生成安全版海外节假日累计观察图。
- `holidays.py`：只读缓存，生成详细版海外节假日累计观察图。
- `sum_holidays.py`：只读缓存，生成节后第 1 / 第 2 个 A 股交易日的海外基金补更新观察图。
- `kepu/first_pic.py`：生成“基金预估图怎么看？”科普首图。
- `kepu/kepu_sum_holidays.py`：生成节后海外基金补更新规则科普图，仅节后第 1 / 第 2 个 A 股交易日出图。
- `kepu/kepu_xiane.py`：生成海外基金限额科普图和限额表，仅北京时间周六出图。
- `tools/email_send.py`：QQ 邮箱发送模块；环境变量优先，其次读取未跟踪的本地配置文件。
- `tools/fund_estimator.py`：基金估算、持仓读取、行情收益、缓存写入、基金表格绘图的核心实现。
- `tools/fund_history_io.py`：海外基金历史缓存读取、交易日识别、区间累计和累计表格绘图。
- `tools/rsi_data.py` / `stock_analysis.py`：市场指数、ETF 行情分析和 RSI 图表。

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
  - `output/haiwai_fund.png`
  - `output/guonei_fund.png`
- safe 每日图：
  - `output/safe_haiwai_fund.png`
  - `output/safe_guonei_fund.png`
- 海外节假日累计图：
  - `output/haiwai_holidays.png`
  - `output/safe_holidays.png`
- 节后补更新观察图：
  - `output/sum_holidays.png`
  - `output/safe_sum_holidays.png`
- 科普图：
  - `output/kepu_sum_holidays.png`
  - `output/kepu_xiane.png`
  - `output/xiane.png`

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
  - 分别读取 `market_group == "overseas"` 和 `market_group == "domestic"` 的最新缓存。
  - 不显示基金代码、不显示限购金额。
  - 基金名称使用 `tools.safe_display.mask_fund_name()` 脱敏。
  - 海外图保留 benchmark footer。
  - 输出保持基金预估表格风格，并叠加品牌水印和风险提示水印。
- `safe_holidays.py`：
  - 自动判断 A 股是否休市：优先 AkShare A 股交易日历，失败时用本地国内行情缓存兜底。
  - 只读取 `main.py` 已写入的海外基金和 benchmark 缓存。
  - 满足条件才出图；否则只打印原因，不生成新图。
- `sum_holidays.py`：
  - 只读取缓存，不拉行情、不重新计算持仓、不写缓存。
  - 普通周六周日不属于节假日累计收益场景。
  - 节后第 1 个 A 股交易日：读取节前最后一个 A 股交易日对应的海外基金估值日，生成单日观察图。
  - 节后第 2 个 A 股交易日：累计节前最后估值日之后到缓存中最新海外估值日的实际存在记录。
  - 节后第 3 个 A 股交易日起：不生成图，回归 `main.py` / `safe_fund.py` 的普通每日节奏。

## 计算口径摘要

- 普通持仓型基金：读取公开披露的季度前十大持仓，按持仓权重和证券涨跌幅估算。
- 代理型基金：若基金在 `DEFAULT_FUND_PROXY_MAP` 中，使用相关 ETF / 指数代理资产和配置权重估算。
- 海外 / QDII 基金：以海外 benchmark 的实际 `valuation_date` 作为估值日；北京时间运行日记录为 `run_date_bj`。
- 海外持仓中 A 股或港股若在对应海外估值日没有新行情，收益按 0 计入，避免节假日期间重复计算旧涨跌幅。
- 区间累计收益使用复利：
  `累计 = (prod(1 + 每日估算收益率 / 100) - 1) * 100`
- 同一基金、同一 `valuation_date` 只计入一次；优先 final，其次取较晚 intraday。
- 海外六位数股票代码可能会被识别为 A 股；当前按用户选择不修复，允许失败后走纳斯达克100补偿口径。

## 缓存策略

- `security_return_cache.json`：
  - 小时桶缓存保留 15 天。
  - 普通证券日缓存保留 30 天。
  - 指数行情缓存保留 300 天。
  - 无法解析日期的缓存项保留，避免误删有效缓存。
- `fund_estimate_return_cache.json`：
  - `records` 和 `benchmark_records` 保留最近 300 天。
  - 按 `valuation_date` 裁剪；缺失时回退 `run_date_bj`。
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
& C:\Users\weili\.conda\envs\py310\python.exe -m py_compile .\git_main.py .\main.py .\safe_fund.py .\safe_holidays.py .\holidays.py .\sum_holidays.py .\stock_analysis.py .\kepu\first_pic.py .\kepu\kepu_sum_holidays.py .\kepu\kepu_xiane.py .\tools\*.py
```

总入口预演：

```powershell
& C:\Users\weili\.conda\envs\py310\python.exe .\git_main.py --no-send
```

单独生成常用图：

```powershell
& C:\Users\weili\.conda\envs\py310\python.exe .\kepu\first_pic.py
& C:\Users\weili\.conda\envs\py310\python.exe .\safe_fund.py
& C:\Users\weili\.conda\envs\py310\python.exe .\safe_holidays.py
```

节后补更新测试：

```powershell
& C:\Users\weili\.conda\envs\py310\python.exe .\sum_holidays.py --today 2026-05-06
& C:\Users\weili\.conda\envs\py310\python.exe .\sum_holidays.py --today 2026-05-07
& C:\Users\weili\.conda\envs\py310\python.exe .\sum_holidays.py --today 2026-05-08
```

科普图测试：

```powershell
& C:\Users\weili\.conda\envs\py310\python.exe .\kepu\kepu_sum_holidays.py --today 2026-05-06
& C:\Users\weili\.conda\envs\py310\python.exe .\kepu\kepu_xiane.py --today 2026-05-02
```

## 最近完成的改动

- 新增 `git_main.py` 总控入口，支持全流程运行、图片收集、邮件发送和 `--no-send` 预演。
- `first_pic.py` 已迁移到 `kepu/first_pic.py`，输出 `output/first_pic.png`。
- 新增 `sum_holidays.py`，用于节后第 1 / 第 2 个 A 股交易日的海外基金补更新观察图。
- 新增 `kepu/kepu_sum_holidays.py`，用于解释节后海外基金预估收益率的更新节奏。
- 新增 `kepu/kepu_xiane.py`，用于每周六生成海外基金限额科普图和限额表。
- `tools/fund_estimator.py` 已加入缓存裁剪逻辑，并修复基金估算缓存元数据字段写入。
- 表格类图片和 RSI 图默认降为 `180 DPI`；科普图使用 PNG 无损压缩保存。
- `tools/email_send.py` 支持环境变量和本地未跟踪配置文件，SMTP timeout 默认 `120s`。
- `main.py` 已增加 `main()` 入口保护，导入该文件不会自动拉行情或生成图片。
