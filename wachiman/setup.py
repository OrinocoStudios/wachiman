"""First-run wizard: language and optional Telegram alerts, saved to config.env.

    wachiman setup                    ask questions
    wachiman setup --non-interactive  take WACHIMAN_LANG / WACHIMAN_TELEGRAM_BOT_TOKEN /
                                      WACHIMAN_TELEGRAM_CHAT_ID from the environment

The token is read without echo, never printed and only ever written to
config.env (mode 600, folder 700). Input, output and HTTP are injected so the
wizard can be tested without a terminal or a network.
"""
from __future__ import annotations

import getpass
import json
import os
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Callable, Dict, List, Mapping, Optional

from . import paths

LANGS = ("en", "es")
TOKEN_KEY = "WACHIMAN_TELEGRAM_BOT_TOKEN"
CHAT_KEY = "WACHIMAN_TELEGRAM_CHAT_ID"
API = "https://api.telegram.org/bot%s/%s"

T = {
    "en": {
        "hello": "Wachiman setup. Press Enter to accept the answer in [brackets].",
        "lang": "Language for alerts (en/es)",
        "telegram_q": "Get alerts on Telegram? (optional, y/n)",
        "keep_q": "Telegram is already set up. Keep it? (y/n)",
        "howto": "To get alerts on your phone:\n"
                 "  1. In Telegram open @BotFather, send /newbot and follow the steps. It gives you a token.\n"
                 "  2. Open @userinfobot and send any message. It replies with your numeric chat id.\n"
                 "  3. Send any message to your new bot once, so it is allowed to write to you.",
        "token": "Bot token (typing is hidden, Enter to skip)",
        "chat": "Your chat id",
        "bad_token": "Telegram did not accept that token (or there is no internet). Telegram alerts left off.",
        "bot_ok": "Token works: bot @%s.",
        "test_q": "Send one test message now? (y/n)",
        "test_sent": "Test message sent.",
        "test_failed": "The test message could not be sent. Check the chat id and that you wrote to your bot first.",
        "skipped": "Telegram skipped. You can run `wachiman setup` again any time.",
        "test_text": "Wachiman is set up. You will get alerts here.",
        "saved": "Saved to %s (private to you).",
        "bad_lang": "WACHIMAN_LANG must be one of: en, es",
        "nothing": "Nothing to change.",
    },
    "es": {
        "hello": "Configuración de Wachiman. Pulsa Enter para aceptar la respuesta entre [corchetes].",
        "lang": "Idioma de las alertas (en/es)",
        "telegram_q": "¿Recibir avisos en Telegram? (opcional, s/n)",
        "keep_q": "Telegram ya está configurado. ¿Lo mantienes? (s/n)",
        "howto": "Para recibir alertas en el móvil:\n"
                 "  1. En Telegram abre @BotFather, envía /newbot y sigue los pasos. Te da un token.\n"
                 "  2. Abre @userinfobot y envíale cualquier mensaje. Te responde con tu chat id numérico.\n"
                 "  3. Escribe un mensaje a tu nuevo bot una vez, para que pueda escribirte.",
        "token": "Token del bot (no se ve al escribir, Enter para saltar)",
        "chat": "Tu chat id",
        "bad_token": "Telegram no aceptó ese token (o no hay internet). Avisos de Telegram desactivados.",
        "bot_ok": "El token funciona: bot @%s.",
        "test_q": "¿Enviar ahora un mensaje de prueba? (s/n)",
        "test_sent": "Mensaje de prueba enviado.",
        "test_failed": "No se pudo enviar el mensaje de prueba. Revisa el chat id y que hayas escrito antes a tu bot.",
        "skipped": "Telegram omitido. Puedes ejecutar `wachiman setup` otra vez cuando quieras.",
        "test_text": "Wachiman está configurado. Aquí recibirás los avisos.",
        "saved": "Guardado en %s (privado, solo para ti).",
        "bad_lang": "WACHIMAN_LANG debe ser: en, es",
        "nothing": "Nada que cambiar.",
    },
}

Ask = Callable[[str], str]
Say = Callable[..., None]
Get = Callable[[str], dict]
Post = Callable[[str, dict], object]


def _get(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=15) as r:
        return json.loads(r.read() or b"{}")


def _post(url: str, data: dict) -> object:
    body = urllib.parse.urlencode(data).encode()
    with urllib.request.urlopen(urllib.request.Request(url, data=body), timeout=15) as r:
        return json.loads(r.read() or b"{}")


def write_config(path: Path, updates: Mapping[str, str]) -> None:
    """Merge `updates` into a KEY=VALUE file. Other lines, comments included, stay as they are."""
    for k, v in updates.items():
        if "\n" in k or "\r" in k or "\n" in v or "\r" in v:
            raise ValueError("config values must be a single line")
    path = Path(path)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(str(path.parent), 0o700)
    try:
        lines: List[str] = path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        lines = []
    todo = dict(updates)
    out: List[str] = []
    for raw in lines:
        s = raw.strip()
        body = s[len("export "):].lstrip() if s.startswith("export ") else s
        key = body.split("=", 1)[0].strip() if "=" in body and not s.startswith("#") else None
        if key in todo:
            out.append("%s=%s" % (key, todo.pop(key)))
        else:
            out.append(raw)
    out.extend("%s=%s" % kv for kv in todo.items())
    tmp = path.with_name(path.name + ".tmp")
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write("\n".join(out) + "\n")
    os.chmod(str(tmp), 0o600)
    os.replace(str(tmp), str(path))


def check_token(token: str, get: Get = _get) -> Optional[str]:
    """Ask Telegram who the bot is. Returns its username, or None. Never raises, never leaks the token."""
    try:
        data = get(API % (token, "getMe"))
    except Exception:
        return None
    if isinstance(data, dict) and data.get("ok"):
        return str((data.get("result") or {}).get("username") or "bot")
    return None


def _yes(answer: str) -> bool:
    return answer.strip().lower() in ("y", "yes", "s", "si", "sí")


def run(env: Optional[Mapping[str, str]] = None, ask: Ask = input,
        ask_secret: Ask = getpass.getpass, say: Say = print, get: Get = _get, post: Post = _post,
        non_interactive: bool = False) -> int:
    env = os.environ if env is None else env
    cfg_path = paths.config_path(env)

    if non_interactive:
        updates: Dict[str, str] = {k: env[k] for k in ("WACHIMAN_LANG", TOKEN_KEY, CHAT_KEY) if env.get(k)}
        if updates.get("WACHIMAN_LANG", "en") not in LANGS:
            say(T["en"]["bad_lang"])
            return 2
        if not updates:
            say(T["en"]["nothing"])
            return 0
        write_config(cfg_path, updates)
        say(T["en"]["saved"] % cfg_path)
        return 0

    existing = paths.load_config(cfg_path)
    say(T["en"]["hello"])
    default = existing.get("WACHIMAN_LANG") if existing.get("WACHIMAN_LANG") in LANGS else "en"
    answer = ask("%s [%s]: " % (T["en"]["lang"], default)).strip().lower()
    lang = answer or default
    if lang not in LANGS:
        lang = "en"
    t = T[lang]
    updates = {"WACHIMAN_LANG": lang}

    if existing.get(TOKEN_KEY) and existing.get(CHAT_KEY):
        if not _yes(ask(t["keep_q"] + " ")):
            existing = {}
    if not (existing.get(TOKEN_KEY) and existing.get(CHAT_KEY)):
        if _yes(ask(t["telegram_q"] + " ")):
            say(t["howto"])
            token = ask_secret(t["token"] + ": ").strip()
            bot = check_token(token, get) if token else None
            if bot:
                say(t["bot_ok"] % bot)
                chat = ask(t["chat"] + ": ").strip()
                if chat:
                    updates[TOKEN_KEY], updates[CHAT_KEY] = token, chat
                    if _yes(ask(t["test_q"] + " ")):
                        try:
                            post(API % (token, "sendMessage"), {"chat_id": chat, "text": t["test_text"]})
                            say(t["test_sent"])
                        except Exception:
                            say(t["test_failed"])
            else:
                say(t["bad_token"] if token else t["skipped"])
        else:
            say(t["skipped"])

    write_config(cfg_path, updates)
    say(t["saved"] % cfg_path)
    return 0
