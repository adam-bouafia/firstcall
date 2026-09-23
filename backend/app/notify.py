"""Push notifications (Telegram, Slack), gated by the alert mode.

Everything is always visible in the console, Kubernetes Events and metrics.
Only pushes to people are subject to the schedule.
"""
import logging

import httpx

from app import metrics as M
from app.activity import emit

log = logging.getLogger("firstcall.notify")


class Notifier:
    def __init__(self, settings, store, schedule, telegram=None):
        self.s = settings
        self.store = store
        self.schedule = schedule
        self.telegram = telegram

    @property
    def channels(self) -> dict:
        return {"telegram": bool(self.telegram and self.telegram.configured and self.telegram.chat_ids),
                "slack": bool(self.s.slack_webhook_url)}

    def _gate(self, inc: dict, what: str) -> bool:
        if not any(self.channels.values()):
            return False
        mode = self.store.get_setting("alert_mode", self.s.firstcall_alert_mode)
        if self.schedule.should_page(mode):
            return True
        M.NOTIFICATIONS.labels("all", "suppressed").inc()
        emit("notify", f"{what} not pushed: business hours (alert mode 'offhours')", inc["id"])
        self.store.add_event(inc["id"], "note", {"text": f"{what}: not pushed (business hours, alert mode 'offhours')"})
        return False

    def diagnosed(self, inc: dict, resp: dict):
        if not self._gate(inc, "Alert"):
            return
        url = self.s.firstcall_public_url
        if self.channels["telegram"]:
            n = self.telegram.send(self.telegram.format_diagnosis(inc, resp, url),
                                   buttons=self.telegram.buttons_for(inc["id"], resp["diagnosis"].get("next_command_safe", False),
                                                                     self.telegram.fixable(resp)))
            M.NOTIFICATIONS.labels("telegram", "sent" if n else "failed").inc()
            emit("notify", f"Telegram alert sent to {n} chat(s)" if n else "Telegram alert FAILED", inc["id"],
                 "info" if n else "error")
            if n and self.s.firstcall_voice_alerts:
                from app.voice import spoken_text, synthesize
                mp3 = synthesize(spoken_text(inc, resp))
                if mp3:
                    v = self.telegram.send_voice(mp3, f"{inc['namespace']}/{inc.get('workload') or inc['pod']}")
                    emit("voice", f"voice note sent to {v} chat(s) ({len(mp3) // 1024} KB)", inc["id"])
        if self.channels["slack"]:
            d = resp["diagnosis"]
            self._slack(f":rotating_light: *{inc['namespace']}/{inc.get('workload') or inc['pod']}* — {inc['reason']}\n"
                        f"*{d['summary']}* ({round(d['confidence'] * 100)}% · {d['category']})\n"
                        f"Next: `{d['next_command']}`\n<{url}/?incident={inc['id']}|Open in FirstCall>")

    def resolved(self, inc: dict):
        if not self._gate(inc, "Resolution"):
            return
        text = f"{inc['namespace']}/{inc.get('workload') or inc['pod']} resolved"
        if self.channels["telegram"]:
            n = self.telegram.send(f"✅ <b>{text}</b>")
            M.NOTIFICATIONS.labels("telegram", "sent" if n else "failed").inc()
        if self.channels["slack"]:
            self._slack(f":white_check_mark: *{text}*")

    def _slack(self, text: str):
        try:
            httpx.post(self.s.slack_webhook_url, json={"text": text}, timeout=10).raise_for_status()
            M.NOTIFICATIONS.labels("slack", "sent").inc()
        except Exception as e:
            M.NOTIFICATIONS.labels("slack", "failed").inc()
            log.warning("slack notify failed: %s", e)
