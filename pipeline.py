"""
Pipeline de automatizacion para tecno.ar
==========================================
Flujo: RSS (fuentes de autoridad) -> deduplicacion -> agrupado por tema
       -> redaccion con Gemini API (free tier) -> borrador para revision humana

Requisitos:
    pip install feedparser requests --break-system-packages

Variables de entorno necesarias:
    GEMINI_API_KEY   -> obtenida gratis en https://aistudio.google.com/apikey

Uso:
    python pipeline.py

Salida:
    - drafts/YYYY-MM-DD_slug.md  (un borrador por tema agrupado, listo para revisar)
    - seen.json                  (registro de items ya procesados, evita duplicados)

IMPORTANTE: este script NO publica nada automaticamente. Genera borradores
en Markdown para que un editor humano los revise antes de subirlos a WordPress.
Esto es intencional: es el paso de control de calidad mas importante del pipeline.
"""

import feedparser
import requests
import json
import os
import re
import time
import hashlib
from pathlib import Path
from datetime import datetime

# ----------------------------------------------------------------------
# CONFIGURACION
# ----------------------------------------------------------------------

BASE_DIR = Path(__file__).parent
FEEDS_FILE = BASE_DIR / "feeds.txt"
SEEN_FILE = BASE_DIR / "seen.json"
DRAFTS_DIR = BASE_DIR / "drafts"

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
GEMINI_MODEL = "gemini-2.5-flash"  # modelo del tier gratuito, buena relacion calidad/costo(cero)
GEMINI_URL = (
    f"https://generativelanguage.googleapis.com/v1beta/models/"
    f"{GEMINI_MODEL}:generateContent?key={GEMINI_API_KEY}"
)

MAX_ITEMS_PER_RUN = 8  # limite prudente para no acercarse al tope diario del free tier
MIN_SOURCES_PER_TOPIC = 1  # subir a 2 si queres exigir cruce de fuentes obligatorio

# Palabras clave para filtrar relevancia (ajustar a la linea editorial de tecno.ar)
KEYWORDS = [
    "inteligencia artificial", "ai", "ciberseguridad", "seguridad informatica",
    "software", "hardware", "app", "smartphone", "google", "microsoft",
    "apple", "startup", "tecnologia", "internet", "nube", "cloud",
]


# ----------------------------------------------------------------------
# UTILIDADES
# ----------------------------------------------------------------------

def load_feeds():
    urls = []
    for line in FEEDS_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            urls.append(line)
    return urls


def load_seen():
    if SEEN_FILE.exists():
        return json.loads(SEEN_FILE.read_text(encoding="utf-8"))
    return {}


def save_seen(seen):
    SEEN_FILE.write_text(json.dumps(seen, ensure_ascii=False, indent=2), encoding="utf-8")


def item_hash(entry):
    key = entry.get("link") or entry.get("title", "")
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def is_relevant(entry):
    text = (entry.get("title", "") + " " + entry.get("summary", "")).lower()
    return any(kw in text for kw in KEYWORDS)


def slugify(text):
    text = re.sub(r"[^\w\s-]", "", text.lower())
    return re.sub(r"[\s_-]+", "-", text).strip("-")[:60]


# ----------------------------------------------------------------------
# PASO 1: INGESTA + DEDUPLICACION + FILTRO
# ----------------------------------------------------------------------

def fetch_new_relevant_items():
    seen = load_seen()
    new_items = []

    for url in load_feeds():
        try:
            feed = feedparser.parse(url)
        except Exception as e:
            print(f"[WARN] No se pudo leer {url}: {e}")
            continue

        for entry in feed.entries:
            h = item_hash(entry)
            if h in seen:
                continue
            if not is_relevant(entry):
                continue

            new_items.append({
                "hash": h,
                "title": entry.get("title", "Sin titulo"),
                "link": entry.get("link", ""),
                "summary": re.sub("<[^<]+?>", "", entry.get("summary", ""))[:600],
                "source": feed.feed.get("title", url),
                "published": entry.get("published", ""),
            })
            seen[h] = {"title": entry.get("title", ""), "date": datetime.now().isoformat()}

    save_seen(seen)
    return new_items[:MAX_ITEMS_PER_RUN]


# ----------------------------------------------------------------------
# PASO 2: REDACCION CON GEMINI
# ----------------------------------------------------------------------

def build_prompt(item):
    """
    El prompt le pide explicitamente a Gemini que NO copie frases textuales,
    que aporte contexto propio y que liste las fuentes. Esto es clave tanto
    para SEO (E-E-A-T) como para evitar problemas de derechos de autor.
    """
    return f"""Actua como un periodista senior especializado en tecnologia,
escribiendo para el sitio argentino tecno.ar.

Con base en la siguiente informacion de una fuente de autoridad, redacta una
nota periodistica original en espanol, de entre 600 y 800 palabras.

Titulo original: {item['title']}
Fuente: {item['source']}
Resumen disponible: {item['summary']}
URL original: {item['link']}

Reglas estrictas:
- NO copies frases textuales de la fuente; parafrasea completamente con tus propias palabras.
- Agrega contexto o una perspectiva adicional que no este en el resumen (por que importa, que implica para el usuario, comparacion con antecedentes).
- Tono profesional pero cercano, sin sonar generico ni "hecho por IA" (evita frases hechas tipo "en la era digital actual", "es importante destacar", etc.).
- Al final, agrega una linea "Fuente: {item['source']}" con el link.
- Devolveme SOLO el texto de la nota en Markdown, con un titulo H1 propio (no copies el titulo original tal cual, mejoralo), sin comentarios adicionales.
"""


def call_gemini(prompt, retries=3):
    if not GEMINI_API_KEY:
        raise RuntimeError("Falta la variable de entorno GEMINI_API_KEY")

    payload = {
        "contents": [{"parts": [{"text": prompt}]}]
    }

    for attempt in range(retries):
        resp = requests.post(GEMINI_URL, json=payload, timeout=60)
        if resp.status_code == 200:
            data = resp.json()
            try:
                return data["candidates"][0]["content"]["parts"][0]["text"]
            except (KeyError, IndexError):
                raise RuntimeError(f"Respuesta inesperada de Gemini: {data}")
        elif resp.status_code == 429:
            wait = 2 ** attempt * 5
            print(f"[RATE LIMIT] Esperando {wait}s antes de reintentar...")
            time.sleep(wait)
        else:
            raise RuntimeError(f"Error Gemini {resp.status_code}: {resp.text}")

    raise RuntimeError("Se agotaron los reintentos por rate limit (429).")


# ----------------------------------------------------------------------
# PASO 3: GUARDAR BORRADOR
# ----------------------------------------------------------------------

def save_draft(item, article_md):
    DRAFTS_DIR.mkdir(exist_ok=True)
    date_str = datetime.now().strftime("%Y-%m-%d")
    filename = f"{date_str}_{slugify(item['title'])}.md"
    path = DRAFTS_DIR / filename

    header = (
        f"<!--\n"
        f"ESTADO: borrador sin revisar - NO publicar directo\n"
        f"Fuente original: {item['link']}\n"
        f"Fecha generacion: {datetime.now().isoformat()}\n"
        f"-->\n\n"
    )
    path.write_text(header + article_md, encoding="utf-8")
    print(f"[OK] Borrador guardado: {path}")


# ----------------------------------------------------------------------
# MAIN
# ----------------------------------------------------------------------

def main():
    print("Buscando items nuevos y relevantes en los feeds...")
    items = fetch_new_relevant_items()
    print(f"Encontrados {len(items)} items nuevos para procesar.")

    for item in items:
        print(f"\nRedactando: {item['title'][:70]}...")
        try:
            prompt = build_prompt(item)
            article = call_gemini(prompt)
            save_draft(item, article)
        except Exception as e:
            print(f"[ERROR] No se pudo procesar '{item['title']}': {e}")
        time.sleep(4)  # respeta el limite de RPM del free tier

    print("\nListo. Revisa la carpeta drafts/ antes de publicar cualquier nota.")


if __name__ == "__main__":
    main()
