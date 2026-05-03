"""
本地邮箱配置示例。

使用方法：
1. 复制本文件为 tools/email_local_config.py；
2. 填入自己的 QQ 邮箱和 SMTP 授权码；
3. 不要提交 tools/email_local_config.py。
"""

SENDER_EMAIL = "your_qq_number@qq.com"
AUTH_CODE = "your_qq_smtp_auth_code"

# 可选。留空时默认发送给 SENDER_EMAIL。
DEFAULT_RECEIVER = ""
