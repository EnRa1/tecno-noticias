#!/usr/bin/env python3
"""
Pipeline de automatizacion para tecno.ar (Hybrid 2.0 + Articulo Completo)
==========================================================================
1. Filtro rapido por reglas (gratis) -> reduce de cientos a ~20-30
2. Filtro contextual con Gemini (1 sola llamada, con retry) -> elige las 3 mejores
3. Triangulacion de fuentes: busca una segunda nota sobre el mismo tema
4. Extraccion del articulo completo desde ambas fuentes (trafilatura + readability)
5. Busqueda de imagen relevante via Google Custom Search API
6. Redaccion con Gemini usando contenido triangulado (con retry, keyword semantico)
"""

import feedparser
import requests
import json
import os
import re
import time
import hashlib
from pathlib import Path
from datetime import datetime, timezone

import trafilatura
from readability import Document
from bs4 import BeautifulSoup

# ----------------------------------------------------------------------
# CONFIGURACION
# ----------------------------------------------------------------------

BASE_DIR = Path(__file__).parent
FEEDS_FILE = BASE_DIR / "feeds.txt"
SEEN_FILE = BASE_DIR / "seen.json"
DRAFTS_DIR = BASE_DIR / "drafts"

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
GEMINI_MODEL = "gemini-2.5-flash"
GEMINI_URL = (
    f"https://generativelanguage.googleapis.com/v1beta/models/"
    f"{GEMINI_MODEL}:generateContent?key={GEMINI_API_KEY}"
)

GOOGLE_SEARCH_API_KEY = os.environ.get("GOOGLE_SEARCH_API_KEY")
GOOGLE_SEARCH_ENGINE_ID = os.environ.get("GOOGLE_SEARCH_ENGINE_ID")
GOOGLE_SEARCH_URL = "https://www.googleapis.com/customsearch/v1"

MAX_ITEMS_PER_RUN = 4
MAX_HOURS_OLD = 16
DELAY_ENTRE_FASES = 15
GEMINI_MAX_RETRIES = 4
GEMINI_BASE_BACKOFF = 8

# Umbral de similitud para considerar que dos noticias cubren el mismo tema.
# Ahora se calcula con Jaccard sobre palabras clave (no diff de caracteres),
# asi que los valores tipicos son mas bajos que antes.
# 0.15 = permisivo (temas relacionados), 0.30 = mas estricto (casi identicos).
SIMILITUD_MINIMA = 0.18

REQUEST_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "es-AR,es;q=0.9,en-US;q=0.8,en;q=0.7",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

KEYWORDS = [
    "inteligencia artificial", "ai", "ciberseguridad", "seguridad informatica",
    "software", "hardware", "app", "smartphone", "google", "microsoft",
    "apple", "startup", "tecnologia", "internet", "nube", "cloud",
]

STOPWORDS_ES = {
    "el", "la", "los", "las", "un", "una", "unos", "unas", "de", "del", "al",
    "y", "o", "que", "en", "con", "por", "para", "su", "sus", "es", "se",
    "a", "como", "mas", "más", "sobre", "tras", "ya", "no", "lo", "le", "les",
    "esta", "está", "este", "esa", "ese", "sin", "hay", "fue", "son", "ser",
}

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

def tokenizar(texto):
    """Extrae palabras clave relevantes de un texto, sacando stopwords y palabras cortas."""
    palabras = re.findall(r"[a-záéíóúñ0-9]+", texto.lower())
    return {p for p in palabras if p not in STOPWORDS_ES and len(p) > 2}

def similitud_texto(a, b):
    """
    Calcula similitud entre dos strings (0.0 a 1.0) usando el indice de Jaccard
    sobre palabras clave. Es mas robusto que un diff de caracteres (SequenceMatcher)
    porque dos notas que cubren el mismo hecho con redaccion distinta van a compartir
    las palabras clave (nombres propios, terminos tecnicos) aunque el orden y la
    redaccion sean completamente diferentes.
    """
    set_a, set_b = tokenizar(a), tokenizar(b)
    if not set_a or not set_b:
        return 0.0
    interseccion = len(set_a & set_b)
    union = len(set_a | set_b)
    return interseccion / union if union else 0.0

# ----------------------------------------------------------------------
# FILTRO POR FECHA
# ----------------------------------------------------------------------

def is_recent(entry, max_hours=MAX_HOURS_OLD):
    published_parsed = entry.get('published_parsed') or entry.get('updated_parsed')
    if not published_parsed:
        return True
    pub_date = datetime.fromtimestamp(time.mktime(published_parsed), tz=timezone.utc)
    now = datetime.now(timezone.utc)
    diff = now - pub_date
    if diff.total_seconds() < 0:
        return True
    return diff.total_seconds() <= max_hours * 3600

# ----------------------------------------------------------------------
# SISTEMA DE SCORING POR REGLAS
# ----------------------------------------------------------------------

LAUNCH_KEYWORDS = [
    "lanza", "lanzamiento", "presenta", "presento", "anuncia", "anuncio",
    "debuta", "revela", "sale a la venta", "disponible desde", "estrena",
    "launches", "unveils", "announces", "introduces", "debuts", "reveals",
]

HARDWARE_KEYWORDS = [
    "smartphone", "celular", "iphone", "procesador", "chip", "cpu", "gpu",
    "tarjeta grafica", "periferico", "teclado", "mouse", "auriculares",
    "smart tv", "televisor", "auto electrico", "vehiculo electrico", "ev",
    "tablet", "notebook", "laptop", "smartwatch", "wearable", "consola",
    "placa de video", "motherboard", "placa madre", "bateria", "grafeno",
]

ARGENTINA_KEYWORDS = [
    "argentina", "argentino", "buenos aires",
    "mercado libre", "mercadolibre", "globant", "uala", "ualá",
    "satellogic", "auth0", "despegar", "tiendanube",
]

AI_KEYWORDS = [
    "inteligencia artificial", "modelo de ia", "llm", "chatgpt", "gemini",
    "claude", "openai", "anthropic", "copilot", "gpt-", "modelo de lenguaje",
    "machine learning", "deep learning", "red neuronal"
]

PENALTY_KEYWORDS = [
    "lo que tenés que saber", "imperdible", "no te pierdas", "resumen del día",
    "lo mejor de", "top 5", "top 10"
]

def compute_relevance_score(entry_text):
    text = entry_text.lower()
    is_launch = any(kw in text for kw in LAUNCH_KEYWORDS)
    score = 1
    categorias = []

    if any(kw in text for kw in HARDWARE_KEYWORDS):
        score += 3
        if is_launch:
            score += 2
        categorias.append("hardware")

    if any(kw in text for kw in AI_KEYWORDS):
        score += 3
        if is_launch:
            score += 2
        categorias.append("ia")

    if is_launch and any(kw in text for kw in ARGENTINA_KEYWORDS):
        score += 1
        categorias.append("argentina")

    if any(kw in text for kw in PENALTY_KEYWORDS):
        score -= 2

    return max(0, min(10, score)), (categorias[0] if categorias else "general")

# ----------------------------------------------------------------------
# TRIANGULACION DE FUENTES
# ----------------------------------------------------------------------

def encontrar_fuente_secundaria(item_principal, todos_los_candidatos):
    """
    Busca entre todos los candidatos una segunda nota que cubra el mismo
    tema que el item principal, de una fuente distinta.
    Usa similitud Jaccard de palabras clave (titulo + resumen) como criterio.
    Devuelve el candidato más similar (si supera el umbral) o None.
    """
    texto_principal = item_principal["title"] + " " + item_principal["summary"]
    mejor_similitud = 0
    mejor_candidato = None

    for candidato in todos_los_candidatos:
        # Descartar si es el mismo item o de la misma fuente
        if candidato["hash"] == item_principal["hash"]:
            continue
        if candidato["source"] == item_principal["source"]:
            continue

        texto_candidato = candidato["title"] + " " + candidato["summary"]
        sim = similitud_texto(texto_principal, texto_candidato)

        if sim > mejor_similitud:
            mejor_similitud = sim
            mejor_candidato = candidato

    if mejor_similitud >= SIMILITUD_MINIMA:
        print(f"  🔗 Fuente secundaria encontrada ({mejor_similitud:.0%} similitud): "
              f"{mejor_candidato['source']} — {mejor_candidato['title'][:60]}")
        return mejor_candidato

    print(f"  ℹ️ No se encontró fuente secundaria con similitud suficiente "
          f"(máx: {mejor_similitud:.0%}, umbral: {SIMILITUD_MINIMA:.0%})")
    return None

# ----------------------------------------------------------------------
# BUSQUEDA DE IMAGEN VIA GOOGLE CUSTOM SEARCH
# ----------------------------------------------------------------------

def buscar_imagen_google(query, fallback_url=None):
    """
    Busca una imagen relacionada con el tema del artículo usando
    Google Custom Search API (100 búsquedas/día gratis).
    Devuelve la URL de la primera imagen encontrada, o fallback_url si falla.
    """
    if not GOOGLE_SEARCH_API_KEY or not GOOGLE_SEARCH_ENGINE_ID:
        print("⚠️ Sin credenciales de Google Search (revisar env vars del workflow), "
              "usando imagen de la fuente original.")
        return fallback_url

    # Limitar el query a las primeras palabras más relevantes para mayor precisión
    query_corto = " ".join(query.split()[:8])
    print(f"🔍 Buscando imagen para: '{query_corto}'...")

    params = {
        "key": GOOGLE_SEARCH_API_KEY,
        "cx": GOOGLE_SEARCH_ENGINE_ID,
        "q": query_corto,
        "searchType": "image",
        "num": 5,                    # traer 5 y elegir la mejor
        "imgSize": "large",          # imágenes grandes (mejor calidad para Instagram)
        "imgType": "photo",           # tipo noticia, más relevante para tech
        "safe": "active",
        "fileType": "jpg",
    }

    try:
        resp = requests.get(GOOGLE_SEARCH_URL, params=params, timeout=15)
        if resp.status_code == 200:
            items = resp.json().get("items", [])
            if items:
                # Elegir la primera que no sea un logo pequeño
                for item in items:
                    image_info = item.get("image", {})
                    width = image_info.get("width", 0)
                    height = image_info.get("height", 0)
                    url = item.get("link", "")
                    if width >= 400 and height >= 300 and url:
                        print(f"✅ Imagen encontrada: {url[:80]}")
                        return url
                # Si todas son pequeñas, devolver la primera igual
                primera_url = items[0].get("link", "")
                if primera_url:
                    print(f"✅ Imagen encontrada (sin filtro de tamaño): {primera_url[:80]}")
                    return primera_url
        else:
            print(f"⚠️ Error Google Search API: {resp.status_code} — {resp.text[:200]}")
    except Exception as e:
        print(f"⚠️ Excepción buscando imagen: {e}")

    print("⚠️ No se encontró imagen, usando imagen de la fuente original.")
    return fallback_url

# ----------------------------------------------------------------------
# EXTRACCION DEL ARTICULO COMPLETO (robusta, con fallback en cascada)
# ----------------------------------------------------------------------

def _fetch_html(url, timeout=15):
    resp = requests.get(url, headers=REQUEST_HEADERS, timeout=timeout, allow_redirects=True)
    resp.raise_for_status()
    resp.encoding = resp.apparent_encoding
    return resp.text

def _extract_with_trafilatura(html, url):
    text = trafilatura.extract(
        html,
        url=url,
        favor_precision=True,
        include_comments=False,
        include_tables=False,
    )
    if text and len(text) >= 200:
        metadata = trafilatura.extract_metadata(html, default_url=url)
        return {
            "title": metadata.title if metadata else "",
            "text": text,
            "authors": [metadata.author] if metadata and metadata.author else [],
            "publish_date": metadata.date if metadata else None,
            "top_image": metadata.image if metadata else None,
        }
    return None

def _extract_with_readability(html, url):
    try:
        doc = Document(html)
        title = doc.short_title()
        summary_html = doc.summary()
        soup = BeautifulSoup(summary_html, "html.parser")
        text = "\n\n".join(
            p.get_text(strip=True) for p in soup.find_all("p") if p.get_text(strip=True)
        )
        if text and len(text) >= 200:
            return {"title": title, "text": text, "authors": [],
                    "publish_date": None, "top_image": None}
    except Exception:
        pass
    return None

def extract_full_article(url):
    try:
        print(f"  📄 Extrayendo: {url[:60]}...")
        html = _fetch_html(url)
    except requests.exceptions.RequestException as e:
        print(f"  ⚠️ No se pudo descargar ({type(e).__name__}): {e}")
        return None

    result = _extract_with_trafilatura(html, url)
    if result:
        print(f"  ✅ Trafilatura ({len(result['text'])} caracteres)")
        return result

    result = _extract_with_readability(html, url)
    if result:
        print(f"  ✅ Readability fallback ({len(result['text'])} caracteres)")
        return result

    print("  ⚠️ No se pudo extraer texto, se usará resumen del RSS.")
    return None

# ----------------------------------------------------------------------
# HELPER COMPARTIDO: LLAMADA A GEMINI CON RETRY/BACKOFF
# ----------------------------------------------------------------------

def call_gemini_api(payload, context="gemini", retries=GEMINI_MAX_RETRIES):
    if not GEMINI_API_KEY:
        raise RuntimeError("Falta la variable de entorno GEMINI_API_KEY")

    last_error = None

    for attempt in range(retries):
        try:
            resp = requests.post(GEMINI_URL, json=payload, timeout=60)

            if resp.status_code == 200:
                return resp.json()
            elif resp.status_code == 429:
                wait = GEMINI_BASE_BACKOFF * (2 ** attempt)
                print(f"[RATE LIMIT] {context}: intento {attempt + 1}/{retries}, esperando {wait}s...")
                time.sleep(wait)
                last_error = RuntimeError(f"429 rate limit tras {retries} intentos ({context})")
                continue
            elif resp.status_code >= 500:
                wait = GEMINI_BASE_BACKOFF * (2 ** attempt)
                print(f"[SERVER ERROR] {context}: {resp.status_code}, esperando {wait}s...")
                time.sleep(wait)
                last_error = RuntimeError(f"Error {resp.status_code} ({context})")
                continue
            else:
                raise RuntimeError(f"Error Gemini {resp.status_code} ({context}): {resp.text[:300]}")

        except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as e:
            wait = GEMINI_BASE_BACKOFF * (2 ** attempt)
            print(f"[NETWORK ERROR] {context}: {type(e).__name__}, esperando {wait}s...")
            time.sleep(wait)
            last_error = e
            continue

    raise RuntimeError(f"Se agotaron los reintentos en {context}: {last_error}")

# ----------------------------------------------------------------------
# FILTRO CONTEXTUAL CON GEMINI (1 SOLA LLAMADA, CON RETRY)
# ----------------------------------------------------------------------

def rank_with_gemini(candidatos):
    if not candidatos or len(candidatos) <= MAX_ITEMS_PER_RUN:
        return candidatos

    if not GEMINI_API_KEY:
        print("⚠️ Sin API Key, usando orden por reglas.")
        return candidatos

    print(f"🧠 Enviando {len(candidatos)} noticias a Gemini para ranking contextual...")

    lista_texto = ""
    for idx, item in enumerate(candidatos, 1):
        lista_texto += f"{idx}. Título: {item['title']}\n   Resumen: {item['summary'][:250]}\n\n"

    prompt = f"""
    Eres un editor jefe de un blog de tecnología llamado tecno.ar.
    Tu tarea es seleccionar las {MAX_ITEMS_PER_RUN} noticias MÁS RELEVANTES Y CON CONTEXTO de la siguiente lista.

    Criterios de selección (en orden de prioridad):
    1. **Lanzamientos oficiales** de hardware (celulares, procesadores, GPU, televisores, wearables, gadgets, etc.) o software.
    2. **Avances reales en Inteligencia Artificial** (nuevos modelos, aplicaciones prácticas).
    3. **Empresas de Tecnología** (negociaciones, tratos, convenios).
    4. **Ciberseguridad** (ataques reales, vulnerabilidades críticas).
    5. Ignora artículos de opinión, análisis retrospectivos, o rumores sin fuentes.

    Lista de noticias:
    {lista_texto}

    Debes devolver SOLO un JSON con los números de los {MAX_ITEMS_PER_RUN} índices seleccionados, en orden de prioridad.
    Formato exacto: {{"seleccionados": [3, 7, 12]}}
    """

    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "response_mime_type": "application/json",
            "temperature": 0.1
        }
    }

    try:
        data = call_gemini_api(payload, context="ranking")
        raw_text = data["candidates"][0]["content"]["parts"][0]["text"]
        result = json.loads(raw_text)
        indices = result.get("seleccionados", [])

        if not indices:
            print("⚠️ Gemini no devolvió índices, usando orden por reglas.")
            return candidatos[:MAX_ITEMS_PER_RUN]

        seleccionados = []
        for i in indices:
            if 1 <= i <= len(candidatos):
                seleccionados.append(candidatos[i - 1])
            if len(seleccionados) >= MAX_ITEMS_PER_RUN:
                break

        print(f"✅ Gemini seleccionó {len(seleccionados)} noticias por contexto.")
        return seleccionados

    except Exception as e:
        print(f"⚠️ No se pudo rankear con Gemini ({e}), usando orden por reglas.")
        return candidatos[:MAX_ITEMS_PER_RUN]

# ----------------------------------------------------------------------
# INGESTA + FILTROS
# ----------------------------------------------------------------------

MAX_POR_FUENTE = max(1, MAX_ITEMS_PER_RUN // 2)

def fetch_new_relevant_items():
    seen = load_seen()
    candidatos = []
    candidatos_para_triangular = []  # pool amplio, sin filtro de score_reglas>=3

    for url in load_feeds():
        try:
            feed = feedparser.parse(url)
        except Exception as e:
            print(f"[WARN] No se pudo leer {url}: {e}")
            continue

        source_name = feed.feed.get("title", url)

        for entry in feed.entries:
            h = item_hash(entry)
            if h in seen:
                continue
            if not is_recent(entry):
                continue
            if not is_relevant(entry):
                continue

            texto_completo = entry.get("title", "") + " " + entry.get("summary", "")
            score_reglas, categoria_reglas = compute_relevance_score(texto_completo)

            item_data = {
                "hash": h,
                "title": entry.get("title", "Sin titulo"),
                "link": entry.get("link", ""),
                "summary": re.sub("<[^<]+?>", "", entry.get("summary", ""))[:600],
                "source": source_name,
                "published": entry.get("published", ""),
                "score_reglas": score_reglas,
                "categoria_reglas": categoria_reglas,
            }

            # Pool amplio para triangulacion: cualquier nota relevante y reciente,
            # aunque no llegue al score minimo de seleccion.
            candidatos_para_triangular.append(item_data)

            if score_reglas < 3:
                continue

            candidatos.append(item_data)

    print(f"📰 Noticias que pasaron el filtro rapido: {len(candidatos)} "
          f"(pool de triangulación: {len(candidatos_para_triangular)})")

    if not candidatos:
        return [], []

    if len(candidatos) > 30:
        candidatos.sort(key=lambda x: x["score_reglas"], reverse=True)
        candidatos = candidatos[:30]
        print("🔪 Limitando a 30 para el ranking contextual.")

    if len(candidatos) > MAX_ITEMS_PER_RUN:
        seleccionados_por_ia = rank_with_gemini(candidatos)
    else:
        seleccionados_por_ia = candidatos

    seleccionados_final = []
    conteo_por_fuente = {}

    for item in seleccionados_por_ia:
        if len(seleccionados_final) >= MAX_ITEMS_PER_RUN:
            break
        fuente = item["source"]
        if conteo_por_fuente.get(fuente, 0) >= MAX_POR_FUENTE:
            continue

        seleccionados_final.append(item)
        conteo_por_fuente[fuente] = conteo_por_fuente.get(fuente, 0) + 1
        seen[item["hash"]] = {"title": item["title"], "date": datetime.now().isoformat()}

    save_seen(seen)

    print("\n🏆 NOTICIAS SELECCIONADAS FINALMENTE:")
    for it in seleccionados_final:
        print(f"  [{it['score_reglas']:>2}pts | {it['categoria_reglas']:<9}] {it['title'][:70]}")

    # Devolvemos el pool AMPLIO para triangular, no solo los que pasaron score>=3
    return seleccionados_final, candidatos_para_triangular

# ----------------------------------------------------------------------
# CONSTRUCCION DEL PROMPT (con soporte para fuente secundaria)
# ----------------------------------------------------------------------

def build_prompt(item, full_text_principal, fuente_secundaria=None,
                 full_text_secundario=None, imagen_url=None):
    text_limit = full_text_principal[:6000] if full_text_principal else item['summary']

    bloque_secundario = ""
    if fuente_secundaria and full_text_secundario:
        text_sec = full_text_secundario[:3000]
        bloque_secundario = f"""
FUENTE SECUNDARIA (segundo medio que cubre el mismo tema — usala para enriquecer,
contrastar datos, o agregar perspectivas que no estén en la fuente principal):
Título: {fuente_secundaria['title']}
Medio: {fuente_secundaria['source']}
URL: {fuente_secundaria['link']}

{text_sec}
"""
    elif fuente_secundaria:
        bloque_secundario = f"""
FUENTE SECUNDARIA (resumen del RSS, no se pudo extraer el artículo completo):
Título: {fuente_secundaria['title']}
Medio: {fuente_secundaria['source']}
URL: {fuente_secundaria['link']}
Resumen: {fuente_secundaria['summary']}
"""

    instruccion_imagen = ""
    if imagen_url:
        instruccion_imagen = f"\nImagen sugerida para el artículo (URL): {imagen_url}"

    return f"""Actua como un redactor SEO senior especializado en tecnologia,
con dominio experto de los criterios de puntuacion de Rank Math para WordPress,
escribiendo para el sitio tecno.ar.

FUENTE PRINCIPAL (basate principalmente en este texto para redactar):
Titulo original: {item['title']}
Medio: {item['source']}
URL original: {item['link']}
{instruccion_imagen}

TEXTO COMPLETO DEL ARTICULO PRINCIPAL:
{text_limit}
{bloque_secundario}
===========================================
INSTRUCCIONES DE REDACCION CON FUENTES MULTIPLES
===========================================
{"Tenés DOS fuentes sobre el mismo tema. Usá ambas para: (1) cruzar datos, (2) agregar perspectivas o detalles que solo tenga una de las dos. NO copies frases textuales de ninguna fuente." if bloque_secundario else "Redactá basándote en la fuente principal, parafraseando completamente y agregando contexto."}

===========================================
PASO 1: DEFINI EL FOCUS KEYWORD (REGLAS SEMANTICAS ESTRICTAS)
===========================================
El focus keyword NO es una etiqueta ni un hashtag: tiene que ser una frase
que un lector diria en una oracion normal en español. Muchas veces la noticia
tiene un producto con nombre propio + marca (ej: "GPT-Live" de "OpenAI",
"Moto Tag 2" de "Motorola"). En esos casos, JAMAS encadenes los dos nombres
propios uno al lado del otro sin conector, porque eso no es una frase real
y despues no se puede insertar en el texto sin que quede forzado.

EJEMPLOS DE KEYWORDS INCORRECTOS (rechazar siempre este patron):
- "GPT-Live OpenAI"        -> mal: dos nombres propios pegados, no es una frase
- "Apple Broadcom Chips"   -> mal: tres sustantivos en ingles sin conector
- "Moto Tag 2 Motorola"    -> mal: producto + marca sin relacion gramatical

EJEMPLOS DE KEYWORDS CORRECTOS PARA ESOS MISMOS CASOS:
- "modo de voz de ChatGPT"        (en vez de "GPT-Live OpenAI")
- "chips de Apple con Broadcom"   (en vez de "Apple Broadcom Chips")
- "rastreador Moto Tag 2"         (en vez de "Moto Tag 2 Motorola")

REGLA GENERAL: si el nombre del producto ya es un sustantivo reconocible en
español (rastreador, auriculares, procesador, modelo, chatbot, app, robot,
notebook, etc.), combina ESA categoria con el nombre propio del producto.

CHECKLIST antes de definir el keyword final:
1. ¿Se puede leer el keyword dentro de una oracion completa sin sonar
   una lista de nombres propios pegados?
2. ¿Tiene al menos una palabra funcional en español (de, con, para, en)?
3. ¿Es asi como lo diria un periodista en voz alta?

===========================================
PASO 2: GENERA TODOS ESTOS CAMPOS (en este orden exacto)
===========================================

## FOCUS_KEYWORD
[el keyword elegido (no debe variar, debe ser igual al elegido), ya validado con el checklist de arriba]

## SEO_TITLE
Titulo de 50-60 caracteres. Reglas:
- El focus keyword debe aparecer LO MAS CERCA POSIBLE DEL INICIO del titulo. (El keyword no debe variar dentro del artículo, debe permanecer igual)

## SLUG
version-corta-en-minusculas-con-guiones-del-focus-keyword
(5-6 palabras maximo)
Si la keyword tiene conectores, el slug tambien debe tener los conectores.

## META_DESCRIPTION
Entre 150 y 160 caracteres. Debe incluir el focus keyword (keyword debe permanecer igual al definido). NO colocar estos caracteres: ** **  

## H1
El titulo visible del articulo. Debe incluir el focus keyword (igual al keyword definido, no debe variar).

## ARTICULO
El cuerpo de la nota en Markdown (600-900 palabras), siguiendo ESTAS reglas:
1. ESTRUCTURA:
   - El focus keyword debe aparecer en el PRIMER PARRAFO, integrado en una
     oracion natural. (keyword debe permanecer igual al definido)
   - Dividi el cuerpo en al menos 3-4 subtitulos H2 (##).
   - Parrafos cortos: maximo 3-4 lineas cada uno.
2. CONTENIDO:
   - NO copies frases textuales de la fuente; parafrasea completamente.
   - Evita frases genericas de relleno tipicas de IA.
   - Voz activa, tono profesional pero cercano (espanol).
   - COHERENCIA: relee mentalmente cada oracion donde aparece el focus
     keyword y confirma que se entiende igual que el resto del texto.
3. ENLACES:
   - No se debe mencionar en el cuerpo del artículo el nombre de otros medios. Incluir al menos 1 ENLACE EXTERNO real hacia la fuente original (El enlace debe estar puesto cuando se mencione la palabra clave):
     [texto del enlace]({item['link']})
   {"- Incluir también 1 ENLACE a la fuente secundaria: [texto](" + fuente_secundaria['link'] + ")" if fuente_secundaria else ""}


===========================================
FORMATO DE SALIDA
===========================================
Devolveme EXCLUSIVAMENTE los campos de arriba (FOCUS_KEYWORD, SEO_TITLE, SLUG,
META_DESCRIPTION, H1, ARTICULO, ALT_TEXT) con esos encabezados exactos en
Markdown. No agregues explicaciones fuera de esa estructura.
Al final del ARTICULO, agrega: "Fuente: {item['source']}{f' y {fuente_secundaria["source"]}' if fuente_secundaria else ''}"
"""

# ----------------------------------------------------------------------
# REDACCION CON GEMINI
# ----------------------------------------------------------------------

def call_gemini(prompt):
    payload = {"contents": [{"parts": [{"text": prompt}]}]}
    data = call_gemini_api(payload, context="redaccion")
    try:
        return data["candidates"][0]["content"]["parts"][0]["text"]
    except (KeyError, IndexError):
        raise RuntimeError(f"Respuesta inesperada de Gemini: {data}")

# ----------------------------------------------------------------------
# GUARDADO DE BORRADORES LOCALES
# ----------------------------------------------------------------------

def save_draft(item, article_md, imagen_url=None):
    """
    Guarda el borrador en drafts/. El header incluye la URL de la imagen
    encontrada por Google Custom Search (o fallback de la fuente original),
    para que publish_to_wordpress.py pueda descargarla y subirla como
    imagen destacada en el step siguiente del workflow.
    """
    DRAFTS_DIR.mkdir(exist_ok=True)
    date_str = datetime.now().strftime("%Y-%m-%d")
    filename = f"{date_str}_{slugify(item['title'])}.md"
    path = DRAFTS_DIR / filename

    header = (
        f"<!--\n"
        f"ESTADO: borrador sin revisar - NO publicar directo\n"
        f"Fuente original: {item['link']}\n"
        f"Imagen sugerida: {imagen_url or ''}\n"
        f"Fecha generacion: {datetime.now().isoformat()}\n"
        f"-->\n\n"
    )
    path.write_text(header + article_md, encoding="utf-8")
    print(f"[OK] Borrador guardado localmente: {path}")

# ----------------------------------------------------------------------
# MAIN
# ----------------------------------------------------------------------

def main():
    print("🚀 Iniciando pipeline Hybrid 2.0 con triangulacion de fuentes e imagen IA...")
    print(f"DEBUG: GEMINI_API_KEY {'OK' if GEMINI_API_KEY else 'FALTA'}")
    print(f"DEBUG: GOOGLE_SEARCH_API_KEY {'OK' if GOOGLE_SEARCH_API_KEY else 'FALTA'}")
    print(f"DEBUG: GOOGLE_SEARCH_ENGINE_ID {'OK' if GOOGLE_SEARCH_ENGINE_ID else 'FALTA'}")

    items, todos_los_candidatos = fetch_new_relevant_items()
    print(f"Encontrados {len(items)} items nuevos para procesar.")

    if items:
        print(f"⏳ Esperando {DELAY_ENTRE_FASES}s antes de redactar (evitar rate limit)...")
        time.sleep(DELAY_ENTRE_FASES)

    for item in items:
        print(f"\n{'='*60}")
        print(f"📄 Procesando: {item['title'][:70]}...")

        # 1. Triangulacion: buscar fuente secundaria
        print("🔗 Buscando fuente secundaria...")
        fuente_secundaria = encontrar_fuente_secundaria(item, todos_los_candidatos)

        # 2. Extraer articulo principal
        print("📥 Extrayendo fuente principal...")
        full_article = extract_full_article(item['link'])
        contenido_principal = (
            full_article['text'] if full_article and full_article.get('text')
            else item['summary']
        )

        # 3. Extraer fuente secundaria (si existe)
        contenido_secundario = None
        if fuente_secundaria:
            print("📥 Extrayendo fuente secundaria...")
            full_article_sec = extract_full_article(fuente_secundaria['link'])
            contenido_secundario = (
                full_article_sec['text'] if full_article_sec and full_article_sec.get('text')
                else None
            )

        # 4. Buscar imagen relevante con Google Custom Search
        print("🖼️ Buscando imagen relevante...")
        fallback_image = full_article.get('top_image') if full_article else None
        imagen_url = buscar_imagen_google(item['title'], fallback_url=fallback_image)

        # 5. Redactar con Gemini (con todo el contexto triangulado)
        try:
            prompt = build_prompt(
                item,
                contenido_principal,
                fuente_secundaria=fuente_secundaria,
                full_text_secundario=contenido_secundario,
                imagen_url=imagen_url,
            )
            article = call_gemini(prompt)
            save_draft(item, article, imagen_url=imagen_url)

        except Exception as e:
            print(f"[ERROR] No se pudo procesar '{item['title']}': {e}")

        time.sleep(6)

    print("\n✅ Pipeline finalizado.")

if __name__ == "__main__":
    main()
