"""WhatsApp delivery backends.

* CallMeBot (free, personal use) - https://www.callmebot.com/blog/free-api-whatsapp-messages/
  GET https://api.callmebot.com/whatsapp.php?phone=+NUMBER&text=URLENCODED&apikey=KEY
* Twilio WhatsApp - https://www.twilio.com/docs/whatsapp/quickstart
  POST https://api.twilio.com/2010-04-01/Accounts/{SID}/Messages.json  (From/To = whatsapp:+NUMBER)
* Meta WhatsApp Cloud API - https://developers.facebook.com/docs/whatsapp/cloud-api/messages/text-messages
  POST https://graph.facebook.com/{version}/{phone_number_id}/messages
* console - prints the message (testing).
"""
from __future__ import annotations

import logging
import time

import requests

from .config import AppConfig

log = logging.getLogger(__name__)


class NotifyError(RuntimeError):
    pass


def _normalize(number: str) -> str:
    n = number.replace(" ", "").replace("-", "")
    if n.startswith("whatsapp:"):
        n = n[len("whatsapp:"):]
    if not n.startswith("+"):
        n = "+" + n
    return n


class ConsoleNotifier:
    def send(self, text: str) -> None:
        print("\n" + "=" * 40 + "\n" + text + "\n" + "=" * 40, flush=True)


class CallMeBotNotifier:
    URL = "https://api.callmebot.com/whatsapp.php"
    MAX_LEN = 1000  # stay conservative; long texts are split

    def __init__(self, phone: str, api_key: str, session: requests.Session | None = None):
        if not phone or not api_key:
            raise NotifyError("WHATSAPP_TO and CALLMEBOT_API_KEY are required for CallMeBot")
        self.phone, self.api_key = _normalize(phone), api_key
        self.session = session or requests.Session()

    def send(self, text: str) -> None:
        for chunk in _chunks(text, self.MAX_LEN):
            r = self.session.get(
                self.URL, params={"phone": self.phone, "text": chunk, "apikey": self.api_key}, timeout=30
            )
            if r.status_code != 200 or "error" in r.text.lower()[:300]:
                raise NotifyError(f"CallMeBot failed ({r.status_code}): {r.text[:200]}")


class TwilioNotifier:
    URL = "https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages.json"
    MAX_LEN = 1600  # Twilio message body limit

    def __init__(self, sid: str, token: str, from_number: str, to_number: str,
                 session: requests.Session | None = None):
        if not (sid and token and from_number and to_number):
            raise NotifyError("TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, TWILIO_WHATSAPP_FROM and WHATSAPP_TO are required")
        self.sid, self.token = sid, token
        self.from_ = "whatsapp:" + _normalize(from_number)
        self.to = "whatsapp:" + _normalize(to_number)
        self.session = session or requests.Session()

    def send(self, text: str) -> None:
        for chunk in _chunks(text, self.MAX_LEN):
            r = self.session.post(
                self.URL.format(sid=self.sid),
                data={"From": self.from_, "To": self.to, "Body": chunk},
                auth=(self.sid, self.token),
                timeout=30,
            )
            if r.status_code >= 300:
                raise NotifyError(f"Twilio failed ({r.status_code}): {r.text[:300]}")


class MetaCloudNotifier:
    URL = "https://graph.facebook.com/{ver}/{pid}/messages"
    MAX_LEN = 4096  # WhatsApp text body limit

    def __init__(self, token: str, phone_number_id: str, to_number: str, version: str = "v21.0",
                 session: requests.Session | None = None):
        if not (token and phone_number_id and to_number):
            raise NotifyError("META_ACCESS_TOKEN, META_PHONE_NUMBER_ID and WHATSAPP_TO are required")
        self.token, self.pid, self.ver = token, phone_number_id, version
        self.to = _normalize(to_number).lstrip("+")
        self.session = session or requests.Session()

    def send(self, text: str) -> None:
        for chunk in _chunks(text, self.MAX_LEN):
            r = self.session.post(
                self.URL.format(ver=self.ver, pid=self.pid),
                headers={"Authorization": f"Bearer {self.token}"},
                json={"messaging_product": "whatsapp", "to": self.to, "type": "text",
                      "text": {"preview_url": False, "body": chunk}},
                timeout=30,
            )
            if r.status_code >= 300:
                raise NotifyError(f"Meta Cloud API failed ({r.status_code}): {r.text[:300]}")


def _chunks(text: str, size: int):
    if len(text) <= size:
        yield text
        return
    buf = ""
    for line in text.splitlines(keepends=True):
        if len(buf) + len(line) > size and buf:
            yield buf
            buf = ""
        while len(line) > size:
            yield line[:size]
            line = line[size:]
        buf += line
    if buf:
        yield buf


def send_with_retry(notifier, text: str, attempts: int = 3, backoff_s: float = 5.0) -> bool:
    for i in range(1, attempts + 1):
        try:
            notifier.send(text)
            return True
        except (NotifyError, requests.RequestException) as e:
            log.warning("WhatsApp send attempt %d/%d failed: %s", i, attempts, e)
            if i < attempts:
                time.sleep(backoff_s * i)
    return False


def make_notifier(cfg: AppConfig):
    p = cfg.whatsapp_provider
    if p == "console":
        return ConsoleNotifier()
    if p == "callmebot":
        return CallMeBotNotifier(cfg.whatsapp_to, cfg.callmebot_api_key)
    if p == "twilio":
        return TwilioNotifier(cfg.twilio_account_sid, cfg.twilio_auth_token,
                              cfg.twilio_whatsapp_from, cfg.whatsapp_to)
    if p == "meta":
        return MetaCloudNotifier(cfg.meta_access_token, cfg.meta_phone_number_id,
                                 cfg.whatsapp_to, cfg.meta_api_version)
    raise NotifyError(f"Unknown WHATSAPP_PROVIDER: {p}")
