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

    # Primero juntamos los candidatos relevantes de CADA fuente por separado,
    # sin mezclarlos todavia. Asi ninguna fuente "se come" el cupo de las demas.
    items_by_source = []
    for url in load_feeds():
        try:
            feed = feedparser.parse(url)
        except Exception as e:
            print(f"[WARN] No se pudo leer {url}: {e}")
            continue

        source_items = []
        for entry in feed.entries:
            h = item_hash(entry)
            if h in seen:
                continue
            if not is_relevant(entry):
                continue

            source_items.append({
                "hash": h,
                "title": entry.get("title", "Sin titulo"),
                "link": entry.get("link", ""),
                "summary": re.sub("<[^<]+?>", "", entry.get("summary", ""))[:600],
                "source": feed.feed.get("title", url),
                "published": entry.get("published", ""),
            })

        if source_items:
            items_by_source.append(source_items)

    # Ahora intercalamos: 1 item de la fuente A, 1 de la B, 1 de la C, etc.
    # Esto garantiza diversidad de fuentes en cada corrida.
    new_items = []
    idx = 0
    while len(new_items) < MAX_ITEMS_PER_RUN and any(items_by_source):
        for source_items in items_by_source:
            if idx < len(source_items):
                item = source_items[idx]
                new_items.append(item)
                seen[item["hash"]] = {"title": item["title"], "date": datetime.now().isoformat()}
                if len(new_items) >= MAX_ITEMS_PER_RUN:
                    break
        idx += 1

    save_seen(seen)
    return new_items


# ----------------------------------------------------------------------
# PASO 2: REDACCION CON GEMINI
# ----------------------------------------------------------------------

def build_prompt(item):
    """
    Prompt disenado para maximizar el SEO Score de Rank Math (apuntando a 85+),
    sin sacrificar calidad editorial real ni caer en keyword stuffing.

    Cubre los checks principales que evalua Rank Math:
    - Basic SEO: keyword en titulo, meta description, URL, primer 10% del
      contenido, contenido en general, longitud minima.
    - Additional: keyword en subtitulos (H2/H3), densidad de keyword,
      alt text de imagenes, enlaces internos y externos.
    - Title Readability: keyword al inicio del titulo, power word, numero,
      longitud del titulo (~50-60 caracteres).
    - Content Readability: parrafos cortos, subtitulos frecuentes,
      voz activa, transiciones.
    """
    return f"""Actua como un redactor SEO senior especializado en tecnologia,
con dominio experto de los criterios de puntuacion de Rank Math para WordPress,
escribiendo para el sitio argentino tecno.ar.

FUENTE DE REFERENCIA (no copiar frases, solo usar como base factual):
Titulo original: {item['title']}
Medio: {item['source']}
Resumen: {item['summary']}
URL original: {item['link']}

===========================================
PASO 1: DEFINI EL FOCUS KEYWORD
===========================================
Elegi UN focus keyword principal (2-4 palabras, en espanol, con intencion de
busqueda real, no generico) que represente el tema central de la noticia.
Todo lo que sigue debe girar alrededor de ese keyword, de forma natural,
sin forzarlo ni repetirlo de forma artificial.

===========================================
PASO 2: GENERA TODOS ESTOS CAMPOS (en este orden exacto)
===========================================

## FOCUS_KEYWORD
[el keyword elegido]

## SEO_TITLE
Titulo de 50-60 caracteres. Reglas obligatorias:
- El focus keyword debe aparecer LO MAS CERCA POSIBLE DEL INICIO del titulo.
- Incluir un numero O una power word (ej: "clave", "revolucionario", "definitivo",
  "urgente", "confirmado", "oficial") cuando sea genuino y no clickbait vacio.
- Debe generar curiosidad real sin exagerar ni mentir sobre el contenido.

## SLUG
version-corta-en-minusculas-con-guiones-del-focus-keyword
(3-5 palabras maximo, sin stopwords como "el", "la", "de", "en" salvo que sean
imprescindibles para el sentido)

## META_DESCRIPTION
Entre 150 y 160 caracteres. Debe incluir el focus keyword de forma natural
y un llamado a la accion implicito (ej: "descubri por que", "te contamos como").

## H1
El titulo visible del articulo (puede ser igual o levemente distinto al SEO_TITLE).
Debe incluir el focus keyword.

## ARTICULO
El cuerpo de la nota en Markdown, siguiendo ESTAS reglas de estructura y redaccion:

1. ESTRUCTURA:
   - El focus keyword debe aparecer en el PRIMER PARRAFO (primeras ~100 palabras).
   - Dividi el cuerpo en al menos 3-4 subtitulos H2 (##), y H3 (###) si aplica.
   - Al menos UN subtitulo H2 debe contener el focus keyword o una variacion natural.
   - Parrafos cortos: maximo 3-4 lineas cada uno. Nada de bloques de texto densos.
   - Extension total: entre 600 y 900 palabras (Rank Math penaliza contenido
     "delgado" de menos de 600 palabras).

2. DENSIDAD DE KEYWORD:
   - El focus keyword (o variaciones naturales/sinonimos cercanos) debe aparecer
     entre el 1% y el 1.5% del total de palabras. NO fuerces repeticiones
     antinaturales solo para cumplir esta metrica: prioriza que se lea humano.

3. CONTENIDO Y CALIDAD (no negociable):
   - NO copies frases textuales de la fuente; parafrasea completamente.
   - Agrega contexto, antecedentes o una perspectiva que no este en el resumen
     original (por que importa, que cambia para el usuario, comparacion con
     hechos previos). Esto es lo que Google llama "contenido util" (helpful content)
     y es mas importante que cualquier metrica de keyword.
   - Evita frases genericas de relleno tipicas de IA ("en la era digital actual",
     "es importante destacar que", "sin duda alguna", "en resumen"). Escribi como
     un periodista humano especializado, con opiniones y matices propios.
   - Voz activa, tono profesional pero cercano (espanol rioplatense).

4. ENLACES (dejalos marcados para que el editor los complete):
   - Incluir al menos 1 sugerencia de ENLACE INTERNO marcada como:
     [ENLACE INTERNO SUGERIDO: nota relacionada sobre <tema>]
   - Incluir al menos 1 ENLACE EXTERNO real hacia la fuente original o una
     fuente primaria (ej: comunicado oficial de la empresa), en formato Markdown:
     [texto del enlace]({item['link']})

5. IMAGEN:
   - Al final, sugeri un ALT_TEXT para la imagen destacada, de 8-12 palabras,
     descriptivo, que incluya el focus keyword de forma natural (no forzada).

===========================================
FORMATO DE SALIDA
===========================================
Devolveme EXCLUSIVAMENTE los campos de arriba (FOCUS_KEYWORD, SEO_TITLE, SLUG,
META_DESCRIPTION, H1, ARTICULO, ALT_TEXT) con esos encabezados exactos en
Markdown. No agregues explicaciones, comentarios ni texto fuera de esa estructura.
Al final del ARTICULO, agrega una linea: "Fuente: {item['source']}".
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
