"""
QQ 邮箱发送模块。

配置优先级：
1. 函数参数；
2. 环境变量：QQ_EMAIL_ACCOUNT、QQ_EMAIL_AUTH_CODE、QQ_EMAIL_RECEIVER；
3. 本地未跟踪配置文件：tools/email_local_config.py。

QQ_EMAIL_RECEIVER 可选；缺失时默认发送给最终解析出的发件邮箱。
不要把真实 SMTP 授权码提交到公开仓库。
"""

from __future__ import annotations

import mimetypes
import os
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formatdate, make_msgid
from pathlib import Path
from typing import Any, Sequence


SENDER_EMAIL = ""
AUTH_CODE = ""
DEFAULT_RECEIVER = ""

ENV_EMAIL_ACCOUNT = "QQ_EMAIL_ACCOUNT"
ENV_EMAIL_AUTH_CODE = "QQ_EMAIL_AUTH_CODE"
ENV_EMAIL_RECEIVER = "QQ_EMAIL_RECEIVER"

SMTP_HOST = "smtp.qq.com"
SMTP_PORT = 465


def _load_local_config() -> dict[str, Any]:
    try:
        from tools import email_local_config as local_config
    except (ImportError, ModuleNotFoundError):
        return {}
    except Exception as exc:
        raise RuntimeError("本地邮箱配置文件 tools/email_local_config.py 读取失败") from exc

    names = [
        "SENDER_EMAIL",
        "AUTH_CODE",
        "DEFAULT_RECEIVER",
        "QQ_EMAIL_ACCOUNT",
        "QQ_EMAIL_AUTH_CODE",
        "QQ_EMAIL_RECEIVER",
    ]
    return {name: getattr(local_config, name, "") for name in names}


LOCAL_CONFIG = _load_local_config()


def _to_list(value):
    """把单个值或列表统一转为列表。"""
    if value is None:
        return []

    if isinstance(value, (str, Path)):
        return [value]

    return list(value)


def _to_email_list(value) -> list[str]:
    """把逗号、分号或列表形式的邮箱配置转为收件人列表。"""
    emails: list[str] = []
    for item in _to_list(value):
        for part in str(item).replace(";", ",").split(","):
            email = part.strip()
            if email:
                emails.append(email)
    return emails


def _first_nonempty(*values: Any) -> str:
    for value in values:
        text = str(value or "").strip()
        if text:
            return text
    return ""


def _local_value(*names: str) -> str:
    for name in names:
        value = LOCAL_CONFIG.get(name, "")
        text = str(value or "").strip()
        if text:
            return text
    return ""


def _resolve_sender_email(sender_email: str | None = None) -> str:
    value = _first_nonempty(
        sender_email,
        os.environ.get(ENV_EMAIL_ACCOUNT),
        _local_value("QQ_EMAIL_ACCOUNT", "SENDER_EMAIL"),
        SENDER_EMAIL,
    )
    if not value:
        raise ValueError(
            f"缺少发件邮箱，请设置 {ENV_EMAIL_ACCOUNT}，或在 tools/email_local_config.py 中配置 SENDER_EMAIL"
        )
    return value


def _resolve_auth_code(auth_code: str | None = None) -> str:
    value = _first_nonempty(
        auth_code,
        os.environ.get(ENV_EMAIL_AUTH_CODE),
        _local_value("QQ_EMAIL_AUTH_CODE", "AUTH_CODE"),
        AUTH_CODE,
    )
    if not value:
        raise ValueError(
            f"缺少 QQ 邮箱 SMTP 授权码，请设置 {ENV_EMAIL_AUTH_CODE}，或在本地配置 AUTH_CODE"
        )
    return value


def _resolve_receivers(
    to_email: str | Sequence[str] | None = None,
    *,
    sender_email: str | None = None,
) -> list[str]:
    receiver_value = to_email
    if receiver_value is None:
        receiver_value = _first_nonempty(
            os.environ.get(ENV_EMAIL_RECEIVER),
            _local_value("QQ_EMAIL_RECEIVER", "DEFAULT_RECEIVER"),
            DEFAULT_RECEIVER,
        )

    receivers = _to_email_list(receiver_value)
    if not receivers and sender_email:
        receivers = [sender_email]

    if not receivers:
        raise ValueError(f"缺少收件邮箱，请传入 to_email 或设置 {ENV_EMAIL_RECEIVER}")

    return receivers


def _escape_html(text: str) -> str:
    """HTML 转义，避免正文中的 < > & 破坏 HTML。"""
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def _guess_mime(file_path: Path) -> tuple[str, str]:
    """根据文件后缀推断 MIME 类型。"""
    mime_type, _ = mimetypes.guess_type(str(file_path))

    if mime_type is None:
        return "application", "octet-stream"

    maintype, subtype = mime_type.split("/", 1)
    return maintype, subtype


def _check_files(image_paths: list[Path]) -> None:
    """检查图片文件是否存在。"""
    for path in image_paths:
        if not path.exists():
            raise FileNotFoundError(f"图片文件不存在: {path}")

        if not path.is_file():
            raise FileNotFoundError(f"不是有效图片文件: {path}")


def build_message(
    subject: str,
    text: str,
    image_paths: str | Path | Sequence[str | Path] | None = None,
    to_email: str | Sequence[str] | None = None,
    sender_email: str | None = None,
    embed_images: bool = True,
    attach_images: bool = True,
) -> EmailMessage:
    """构建邮件对象，不发送。"""
    resolved_sender = _resolve_sender_email(sender_email)
    receivers = _resolve_receivers(to_email, sender_email=resolved_sender)
    images = [Path(x) for x in _to_list(image_paths)]
    _check_files(images)

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = resolved_sender
    msg["To"] = ", ".join(receivers)
    msg["Date"] = formatdate(localtime=True)

    msg.set_content(text or "")

    if embed_images and images:
        html_parts = [
            "<html><body>",
            "<pre style='font-family: Microsoft YaHei, SimHei, Arial, sans-serif; "
            "font-size: 14px; white-space: pre-wrap;'>",
            _escape_html(text or ""),
            "</pre>",
        ]

        related_images = []

        for image_path in images:
            maintype, subtype = _guess_mime(image_path)
            if maintype != "image":
                continue

            cid = make_msgid(domain="qq_email_sender")
            related_images.append((image_path, cid, maintype, subtype))

            html_parts.append(
                f"<p><img src='cid:{cid[1:-1]}' "
                f"style='max-width: 100%; height: auto;'></p>"
            )

        html_parts.append("</body></html>")
        msg.add_alternative("\n".join(html_parts), subtype="html")

        html_part = msg.get_payload()[-1]

        for image_path, cid, maintype, subtype in related_images:
            with image_path.open("rb") as f:
                html_part.add_related(
                    f.read(),
                    maintype=maintype,
                    subtype=subtype,
                    cid=cid,
                    filename=image_path.name,
                )

    if attach_images and images:
        for image_path in images:
            maintype, subtype = _guess_mime(image_path)

            with image_path.open("rb") as f:
                msg.add_attachment(
                    f.read(),
                    maintype=maintype,
                    subtype=subtype,
                    filename=image_path.name,
                )

    return msg


def send_email(
    subject: str,
    text: str,
    image_paths: str | Path | Sequence[str | Path] | None = None,
    to_email: str | Sequence[str] | None = None,
    sender_email: str | None = None,
    auth_code: str | None = None,
    embed_images: bool = True,
    attach_images: bool = True,
    timeout: int = 120,
) -> bool:
    """发送邮件。成功返回 True；失败时抛出异常。"""
    resolved_sender = _resolve_sender_email(sender_email)
    resolved_auth_code = _resolve_auth_code(auth_code)
    receivers = _resolve_receivers(to_email, sender_email=resolved_sender)

    msg = build_message(
        subject=subject,
        text=text,
        image_paths=image_paths,
        to_email=receivers,
        sender_email=resolved_sender,
        embed_images=embed_images,
        attach_images=attach_images,
    )

    context = ssl.create_default_context()

    with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, timeout=timeout, context=context) as smtp:
        smtp.login(resolved_sender, resolved_auth_code)
        smtp.send_message(msg, from_addr=resolved_sender, to_addrs=receivers)

    return True


def send_text(subject: str, text: str, to_email: str | Sequence[str] | None = None) -> bool:
    """只发送文字。"""
    return send_email(
        subject=subject,
        text=text,
        image_paths=None,
        to_email=to_email,
        embed_images=False,
        attach_images=False,
    )


def send_images(
    subject: str,
    text: str,
    image_paths: str | Path | Sequence[str | Path],
    to_email: str | Sequence[str] | None = None,
) -> bool:
    """发送文字 + 图片。"""
    return send_email(
        subject=subject,
        text=text,
        image_paths=image_paths,
        to_email=to_email,
        embed_images=True,
        attach_images=True,
    )


if __name__ == "__main__":
    pass
