# AHNS

AHNS 是一个个人公开数据建模复盘项目，用于生成每日市场 RSI 图、海外/国内基金模型估算表、安全版公开发布图、海外基金节假日累计观察图，以及面向小白的说明类科普图。

> 本项目仅供个人学习记录，不构成任何投资建议；非实时净值，最终以基金公司公告和销售平台展示为准。

## 功能概览

- 生成纳斯达克、红利低波、上证指数 ETF 等市场 RSI 分析图。
- 基于公开披露持仓、指数/ETF 代理和行情数据，生成海外/国内基金模型估算观察表。
- 生成 safe 系列公开展示图，弱化基金代码和限额信息展示。
- 自动识别海外基金节假日期间的累计观察场景。
- 在节后第 1 / 第 2 个 A 股交易日生成海外基金净值补更新观察图。
- 每周六生成海外基金限额科普图和限额表。
- 支持 QQ 邮箱自动发送本次运行生成或更新的图片。
- 支持 GitHub Actions 定时运行、手动触发、缓存自动回推和失败图片 artifact。

## 目录结构

```text
.
├── git_main.py                  # 总控入口
├── main.py                      # 主计算入口
├── safe_fund.py                 # safe 每日基金图
├── safe_holidays.py             # safe 节假日累计图
├── holidays.py                  # 详细版节假日累计图
├── sum_holidays.py              # 节后补更新观察图
├── stock_analysis.py            # 市场 RSI 图入口
├── kepu/                        # 科普图片脚本
├── tools/                       # 基金估算、缓存、绘图、邮件等模块
├── cache/                       # 运行缓存，会被提交并由 Actions 自动更新
└── output/                      # 运行输出图片，不提交
```

## 本地运行

推荐 Python 版本：3.10。

```powershell
& C:\Users\weili\.conda\envs\py310\python.exe -m pip install -r requirements.txt
```

完整预演，不发邮件：

```powershell
& C:\Users\weili\.conda\envs\py310\python.exe .\git_main.py --no-send
```

正式运行并发送邮件：

```powershell
& C:\Users\weili\.conda\envs\py310\python.exe .\git_main.py
```

临时指定收件人：

```powershell
& C:\Users\weili\.conda\envs\py310\python.exe .\git_main.py --receiver someone@example.com
```

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
- `output/haiwai_fund.png`
- `output/guonei_fund.png`
- `output/safe_haiwai_fund.png`
- `output/safe_guonei_fund.png`
- `output/safe_holidays.png`
- `output/haiwai_holidays.png`
- `output/sum_holidays.png`
- `output/safe_sum_holidays.png`
- `output/kepu_sum_holidays.png`
- `output/kepu_xiane.png`
- `output/xiane.png`

Matplotlib 表格和 RSI 图默认使用 180 DPI，科普图使用 Pillow 固定像素并做 PNG 无损压缩。

## 缓存说明

`cache/` 会提交到仓库，用于减少重复拉取行情和保留基金估算历史。

- 小时行情缓存保留 15 天。
- 普通证券日缓存保留 30 天。
- 指数行情和基金估算历史保留 300 天。
- Actions 运行后会自动回推缓存变化。

## 免责声明

本项目所有内容均为个人公开数据建模复盘和学习记录。模型估算不等于基金净值公告，不构成任何投资建议、收益承诺或交易依据。基金净值、申购规则、限额信息和公告日期均以基金公司公告及销售平台展示为准。
