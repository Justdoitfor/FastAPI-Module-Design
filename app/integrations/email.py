import asyncio
import logging

from email.message import EmailMessage
from typing import Protocol

from app.core.config import settings

logger = logging.getLogger(__name__)


class EmailDeliveryError(Exception):
    """邮件发送失败"""


class EmailSender(Protocol):
    async def send(self, *, to: str, subject: str, body: str) -> None:
        ...


class ConsoleEmailSender:
    async def send(self, *, to: str, subject: str, body: str) -> None:
        logger.info(f"[email] to={to} subject={subject}\n{body}")


class SmtpEmailSender:
    async def send(self, *, to: str, subject: str, body: str) -> None:
        import aiosmtplib
        msg = EmailMessage()
        msg["From"], msg["To"], msg["Subject"] = settings.EMAIL_FROM, to, subject
        msg.set_content(body)
        try:
            await aiosmtplib.send(
                msg,
                hostname=settings.SMTP_HOST,
                port=settings.SMTP_PORT,
                username=settings.SMTP_USER,
                password=settings.SMTP_PASSWORD,
                start_tls=True,
                timeout=10,
            )
        except (aiosmtplib.SMTPException, OSError, asyncio.TimeoutError) as e:
            raise EmailDeliveryError(str(e)) from e


def build_email_sender() -> EmailSender:
    return SmtpEmailSender() if settings.SMTP_HOST else ConsoleEmailSender()
