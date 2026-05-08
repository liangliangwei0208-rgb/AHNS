# AHNS

AHNS 是一个个人公开数据建模复盘项目，用于生成每日市场 RSI 图、海外/全球基金模型估算表、安全版公开发布图、海外基金节假日累计观察图，以及面向小白的说明类科普图。

> 本项目仅供个人学习记录，不构成任何投资建议；非实时净值，最终以基金公司公告和销售平台展示为准。

## 功能概览

- 生成纳斯达克、红利低波、上证指数 ETF 等市场 RSI 分析图。
- 基于公开披露持仓、指数/ETF 代理和行情数据，生成海外/全球基金模型估算观察表；国内基金估算业务线已停用。
- 生成 safe 系列公开展示图：不展示基金代码，基金名称脱敏，并保留模型观察限购信息。
- 支持运行前自检，快速检查 Python 环境、关键缓存、水印图片、邮箱配置、依赖和总入口配置。
- 支持按基金代码和估值日期打印完整估算拆解表，区分“股票自身涨跌幅”和“对基金贡献”。
- 自动识别海外基金节假日期间的累计观察场景。
- 在节后第 1 / 第 2 个 A 股交易日生成海外基金净值补更新观察图。
- 每天生成海外基金限额科普图；每周日额外生成海外基金限额表。
- 支持 QQ 邮箱自动发送本次运行生成或更新的图片。
- 支持 GitHub Actions 定时运行、手动触发、缓存自动回推和失败图片 artifact。

## 目录结构

```text
.
├── git_main.py                  # 总控入口
├── check_project.py             # 运行前自检，只检查不修改
├── main.py                      # 主计算入口
├── fund_estimate_breakdown.py    # 基金估算完整拆解查询工具
├── safe_fund.py                 # safe 每日基金图
├── safe_holidays.py             # safe 节假日累计图
├── holidays.py                  # 详细版节假日累计图
├── sum_holidays.py              # 节后补更新观察图
├── stock_analysis.py            # 市场 RSI 图入口
├── kepu/                        # 科普图片脚本
├── tools/                       # 基金估算、缓存、绘图、邮件等模块
├── tools/configs/               # 常维护配置：基金池、代理、映射、RSI、流程等
├── cache/                       # 运行缓存，会被提交并由 Actions 自动更新
└── output/                      # 运行输出图片，不提交
```

## 本地运行

推荐 Python 版本：3.10。

```powershell
& F:\anaconda\envs\py310\python.exe -m pip install -r requirements.txt
```

运行前自检，不联网、不出图、不发邮件：

```powershell
& F:\anaconda\envs\py310\python.exe .\check_project.py
```

完整预演，不发邮件：

```powershell
& F:\anaconda\envs\py310\python.exe .\git_main.py --no-send
```

正式运行并发送邮件：

```powershell
& F:\anaconda\envs\py310\python.exe .\git_main.py
```

临时指定收件人：

```powershell
& F:\anaconda\envs\py310\python.exe .\git_main.py --receiver someone@example.com
```

查看某只基金在指定估值日的完整估算拆解：

```powershell
& F:\anaconda\envs\py310\python.exe .\fund_estimate_breakdown.py
```

运行后按提示输入基金代码和估值日期。也可以直接传参：

```powershell
& F:\anaconda\envs\py310\python.exe .\fund_estimate_breakdown.py 022184
& F:\anaconda\envs\py310\python.exe .\fund_estimate_breakdown.py 022184 2026-05-06
& F:\anaconda\envs\py310\python.exe .\fund_estimate_breakdown.py 022184 --latest
& F:\anaconda\envs\py310\python.exe .\fund_estimate_breakdown.py 022184 2026-05-06 --save-txt
```

## 常用维护入口

- `tools/configs/workflow_configs.py`：维护 `git_main.py` 每天运行哪些脚本、运行顺序、失败后是否中断、图片是否进入邮件候选。
- `tools/configs/fund_universe_configs.py`：维护海外/全球基金池；新增基金代码优先改这里，基金代码请写 6 位字符串。
- `tools/configs/fund_proxy_configs.py`：维护代理型基金和海外有效披露持仓增强系数。
- `tools/configs/security_mappings.py`：维护美股 / 韩国证券映射。
- `tools/configs/rsi_configs.py`：维护 RSI 图标的列表。
- `tools/paths.py`：集中维护常用缓存和输出图片路径。

旧导入路径会尽量保留兼容，例如 `tools/fund_universe.py` 仍可导入基金池，但真实配置已移动到 `tools/configs/fund_universe_configs.py`。

## 邮件配置

项目使用 QQ 邮箱 SMTP 发送图片邮件。公开仓库不保存真实授权码。

配置优先级：

1. 函数参数；
2. 环境变量；
3. 本地未跟踪文件 `tools/email_local_config.py`。

环境变量：

- `QQ_EMAIL_ACCOUNT`：发件 QQ 邮箱，必填。
- `QQ_EMAIL_AUTH_CODE`：QQ 邮箱 SMTP 授权码，必填。
- `QQ_EMAIL_RECEIVER`：收件邮箱，可选；缺失时默认发送给 `QQ_EMAIL_ACCOUNT`。

本地配置方式：

```powershell
Copy-Item .\tools\email_local_config.example.py .\tools\email_local_config.py
```

然后在 `tools/email_local_config.py` 中填入自己的邮箱和授权码。该文件已被 `.gitignore` 忽略，不应提交。

## GitHub Actions

workflow 文件：`.github/workflows/ahns-daily.yml`。

触发方式：

- 手动触发：`workflow_dispatch`
- 定时触发：
  - UTC `0 20 * * *`，北京时间 04:00
  - UTC `0 22 * * *`，北京时间 06:00
  - UTC `0 0 * * *`，北京时间 08:00

运行环境：

- `ubuntu-24.04`
- Python 3.10
- 安装 `requirements.txt`
- 安装中文字体包，保证图片中的中文正常显示

需要在 GitHub 仓库 Settings -> Secrets and variables -> Actions 中配置：

- `QQ_EMAIL_ACCOUNT`
- `QQ_EMAIL_AUTH_CODE`
- `QQ_EMAIL_RECEIVER` 可选

Actions 运行后如 `cache/` 或 `investment_quote_history.json` 发生变化，会自动提交回仓库，提交信息为：

```text
Update runtime cache [skip ci]
```

成功运行不会上传图片 artifact；失败时才上传 `output/*.png` 作为 debug artifact，保留 3 天，避免 Actions 存储持续膨胀。

## 输出图片

常见输出：

- `output/first_pic.png`
- `output/nasdaq_analysis.png`
- `output/nasdaq.png`
- `output/honglidibo_analysis.png`
- `output/honglidibo.png`
- `output/shangzheng_analysis.png`
- `output/shangzheng.png`
- `output/haiwai_fund.png`（详细版当前在主流程中暂不输出，旧文件可能仍存在）
- `output/safe_haiwai_fund.png`
- `output/safe_holidays.png`
- `output/haiwai_holidays.png`
- `output/sum_holidays.png`（详细版已停用，后续不再新生成/覆盖）
- `output/safe_sum_holidays.png`
- `output/kepu_sum_holidays.png`
- `output/kepu_xiane.png`
- `output/xiane.png`（海外基金限额表，仅北京时间周日生成）

Matplotlib 表格和 RSI 图默认使用 180 DPI，科普图使用 Pillow 固定像素并做 PNG 无损压缩。

旧的 `output/guonei*.png` 文件可能仍在本地目录中，但后续主流程不再生成或加入邮件。

## 计算与缓存说明

`cache/` 会提交到仓库，用于减少重复拉取行情和保留基金估算历史。

- 海外/全球基金使用统一 `valuation_anchor_date` 估值锚点；US/CN/HK/KR 都只能使用该锚点对应的完整日线。
- 每个市场先用交易日历判断开闭市，再校验行情接口返回的 `trade_date == valuation_anchor_date`。
- CN/HK 日线优先使用新浪接口：A 股优先 `stock_zh_a_daily` / `fund_etf_hist_sina`，港股优先 `stock_hk_daily`；东方财富接口只作为兜底。
- 普通持仓型海外基金使用“有效持仓增强 + 纳斯达克100补偿仓位”口径，`fund_estimate_breakdown.py` 可打印逐项明细。
- `security_return_cache.json` 对锚点行情使用 `SECURITY:{market}:{ticker}:{valuation_anchor_date}` key，缓存 `traded/closed/pending/missing/stale` 状态。
- `fund_estimate_return_cache.json` 只写海外/全球基金记录，key 为 `overseas:{fund_code}:{valuation_anchor_date}`。
- 指数行情和基金估算历史保留 300 天。
- Actions 运行后会自动回推缓存变化。

## 验证命令

全项目编译：

```powershell
$files = @('.\git_main.py','.\check_project.py','.\main.py','.\fund_estimate_breakdown.py','.\safe_fund.py','.\safe_holidays.py','.\holidays.py','.\sum_holidays.py','.\stock_analysis.py','.\kepu\first_pic.py','.\kepu\kepu_sum_holidays.py','.\kepu\kepu_xiane.py') + (Get-ChildItem .\tools -File -Filter *.py | ForEach-Object { $_.FullName }) + (Get-ChildItem .\tools\configs -File -Filter *.py | ForEach-Object { $_.FullName }); & F:\anaconda\envs\py310\python.exe -m py_compile @files
```

总入口预演：

```powershell
& F:\anaconda\envs\py310\python.exe .\git_main.py --no-send
```

## 免责声明

本项目所有内容均为个人公开数据建模复盘和学习记录。模型估算不等于基金净值公告，不构成任何投资建议、收益承诺或交易依据。基金净值、申购规则、限额信息和公告日期均以基金公司公告及销售平台展示为准。
