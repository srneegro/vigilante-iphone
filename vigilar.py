#!/usr/bin/env python3
"""
Vigilante de precios Back Market -> Telegram.

Lee config.json, consulta cada URL de producto y avisa por Telegram cuando
el precio (Excelente, con o sin batería nueva) baja del umbral.

Uso:
  python vigilar.py            # ejecución normal (la que lanza GitHub Actions)
  python vigilar.py --prueba   # muestra precios y manda un Telegram de prueba, sin tocar el estado
"""
import json
import os
import random
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from curl_cffi import requests

BASE = Path(__file__).resolve().parent
CONFIG = json.loads((BASE / "config.json").read_text(encoding="utf-8"))
ESTADO_FICHERO = BASE / "estado.json"
TZ = ZoneInfo("Europe/Madrid")

TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

MARCAS_BLOQUEO = ("just a moment", "cf-chl", "challenge-platform", "captcha", "access denied")


# ---------------------------------------------------------------- utilidades
def telegram(texto: str) -> None:
    if not TOKEN or not CHAT_ID:
        print("[sin Telegram configurado]\n" + texto)
        return
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{TOKEN}/sendMessage",
            data={
                "chat_id": CHAT_ID,
                "text": texto,
                "parse_mode": "HTML",
                "disable_web_page_preview": "true",
            },
            timeout=20,
        )
        if r.status_code != 200:
            print("Telegram respondió", r.status_code, r.text[:200])
    except Exception as e:  # noqa: BLE001
        print("Error enviando Telegram:", e)


def a_float(valor) -> float:
    s = str(valor).strip().replace(" ", "").replace("\xa0", "").replace(" ", "").replace("€", "")
    if "," in s:  # formato europeo 1.234,56
        s = s.replace(".", "").replace(",", ".")
    return float(s)


def recorrer(nodo):
    """Recorre cualquier estructura JSON devolviendo todos los dicts."""
    if isinstance(nodo, dict):
        yield nodo
        for v in nodo.values():
            yield from recorrer(v)
    elif isinstance(nodo, list):
        for v in nodo:
            yield from recorrer(v)


# ------------------------------------------------------- extracción de precio
def extraer_precio(html: str):
    """Devuelve (precio, disponible, metodo). disponible puede ser None si no se sabe."""
    # 1) JSON-LD schema.org (lo más fiable si existe)
    for bloque in re.findall(
        r"<script[^>]*application/ld\+json[^>]*>(.*?)</script>", html, re.S | re.I
    ):
        try:
            data = json.loads(bloque.strip())
        except json.JSONDecodeError:
            continue
        for nodo in recorrer(data):
            tipo = nodo.get("@type")
            tipos = tipo if isinstance(tipo, list) else [tipo]
            if any(t in ("Offer", "AggregateOffer") for t in tipos):
                precio = nodo.get("price") or nodo.get("lowPrice")
                if precio not in (None, ""):
                    disp = str(nodo.get("availability", ""))
                    disponible = None if not disp else ("InStock" in disp or "LimitedAvailability" in disp)
                    return a_float(precio), disponible, "json-ld"

    # 2) Meta etiquetas de precio
    for patron in (
        r'(?:itemprop="price"|property="product:price:amount")[^>]*content="([\d.,]+)"',
        r'content="([\d.,]+)"[^>]*(?:itemprop="price"|property="product:price:amount")',
    ):
        m = re.search(patron, html)
        if m:
            return a_float(m.group(1)), None, "meta"

    # 3) Último recurso: texto visible (puede confundirse con productos relacionados)
    m = re.search(r"Precio reacondicionado:\s*(?:<[^>]+>\s*)*([\d.]+,\d{2})\s*(?:<[^>]+>\s*)*€", html)
    if m:
        return a_float(m.group(1)), None, "texto"

    return None, None, None


def texto_plano(html: str) -> str:
    html = re.sub(r"<!--.*?-->", "", html, flags=re.S)
    html = re.sub(r"<svg.*?</svg>", "", html, flags=re.S)
    html = re.sub(r"<[^>]+>", " ", html)
    return re.sub(r"\s+", " ", html)


def seccion(html: str, etiqueta: str) -> str:
    """Texto visible de un selector de la ficha (p. ej. 'Selecciona el estado'),
    hasta donde empieza el siguiente selector."""
    i = html.find(f'aria-label="{etiqueta}"')
    if i < 0:
        return ""
    fin = html.find('aria-label="Selecciona', i + 20)
    if fin < 0 or fin - i > 80000:
        fin = i + 80000
    return texto_plano(html[i:fin])


PRECIO = r"([\d.]+,\d{2})\s*€"


def extraer_bateria_nueva(html: str):
    m = re.search(r"Batería nueva\s+" + PRECIO, seccion(html, "Selecciona una opción de batería"))
    return a_float(m.group(1)) if m else None


def precio_excelente_selector(html: str):
    m = re.search(r"Excelente\s+" + PRECIO, seccion(html, "Selecciona el estado"))
    return a_float(m.group(1)) if m else None


def consultar(url: str):
    """Devuelve dict con precio/disponible/metodo o con 'error'."""
    try:
        r = requests.get(
            url,
            impersonate="chrome",
            timeout=30,
            headers={"Accept-Language": "es-ES,es;q=0.9"},
        )
    except Exception as e:  # noqa: BLE001
        return {"error": f"conexión: {e}"}

    html = r.text or ""
    if r.status_code in (403, 429, 503) or any(m in html[:20000].lower() for m in MARCAS_BLOQUEO):
        return {"error": f"bloqueado (HTTP {r.status_code})", "bloqueo": True}
    if r.status_code != 200:
        return {"error": f"HTTP {r.status_code}"}

    precio, disponible, metodo = extraer_precio(html)
    if precio is None:
        return {"error": "no encuentro el precio en la página (¿ha cambiado la web?)"}

    # ¿La página está de verdad en "Excelente"? Si ese estado se agota,
    # Back Market enseña otro y el precio no sería el que buscas.
    exc = precio_excelente_selector(html)
    if exc is not None and abs(exc - precio) > 1:
        return {"error": f"no está en Excelente (Excelente marca {exc:.0f} €, la página {precio:.0f} €)"}
    if exc is None and "Excelente" in seccion(html, "Selecciona el estado") and CONFIG.get("solo_excelente", True):
        disponible = False  # Excelente aparece sin precio → agotado

    return {
        "precio": precio,
        "bateria_nueva": extraer_bateria_nueva(html),
        "disponible": disponible,
        "metodo": metodo,
    }


# ---------------------------------------------------------------- principal
def main() -> None:
    prueba = "--prueba" in sys.argv
    umbral = float(CONFIG["umbral"])
    con_bateria = bool(CONFIG.get("bateria_nueva", True))
    productos = CONFIG["productos"]
    etiqueta = "con batería nueva" if con_bateria else "batería estándar"

    estado = {}
    if ESTADO_FICHERO.exists():
        try:
            estado = json.loads(ESTADO_FICHERO.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            estado = {}
    estado_original = json.dumps(estado, sort_keys=True)
    avisos = estado.setdefault("avisos", {})  # url -> último precio avisado

    resultados = []
    for i, p in enumerate(productos):
        if i:
            time.sleep(random.uniform(4, 9))  # no machacar la web
        res = consultar(p["url"])
        res.update(nombre=p["nombre"], url=p["url"])
        if "error" not in res:
            if con_bateria and res.get("bateria_nueva") is None:
                res = {"error": "no veo la opción de batería nueva (¿agotada?)", **{k: res[k] for k in ("nombre", "url")}}
            else:
                res["total"] = res["bateria_nueva"] if con_bateria else res["precio"]
        resultados.append(res)
        print(p["nombre"], "->", res)

    if prueba:
        lineas = ["🧪 <b>Prueba del vigilante</b>"]
        for r in resultados:
            if "error" in r:
                lineas.append(f"• {r['nombre']}: ⚠️ {r['error']}")
            else:
                lineas.append(f"• {r['nombre']}: {r['precio']:.2f} € estándar / {r.get('bateria_nueva') or 0:.2f} € batería nueva")
        telegram("\n".join(lineas))
        return

    # --- aviso de bloqueo / recuperación (una sola vez, no cada media hora)
    todos_bloqueados = all(r.get("bloqueo") for r in resultados)
    if todos_bloqueados and not estado.get("bloqueado"):
        telegram(
            "⚠️ Back Market está bloqueando al vigilante. "
            "Mientras dure, no veo precios: revisa a mano o cámbialo a tu PC/móvil."
        )
        estado["bloqueado"] = True
    elif not todos_bloqueados and estado.get("bloqueado"):
        telegram("✅ Vuelvo a ver precios de Back Market.")
        estado["bloqueado"] = False

    # --- avisos de precio
    for r in resultados:
        if "error" in r:
            continue
        total = r["total"]
        anterior = avisos.get(r["url"])
        if r["disponible"] is False:
            continue
        if total <= umbral:
            # Avisa la primera vez, y otra vez solo si baja más de 5 €
            if anterior is None or total < anterior - 5:
                telegram(
                    f"🔥 <b>iPhone 16 Pro {r['nombre']}</b>\n"
                    f"Excelente {etiqueta}: {total:.2f} € — umbral {umbral:.0f} €\n"
                    f"<a href=\"{r['url']}\">Abrir en Back Market</a>"
                )
                avisos[r["url"]] = total
        elif anterior is not None:
            telegram(f"↗️ {r['nombre']} ha vuelto a subir: {total:.2f} €")
            del avisos[r["url"]]

    # --- resumen diario (primera ejecución a partir de las 9:00, hora de Madrid)
    ahora = datetime.now(TZ)
    hoy = ahora.strftime("%Y-%m-%d")
    if CONFIG.get("resumen_diario", True) and ahora.hour >= 9 and estado.get("ultimo_resumen") != hoy:
        lineas = [f"📋 <b>Resumen iPhone 16 Pro Excelente</b> (umbral {umbral:.0f} € {etiqueta})"]
        for r in resultados:
            if "error" in r:
                lineas.append(f"• {r['nombre']}: ⚠️ {r['error']}")
            else:
                agotado = " (agotado)" if r["disponible"] is False else ""
                bn = f" / {r['bateria_nueva']:.0f} € bat. nueva" if r.get("bateria_nueva") else ""
                lineas.append(f"• {r['nombre']}: {r['precio']:.0f} € estándar{bn}{agotado}")
        telegram("\n".join(lineas))
        estado["ultimo_resumen"] = hoy

    if json.dumps(estado, sort_keys=True) != estado_original:
        ESTADO_FICHERO.write_text(json.dumps(estado, indent=2, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    main()
