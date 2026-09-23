"""Telegram bot: pushes diagnoses to engineers' phones and answers a few commands.

Setup (2 minutes):
  1. Talk to @BotFather -> /newbot -> copy the token into TELEGRAM_BOT_TOKEN
  2. Start FirstCall, send /start to your bot (or add it to a group and send /start there)
  3. The bot replies with the chat id -> put it in TELEGRAM_CHAT_IDS (comma separated) and restart

Commands (allowed chats only): /status /incidents /incident <id> /mode [always|offhours] /help
Buttons under each alert: Investigate (runs the read-only next command), Correct, Wrong.
Only one process may poll a bot token: don't run `make dev` and the Helm install at the same time.
"""
import html
import logging
import threading
import time

import httpx

log = logging.getLogger("firstcall.telegram")


def esc(s) -> str:
    return html.escape(str(s if s is not None else ""), quote=False)


class TelegramBot:
    def __init__(self, token: str, chat_ids: list[str], api_base: str = "https://api.telegram.org"):
        self.token = token
        self.chat_ids = [c.strip() for c in chat_ids if c.strip()]
        self.base = f"{api_base.rstrip('/')}/bot{token}"
        self.http = httpx.Client(timeout=40)
        self.engine = None  # set by the engine, used for commands and buttons
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._offset = 0

    @property
    def configured(self) -> bool:
        return bool(self.token)

    # ---------------------------------------------------------------- sending
    def _call(self, method: str, **payload) -> dict:
        r = self.http.post(f"{self.base}/{method}", json=payload)
        data = r.json()
        if not data.get("ok"):
            raise RuntimeError(f"telegram {method}: {data.get('description', r.status_code)}")
        return data.get("result", {})

    def send_voice(self, mp3: bytes, caption: str = "", chat_id: str | None = None) -> int:
        """Send the alert as a voice note: at 3am you hear it without unlocking the phone."""
        sent = 0
        for cid in ([chat_id] if chat_id else self.chat_ids):
            try:
                r = self.http.post(f"{self.base}/sendVoice", data={"chat_id": cid, "caption": caption[:200]},
                                   files={"voice": ("firstcall.mp3", mp3, "audio/mpeg")})
                if r.json().get("ok"):
                    sent += 1
            except Exception as e:
                log.warning("telegram voice to %s failed: %s", cid, e)
        return sent

    def send(self, text: str, chat_id: str | None = None, buttons: list[list[dict]] | None = None) -> int:
        """Send to one chat, or to every allowed chat. Returns messages sent."""
        targets = [chat_id] if chat_id else self.chat_ids
        sent = 0
        for cid in targets:
            payload = {"chat_id": cid, "text": text[:4000], "parse_mode": "HTML",
                       "disable_web_page_preview": True}
            if buttons:
                payload["reply_markup"] = {"inline_keyboard": buttons}
            try:
                self._call("sendMessage", **payload)
                sent += 1
            except Exception as e:
                log.warning("telegram send to %s failed: %s", cid, e)
        return sent

    # ---------------------------------------------------------------- formatting
    @staticmethod
    def format_diagnosis(inc: dict, resp: dict, url: str = "") -> str:
        d, u = resp["diagnosis"], resp["usage"]
        conf = round(d["confidence"] * 100)
        lines = [
            f"🚨 <b>{esc(inc['namespace'])}/{esc(inc.get('workload') or inc['pod'])}</b> · {esc(inc['reason'])}",
            f"<b>{esc(d['summary'])}</b>",
            f"<i>{esc(d['category'])} · {conf}% confidence · {esc(u['model_key'])}</i>",
            "",
            f"<b>Cause:</b> {esc(d['root_cause'])}",
        ]
        if d.get("evidence"):
            lines.append(f"<b>Evidence:</b> <code>{esc(d['evidence'][0][:300])}</code>")
        lines.append(f"<b>Next:</b> <code>{esc(d['next_command'])}</code>"
                     + ("" if d.get("next_command_safe") else " ⚠️ changes the cluster"))
        if d.get("suspected_change"):
            lines.append(f"<b>What changed:</b> {esc(d['suspected_change'])}")
        if d.get("sources"):
            lines.append("<b>Sources:</b> " + " · ".join(f'<a href="{esc(u)}">[{i + 1}]</a>'
                                                          for i, u in enumerate(d["sources"][:3])))
        if d.get("fix"):
            lines.append(f"<b>Fix:</b> {esc(d['fix'])}")
        if d.get("remediation_command"):
            lines.append(f"<b>Proposed fix:</b> <code>{esc(d['remediation_command'])}</code>"
                         + (" (needs your approval)" if d.get("remediation_allowed") else " (not auto-appliable)"))
        if url:
            lines += ["", f'<a href="{esc(url)}/?incident={inc["id"]}">Open incident #{inc["id"]}</a>']
        return "\n".join(lines)

    @staticmethod
    def buttons_for(inc_id: int, safe: bool, fixable: bool = False) -> list[list[dict]]:
        row = []
        if safe:
            row.append({"text": "🔎 Investigate", "callback_data": f"inv:{inc_id}"})
        if fixable:
            row.append({"text": "🛠 Fix…", "callback_data": f"fix:{inc_id}"})
        rows = [row] if row else []
        rows.append([{"text": "✅ Correct", "callback_data": f"ok:{inc_id}"},
                     {"text": "❌ Wrong", "callback_data": f"bad:{inc_id}"}])
        return rows

    # ---------------------------------------------------------------- commands (long polling)
    def start_polling(self):
        if self._thread or not self.configured:
            return
        self._thread = threading.Thread(target=self._poll, name="telegram", daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()

    def _poll(self):
        backoff = 2
        while not self._stop.is_set():
            try:
                updates = self._call("getUpdates", offset=self._offset, timeout=30,
                                     allowed_updates=["message", "callback_query"])
                backoff = 2
                for up in updates:
                    self._offset = up["update_id"] + 1
                    try:
                        self.handle(up)
                    except Exception as e:
                        log.warning("telegram update failed: %s", e)
            except Exception as e:
                log.warning("telegram polling: %s (retry in %ss)", e, backoff)
                self._stop.wait(backoff)
                backoff = min(backoff * 2, 60)

    def allowed(self, chat_id) -> bool:
        return str(chat_id) in self.chat_ids

    def handle(self, up: dict):
        if "callback_query" in up:
            return self._handle_button(up["callback_query"])
        msg = up.get("message") or {}
        text = (msg.get("text") or "").strip()
        chat = str(msg.get("chat", {}).get("id", ""))
        if not text.startswith("/") or not chat:
            return
        cmd, *args = text.split()
        cmd = cmd.split("@")[0].lower()
        if cmd == "/start" and not self.allowed(chat):
            return self.send(f"👋 FirstCall here. This chat id is <code>{esc(chat)}</code>.\n"
                             "Add it to <code>TELEGRAM_CHAT_IDS</code> and restart FirstCall to receive alerts.", chat)
        if not self.allowed(chat):
            return  # never leak cluster data to unknown chats
        e = self.engine
        if cmd in ("/start", "/help"):
            return self.send("FirstCall commands:\n/status · /incidents · /incident &lt;id&gt;\n"
                             "/mode · /mode always · /mode offhours", chat)
        if cmd == "/status":
            s, a = e.store.stats(), e.alerting_state()
            return self.send(f"<b>{s['open']}</b> open · {s['resolved']} resolved\n"
                             f"Alert mode: <b>{a['mode']}</b> ({esc(a['description'])})\n"
                             f"Paging now: {'yes' if a['paging_now'] else 'no (business hours)'}", chat)
        if cmd == "/incidents":
            rows = e.store.list("open")
            if not rows:
                return self.send("✅ No open incidents.", chat)
            body = "\n".join(f"#{r['id']} <b>{esc(r['workload'] or r['pod'])}</b> · {esc(r['reason'])}"
                             f"\n    {esc(r['summary'] or 'diagnosing…')}" for r in rows[:15])
            return self.send(f"<b>{len(rows)} open</b>\n{body}", chat)
        if cmd == "/incident" and args and args[0].lstrip("#").isdigit():
            iid = int(args[0].lstrip("#"))
            inc, last = e.store.get(iid), e.store.last_diagnosis(iid)
            if not inc:
                return self.send("No such incident.", chat)
            if not last:
                return self.send(f"#{iid} has no diagnosis yet.", chat)
            return self.send(self.format_diagnosis(inc, last, e.s.firstcall_public_url), chat,
                             self.buttons_for(iid, last["diagnosis"].get("next_command_safe", False),
                                              self.fixable(last)))
        if cmd == "/mode":
            if args and args[0] in ("always", "offhours"):
                e.set_alert_mode(args[0], by=f"telegram:{chat}")
            a = e.alerting_state()
            return self.send(f"Alert mode: <b>{a['mode']}</b>\n{esc(a['description'])}", chat)
        return self.send("Unknown command. /help", chat)

    def fixable(self, resp: dict) -> bool:
        return bool(self.engine and self.engine.s.firstcall_remediation == "approve"
                    and resp["diagnosis"].get("remediation_allowed"))

    def _handle_button(self, cq: dict):
        chat = str(cq.get("message", {}).get("chat", {}).get("id", ""))
        data = cq.get("data", "")
        who = cq.get("from", {}).get("username") or cq.get("from", {}).get("first_name") or chat
        answer = "Not allowed"
        if self.allowed(chat) and ":" in data:
            action, rest = data.split(":", 1)
            iid = int(rest.split(":")[0])
            e = self.engine
            if action == "fix":
                p = e.remediation_preview(iid, by=f"telegram:{who}")
                if p.get("ok"):
                    self.send(f"🛠 <b>Proposed fix for #{iid}</b>\n<code>{esc(p['command'])}</code>\n"
                              f"Dry run passed:\n<pre>{esc(p.get('output', '')[:800])}</pre>\nApply it?", chat,
                              [[{"text": "✅ Apply fix", "callback_data": f"apply:{iid}:{p['hash']}"},
                                {"text": "✖ Cancel", "callback_data": f"cancel:{iid}"}]])
                    answer = "Dry run passed"
                else:
                    self.send(f"🛑 Fix for #{iid} not applicable: {esc(p.get('reason'))}\n<pre>{esc(p.get('output', '')[:500])}</pre>", chat)
                    answer = "Dry run failed"
            elif action == "apply":
                h = rest.split(":")[1] if ":" in rest else ""
                r = e.remediation_apply(iid, h, by=f"telegram:{who}")
                self.send((f"✅ Applied by {esc(who)}: <code>{esc(r.get('command', ''))}</code>\n"
                           "FirstCall will confirm when the workload is healthy.") if r.get("ok")
                          else f"🛑 Not applied: {esc(r.get('reason'))}", chat)
                answer = "Applied" if r.get("ok") else "Not applied"
            elif action == "cancel":
                answer = "Cancelled"
            elif action == "inv":
                answer = "Investigating (read-only)…" if e.submit_step(iid) else "Already working on it"
            elif action in ("ok", "bad"):
                e.store.set_feedback(iid, "correct" if action == "ok" else "wrong")
                answer = "Thanks, feedback saved"
        try:
            self._call("answerCallbackQuery", callback_query_id=cq["id"], text=answer)
        except Exception:
            pass
