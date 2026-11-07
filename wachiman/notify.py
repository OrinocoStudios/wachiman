"""Telegram alerts in plain language. Optional: without a token nothing is sent."""
from __future__ import annotations

import json
import urllib.parse
import urllib.request
from typing import Callable, Dict, Mapping, Optional

from .radar.store import Event
from .radar.sensors import label as sensor_label
from .radar.traffic import human

COUNTRIES: Dict[str, Dict[str, str]] = {
    "es": {"ES": "España", "US": "Estados Unidos", "RU": "Rusia", "CN": "China", "IE": "Irlanda",
           "DE": "Alemania", "FR": "Francia", "GB": "Reino Unido", "NL": "Países Bajos", "VE": "Venezuela",
           "MX": "México", "BR": "Brasil", "IN": "India", "JP": "Japón", "KP": "Corea del Norte", "IR": "Irán",
           "UA": "Ucrania", "SG": "Singapur", "HK": "Hong Kong", "CA": "Canadá", "SE": "Suecia", "FI": "Finlandia",
           "CH": "Suiza", "IT": "Italia", "PT": "Portugal", "EE": "Estonia", "BY": "Bielorrusia", "TR": "Turquía"},
    "en": {"ES": "Spain", "US": "United States", "RU": "Russia", "CN": "China", "IE": "Ireland",
           "DE": "Germany", "FR": "France", "GB": "United Kingdom", "NL": "Netherlands", "VE": "Venezuela",
           "MX": "Mexico", "BR": "Brazil", "IN": "India", "JP": "Japan", "KP": "North Korea", "IR": "Iran",
           "UA": "Ukraine", "SG": "Singapore", "HK": "Hong Kong", "CA": "Canada", "SE": "Sweden", "FI": "Finland",
           "CH": "Switzerland", "IT": "Italy", "PT": "Portugal", "EE": "Estonia", "BY": "Belarus", "TR": "Turkey"},
}

TEXT = {
    "es": {
        "unknown": "país desconocido",
        "new_app": "🆕 {app} se ha conectado a internet por primera vez.\nDestino: {org} ({country}) · {ip}\n"
                   "¿Conoces esta app? Si no, revísala.",
        "new_country": "🌍 {app} se ha conectado a un país nuevo: {country}.\nDestino: {org} · {ip}\n"
                       "Si no esperabas esto, revísalo.",
        "org_unknown": "propietario desconocido",
        "new_device": "🔌 Nuevo {device}: {app}.\nSi no lo has conectado tú, revísalo.",
        "new_keyboard_tap": "⌨️ {app} ha empezado a leer el teclado (lo que tecleas).\nFirmado por: {signer}\n"
                            "Es normal en lanzadores, gestores de atajos o accesibilidad. Si no la conoces, revísala.",
        "new_sensor_app": "🎙️ {app} ha usado {sensor} por primera vez.\nSi no lo esperabas, revísalo.",
        "big_upload": "📤 {app} ha subido {size} a internet en la última hora.\n"
                      "Si no estabas enviando archivos ni en una videollamada, revísalo.",
    },
    "en": {
        "unknown": "unknown country",
        "new_app": "🆕 {app} connected to the internet for the first time.\nDestination: {org} ({country}) · {ip}\n"
                   "Do you know this app? If not, check it.",
        "new_country": "🌍 {app} connected to a new country: {country}.\nDestination: {org} · {ip}\n"
                       "If you did not expect this, check it.",
        "org_unknown": "unknown owner",
        "new_device": "🔌 New {device}: {app}.\nIf you did not connect it, check it.",
        "new_keyboard_tap": "⌨️ {app} started reading the keyboard (what you type).\nSigned by: {signer}\n"
                            "Normal for launchers, shortcut and accessibility tools. If you do not know it, check it.",
        "new_sensor_app": "🎙️ {app} used the {sensor} for the first time.\nIf you did not expect this, check it.",
        "big_upload": "📤 {app} uploaded {size} to the internet in the last hour.\n"
                      "If you were not sending files or on a video call, check it.",
    },
}


DEVICES = {"es": {"usb": "dispositivo USB", "bt": "dispositivo Bluetooth emparejado", "net": "red"},
           "en": {"usb": "USB device", "bt": "paired Bluetooth device", "net": "network"}}

SIGNERS = {"es": {"unsigned": "nadie (sin firmar) ⚠️", "unknown": "desconocido"},
           "en": {"unsigned": "nobody (unsigned) ⚠️", "unknown": "unknown"}}


def signer_label(signer: Optional[str], lang: str = "en") -> str:
    return SIGNERS[_lang(lang)].get(signer or "unknown", signer or "unknown")


def _lang(lang: Optional[str]) -> str:
    return lang if lang in TEXT else "en"


def country_name(code: Optional[str], lang: str = "en") -> str:
    lang = _lang(lang)
    if not code:
        return TEXT[lang]["unknown"]
    return COUNTRIES[lang].get(code, code)


def message(e: Event, lang: str = "en") -> str:
    lang = _lang(lang)
    t = TEXT[lang]
    return t[e.kind].format(app=e.app, country=country_name(e.country, lang), org=e.org or t["org_unknown"],
                            ip=e.remote_ip or "?", size=human(e.bytes_out),
                            sensor=sensor_label(e.sensor or "", lang), signer=signer_label(e.signer, lang),
                            device=DEVICES[lang].get(e.sensor or "", e.sensor or ""))


Post = Callable[[str, dict], object]


def _post(url: str, data: dict) -> object:
    body = urllib.parse.urlencode(data).encode()
    with urllib.request.urlopen(urllib.request.Request(url, data=body), timeout=15) as r:
        return json.loads(r.read() or b"{}")


def send(settings: Mapping[str, str], text: str, post: Post = _post) -> bool:
    token = settings.get("WACHIMAN_TELEGRAM_BOT_TOKEN", "")
    chat = settings.get("WACHIMAN_TELEGRAM_CHAT_ID", "")
    if not token or not chat:
        return False
    try:
        post("https://api.telegram.org/bot%s/sendMessage" % token, {"chat_id": chat, "text": text})
        return True
    except Exception:  # alerts must never take the radar down
        return False
