#!/usr/bin/env python3
"""
Pipeline de automatizacion para tecno.ar (Hybrid 2.0 + Articulo Completo)
==========================================================================
1. Filtro rapido por reglas (gratis) -> reduce de cientos a ~20-30
2. Filtro contextual con Gemini (1 sola llamada, con retry) -> elige las mejores
3. Triangulacion de fuentes: pool de RSS primero, busqueda activa en la web despues
4. Extraccion del articulo completo desde todas las fuentes (trafilatura + readability)
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
GEMINI_MODEL = "gemini-3-flash-preview"
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

MAX_FUENTES_ADICIONALES = 2

# Umbral de similitud para considerar que dos noticias del pool de RSS
# cubren el mismo tema. Se calcula con Jaccard sobre palabras clave.
SIMILITUD_MINIMA = 0.18

# Dominios que no cuentan como "fuente distinta" util al triangular via web
# (agregadores, redes sociales; el dominio de origen se excluye dinamicamente).
DOMINIOS_EXCLUIDOS = {
    "twitter.com", "x.com", "facebook.com", "reddit.com",
    "youtube.com", "tecno.ar",
}

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

def _extraer_dominio(url):
    match = re.search(r"https?://(?:www\.)?([^/]+)", url)
    return match.group(1).lower() if match else ""

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
# TRIANGULACION DE FUENTES - PASO 1: DENTRO DEL POOL DE RSS (gratis)
# ----------------------------------------------------------------------

def encontrar_fuente_secundaria(item_principal, todos_los_candidatos):
    """
    Busca entre los candidatos de RSS ya descargados una segunda nota que
    cubra el mismo tema que el item principal, de una fuente distinta.
    Usa similitud Jaccard de palabras clave (titulo + resumen) como criterio.
    Devuelve el candidato más similar (si supera el umbral) o None.
    """
    texto_principal = item_principal["title"] + " " + item_principal["summary"]
    mejor_similitud = 0
    mejor_candidato = None

    for candidato in todos_los_candidatos:
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
        print(f"  🔗 Fuente secundaria encontrada en RSS ({mejor_similitud:.0%} similitud): "
              f"{mejor_candidato['source']} — {mejor_candidato['title'][:60]}")
        return mejor_candidato

    print(f"  ℹ️ No se encontró fuente secundaria en el pool de RSS "
          f"(máx: {mejor_similitud:.0%}, umbral: {SIMILITUD_MINIMA:.0%})")
    return None

# ----------------------------------------------------------------------
# TRIANGULACION DE FUENTES - PASO 2: BUSQUEDA ACTIVA EN LA WEB (fallback)
# ----------------------------------------------------------------------

def buscar_fuentes_relacionadas(item, max_fuentes=MAX_FUENTES_ADICIONALES):
    """
    Busca en toda la web (via Google Custom Search, modo texto) noticias que
    cubran el mismo tema que el item principal, en dominios distintos al
    de origen. Devuelve una lista de hasta max_fuentes dicts compatibles
    con el formato de 'candidato' que usa build_prompt.

    Requiere que el motor de Programmable Search Engine tenga activado
    "Search the entire web" en su configuracion (si no, solo va a buscar
    dentro de los sitios especificos que tenga cargados, si tiene alguno).
    """
    if not GOOGLE_SEARCH_API_KEY or not GOOGLE_SEARCH_ENGINE_ID:
        print("  ⚠️ Sin credenciales de Google Search, no se puede buscar en la web.")
        return []

    query = " ".join(item["title"].split()[:10])
    dominio_origen = _extraer_dominio(item["link"])
    print(f"  🌐 Buscando fuentes relacionadas en la web para: '{query[:60]}...'")

    params = {
        "key": GOOGLE_SEARCH_API_KEY,
        "cx": GOOGLE_SEARCH_ENGINE_ID,
        "q": query,
        "num": 10,
        "safe": "active",
        "dateRestrict": "d1",  # solo resultados de los ultimos 3 dias
    }

    try:
        resp = requests.get(GOOGLE_SEARCH_URL, params=params, timeout=15)
        if resp.status_code != 200:
            print(f"  ⚠️ Error Google Search (texto): {resp.status_code} — {resp.text[:200]}")
            return []

        items_resultado = resp.json().get("items", [])
        if not items_resultado:
            print("  ℹ️ No se encontraron resultados relacionados en la web.")
            return []

        fuentes = []
        dominios_vistos = {dominio_origen}

        for r in items_resultado:
            link = r.get("link", "")
            dominio = _extraer_dominio(link)

            if not dominio or dominio in dominios_vistos:
                continue
            if any(excl in dominio for excl in DOMINIOS_EXCLUIDOS):
                continue

            fuentes.append({
                "hash": item_hash({"link": link}),
                "title": r.get("title", "Sin titulo"),
                "link": link,
                "summary": r.get("snippet", ""),
                "source": r.get("displayLink", dominio),
            })
            dominios_vistos.add(dominio)

            if len(fuentes) >= max_fuentes:
                break

        if fuentes:
            print(f"  ✅ {len(fuentes)} fuente(s) relacionada(s) encontrada(s): "
                  + ", ".join(f["source"] for f in fuentes))
        else:
            print("  ℹ️ Resultados encontrados, pero todos del mismo dominio o excluidos.")

        return fuentes

    except Exception as e:
        print(f"  ⚠️ Excepción buscando fuentes relacionadas: {e}")
        return []

# ----------------------------------------------------------------------
# BUSQUEDA DE IMAGEN VIA GOOGLE CUSTOM SEARCH
# ----------------------------------------------------------------------

def buscar_imagen_google(query, fallback_url=None):
    """
    Busca una imagen relacionada con el tema del artículo usando
    Google Custom Search API (100 búsquedas/día gratis, comparte cuota
    con las búsquedas de texto de buscar_fuentes_relacionadas).
    Devuelve la URL de la primera imagen encontrada, o fallback_url si falla.
    """
    if not GOOGLE_SEARCH_API_KEY or not GOOGLE_SEARCH_ENGINE_ID:
        print("⚠️ Sin credenciales de Google Search (revisar env vars del workflow), "
              "usando imagen de la fuente original.")
        return fallback_url

    query_corto = " ".join(query.split()[:8])
    print(f"🔍 Buscando imagen para: '{query_corto}'...")

    params = {
        "key": GOOGLE_SEARCH_API_KEY,
        "cx": GOOGLE_SEARCH_ENGINE_ID,
        "q": query_corto,
        "searchType": "image",
        "num": 5,
        "imgSize": "large",
        "imgType": "",
        "safe": "active",
        "fileType": "jpg",
    }

    try:
        resp = requests.get(GOOGLE_SEARCH_URL, params=params, timeout=15)
        if resp.status_code == 200:
            items = resp.json().get("items", [])
            if items:
                for item in items:
                    image_info = item.get("image", {})
                    width = image_info.get("width", 0)
                    height = image_info.get("height", 0)
                    url = item.get("link", "")
                    if width >= 400 and height >= 300 and url:
                        print(f"✅ Imagen encontrada: {url[:80]}")
                        return url
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
Eres un editor jefe de un blog de tecnología llamado tecno.ar. Tu tarea es
seleccionar las {MAX_ITEMS_PER_RUN} noticias MÁS RELEVANTES de la lista al final
de este mensaje, aplicando los criterios de abajo EN ORDEN DE PRIORIDAD.

===========================================
CRITERIO 1 (máxima prioridad): LANZAMIENTOS OFICIALES DE HARDWARE O SOFTWARE
===========================================
Es la noticia PRIMARIA de un producto que sale al mercado, se anuncia
oficialmente, o se actualiza con una versión nueva. La empresa fabricante
o desarrolladora es quien hace el anuncio (no un tercero especulando).

SI CALIFICA (ejemplos):
- "Samsung presenta el Galaxy S26 con nuevo procesador propio"
- "Apple lanza iOS 20 con rediseño completo de la interfaz"
- "NVIDIA anuncia la nueva serie RTX 6000 para gaming"

NO CALIFICA (ejemplos):
- "Se filtran posibles specs del próximo iPhone" -> es rumor/filtración, no lanzamiento oficial
- "5 cosas que esperamos ver en el próximo Galaxy Unpacked" -> es especulación/preview, no anuncio real
- "Análisis: por qué el nuevo MacBook no convence" -> es opinión/review, no la noticia del lanzamiento en sí

===========================================
CRITERIO 2: AVANCES REALES EN INTELIGENCIA ARTIFICIAL
===========================================
Modelos nuevos, funciones nuevas lanzadas, o aplicaciones prácticas ya
disponibles. Debe ser un avance concreto y verificable, no una promesa a futuro
ni una reflexión general sobre "el futuro de la IA".

SI CALIFICA (ejemplos):
- "OpenAI lanza GPT-6 con capacidades de razonamiento mejoradas"
- "Google integra Gemini directamente en Google Sheets"
- "Anthropic presenta Claude con nueva función de memoria persistente"

NO CALIFICA (ejemplos):
- "Cómo la IA cambiará el trabajo en los próximos 10 años" -> es un ensayo/opinión, no una noticia de un avance concreto
- "Empresas advierten sobre riesgos regulatorios de la IA" -> es cobertura de políticas/regulación, no un avance de producto
- "Rumores indican que Meta prepara un nuevo modelo" -> es especulación sin confirmación oficial

===========================================
CRITERIO 3: EMPRESAS DE TECNOLOGÍA (negociaciones, tratos, convenios)
===========================================
Adquisiciones, fusiones, rondas de inversión, alianzas estratégicas o
convenios comerciales concretos y confirmados entre empresas.

SI CALIFICA (ejemplos):
- "Microsoft adquiere la startup de ciberseguridad XDR por USD 500 millones"
- "Mercado Libre firma un convenio con Visa para pagos internacionales"
- "Globant anuncia una ronda de inversión de USD 200 millones"

NO CALIFICA (ejemplos):
- "Las 10 empresas tech más valiosas del mundo en 2026" -> es un ranking/listicle, no una noticia de un trato concreto
- "¿Podría Apple comprar Netflix?" -> es especulación, no una negociación confirmada

===========================================
CRITERIO 4 (mínima prioridad): CIBERSEGURIDAD
===========================================
Ataques reales ya ocurridos, vulnerabilidades críticas confirmadas (CVE,
parches urgentes), o filtraciones de datos reales y verificadas.

SI CALIFICA (ejemplos):
- "Ransomware ataca los servidores de una aerolínea europea"
- "Descubren vulnerabilidad crítica en routers de Cisco, ya hay parche disponible"
- "Filtración expone datos de 2 millones de usuarios de una app de delivery"

NO CALIFICA (ejemplos):
- "10 consejos para protegerte de hackers" -> es contenido educativo genérico, no una noticia de un incidente real
- "Los ciberataques más comunes en 2026" -> es un resumen/listicle, no un evento puntual

===========================================
CRITERIO DE DESCARTE (aplica a cualquier categoría, siempre)
===========================================
Ignorá SIEMPRE, sin importar el tema, artículos que sean:
- Opinión o análisis retrospectivo ("por qué X importa", "lo que aprendimos de...")
- Rumores, filtraciones o especulación sin confirmación oficial de la empresa
- Rankings, resúmenes, "top 10", "lo mejor de la semana", roundups
- Reviews de productos que ya llevan tiempo en el mercado (no son lanzamientos nuevos)

===========================================
CÓMO DECIDIR ENTRE VARIAS NOTICIAS DEL MISMO CRITERIO
===========================================
Si hay más candidatas de las que necesitás dentro de un mismo criterio,
priorizá la que tenga: (a) confirmación oficial más directa de la empresa
involucrada, (b) mayor impacto o alcance (una empresa grande y conocida
pesa más que una startup poco conocida), (c) mayor actualidad (evento más
reciente dentro de la ventana de tiempo).

===========================================
LISTA DE NOTICIAS A EVALUAR
===========================================
{lista_texto}

===========================================
FORMATO DE SALIDA (obligatorio)
===========================================
Devolvé SOLO un JSON con los números de los {MAX_ITEMS_PER_RUN} índices
seleccionados, en orden de prioridad (el más relevante primero).
No agregues texto explicativo antes ni después del JSON.
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

    return seleccionados_final, candidatos_para_triangular

# ----------------------------------------------------------------------
# CONSTRUCCION DEL PROMPT (soporta multiples fuentes adicionales)
# ----------------------------------------------------------------------

def build_prompt(item, full_text_principal, fuentes_adicionales=None, imagen_url=None):
    """
    fuentes_adicionales: lista de dicts, cada uno con al menos
    {title, source, link} y opcionalmente {summary, texto_completo}.
    """
    text_limit = full_text_principal[:6000] if full_text_principal else item['summary']
    fuentes_adicionales = fuentes_adicionales or []

    bloque_secundario = ""
    for idx, f in enumerate(fuentes_adicionales, 1):
        texto = (f.get("texto_completo") or f.get("summary", ""))[:3000]
        bloque_secundario += f"""
FUENTE ADICIONAL {idx} (medio distinto que cubre el mismo tema — usala para
cruzar datos, agregar perspectivas o detalles que no esten en la fuente
principal):
Título: {f['title']}
Medio: {f['source']}
URL: {f['link']}

{texto}
"""

    instruccion_imagen = ""
    if imagen_url:
        instruccion_imagen = f"\nImagen sugerida para el artículo (URL): {imagen_url}"

    instruccion_fuentes = (
        f"Tenés {len(fuentes_adicionales) + 1} fuentes sobre el mismo tema. Usalas todas para: "
        f"(1) cruzar datos y mencionar si coinciden o difieren entre medios, "
        f"(2) agregar perspectivas o detalles que solo tenga alguna de ellas. "
        f"NO copies frases textuales de ninguna fuente."
        if fuentes_adicionales else
        "Redactá basándote en la fuente principal, parafraseando completamente y agregando contexto."
    )

    enlaces_instruccion = ""
    if fuentes_adicionales:
        partes = " / ".join(f"[texto natural]({f['link']})" for f in fuentes_adicionales)
        enlaces_instruccion = (
            f"- Incluí también un enlace hacia cada fuente adicional, en el punto del "
            f"texto donde tenga sentido mencionarla, con este formato: {partes}"
        )

    fuentes_finales_str = item['source']
    if fuentes_adicionales:
        fuentes_finales_str += " y " + ", ".join(f['source'] for f in fuentes_adicionales)

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
{instruccion_fuentes}

===========================================
PASO 1: DEFINI EL FOCUS KEYWORD (REGLAS SEMANTICAS ESTRICTAS)
===========================================
El focus keyword NO es una etiqueta ni un hashtag: tiene que ser una frase
que un lector diria en una oracion normal en español. Muchas veces la noticia
tiene un producto con nombre propio + marca (ej: "GPT-Live" de "OpenAI",
"Moto Tag 2" de "Motorola"). En esos casos, JAMAS encadenes los dos nombres
propios uno al lado del otro sin conector, porque eso no es una frase real
y despues no se puede insertar en el texto sin que quede forzado.

REGLA CRITICA #1 - EL KEYWORD NO PUEDE CONTENER YA EL SUJETO DE LA ORACION:
Si el keyword incluye como sujeto la misma entidad que vas a usar de sujeto
en la oracion, vas a generar una redundancia (sujeto repetido). Por ejemplo,
si el keyword es "demanda de Apple a OpenAI por secretos comerciales" y
armas la oracion "Apple ha iniciado una demanda de Apple a OpenAI...", eso
es un ERROR GRAVE: el sujeto "Apple" aparece dos veces.

REGLA CRITICA #2 - EL KEYWORD NO PUEDE CONTENER YA LA PALABRA QUE VAS A
USAR COMO SUSTANTIVO PRINCIPAL DE UN TITULO O FRASE ENVOLVENTE:
Muchos keywords empiezan con un sustantivo generico (herramienta, funcion,
modelo, chip, app, actualizacion, servicio). Si armas un titulo o una
oracion que envuelve al keyword con una plantilla generica que repite ese
MISMO sustantivo generico, se genera una redundancia de palabra (no solo
de sujeto). Por ejemplo, con el keyword "herramienta T3MP3ST para pruebas
de penetracion con Claude Code":
- MAL: "T3MP3ST: la herramienta que facilita la herramienta T3MP3ST para..."
  (la palabra "herramienta" aparece dos veces, una en la plantilla del titulo
  y otra ya incluida dentro del keyword)
- BIEN: "T3MP3ST: la herramienta T3MP3ST para pruebas de penetracion con
  Claude Code" (el keyword se inserta una sola vez, sin envolverlo en una
  frase que repita su propio sustantivo)
- TAMBIEN BIEN: "Asi funciona T3MP3ST, la herramienta T3MP3ST para pruebas
  de penetracion con Claude Code" (la frase envolvente usa un verbo/adverbio
  distinto, no repite "herramienta" antes del keyword)

REGLA GENERAL DE ORO: antes de escribir cualquier titulo, H1, o primera
oracion del cuerpo, identifica la PRIMERA PALABRA SUSTANTIVA del keyword
(ej: "herramienta", "demanda", "modelo", "chip"). Esa palabra NO puede
volver a aparecer en la frase envolvente que rodea al keyword, salvo que el
keyword se inserte una unica vez sin ningun envoltorio adicional.

EJEMPLOS DE KEYWORDS INCORRECTOS COMO FRASE (rechazar siempre este patron):
- "GPT-Live OpenAI"        -> mal: dos nombres propios pegados, no es una frase
- "Apple Broadcom Chips"   -> mal: tres sustantivos en ingles sin conector
- "Moto Tag 2 Motorola"    -> mal: producto + marca sin relacion gramatical
- "Apple demanda a OpenAI" -> mal: ya es una oracion con sujeto y verbo propios

EJEMPLOS DE KEYWORDS CORRECTOS:
- "modo de voz de ChatGPT"                          (sustantivo + complementos)
- "chips de Apple con Broadcom"                     (sustantivo + complementos)
- "rastreador Moto Tag 2"                           (sustantivo + nombre propio)
- "demanda por secretos comerciales entre Apple y OpenAI"  (evento como sustantivo)
- "herramienta T3MP3ST para pruebas de penetracion con Claude Code" (correcto
  como keyword, PERO requiere cuidado especial al insertarlo, ver Regla Critica #2)

CHECKLIST antes de definir el keyword final (las 4 deben dar SI):
1. ¿Se puede leer el keyword dentro de una oracion completa sin sonar
   una lista de nombres propios pegados?
2. ¿Tiene al menos una palabra funcional en español (de, con, para, en, por, entre)?
3. ¿Es asi como lo diria un periodista en voz alta?
4. ¿El keyword es un SUSTANTIVO/EVENTO (no una oracion con sujeto+verbo propios),
   de forma que se pueda insertar como objeto/complemento sin repetir el sujeto?

===========================================
PASO 2: GENERA TODOS ESTOS CAMPOS (en este orden exacto)
===========================================

## FOCUS_KEYWORD
[el keyword elegido, validado con el checklist de arriba. ESTE STRING EXACTO,
caracter por caracter, es el que vas a repetir en SEO_TITLE, H1, y en el primer
parrafo del ARTICULO. No lo conjugues, no le cambies el orden de las palabras,
no le agregues ni saques articulos. Es un string fijo que se copia y pega igual
en cada instancia obligatoria.]

## SEO_TITLE
Titulo de 50-60 caracteres. El focus keyword (string identico al de arriba)
debe aparecer lo mas cerca posible del inicio del titulo.
ANTES DE ESCRIBIRLO: aplica la Regla Critica #2. Identifica la primera palabra
sustantiva del keyword y asegurate de que la plantilla del titulo NO la repita
antes de insertar el keyword. Si el keyword ya es autosuficiente como titulo,
NO le agregues una frase envolvente generica delante — insertalo directo o
antecedido por algo especifico de la noticia (un dato, una accion, un "asi").

## SLUG
version-corta-en-minusculas-con-guiones-del-focus-keyword
(5-6 palabras maximo). Si el keyword tiene conectores (de, con, para, entre),
el slug tambien debe conservarlos como guion (ej: entre-apple-y-openai).

## META_DESCRIPTION
Entre 150 y 160 caracteres. Debe incluir el focus keyword (string identico).
NO usar asteriscos ni markdown de ningun tipo dentro de este campo.

## H1
El titulo visible del articulo. Debe incluir el focus keyword (string identico).
Aplica la MISMA Regla Critica #2 que en el SEO_TITLE: no repitas la primera
palabra sustantiva del keyword en la frase que lo envuelve.

## ARTICULO
El cuerpo de la nota en Markdown (600-900 palabras):

1. PRIMERA MENCION DEL KEYWORD (la mas importante):
   - Antes de escribir la oracion, preguntate DOS cosas:
     a) "¿el keyword ya trae su propio sujeto y verbo?" (Regla Critica #1)
     b) "¿el keyword ya trae su propio sustantivo principal que mi frase
        envolvente podria repetir?" (Regla Critica #2)
   - Si (a) es cierto, usa un sujeto distinto para tu oracion, o reformula
     para que el keyword entre como complemento/objeto.
   - Si (b) es cierto, no repitas esa palabra sustantiva en el texto que
     rodea directamente al keyword.
   - Ejemplo de integracion CORRECTA con keyword "demanda por secretos
     comerciales entre Apple y OpenAI":
     "La tensión entre gigantes tecnológicos escaló esta semana con una
     [demanda por secretos comerciales entre Apple y OpenAI], presentada
     ante un tribunal de California."
   - Ejemplo de integracion INCORRECTA (sujeto duplicado):
     "Apple ha iniciado una demanda de Apple a OpenAI por secretos..."
   - Ejemplo de integracion INCORRECTA (sustantivo duplicado):
     "Existe una nueva herramienta que facilita la [herramienta T3MP3ST
     para pruebas de penetracion con Claude Code]"
   - El keyword debe aparecer como STRING EXACTO (mismas palabras, mismo
     orden) dentro de una oracion que se lea 100% natural al leerla en voz alta.
   - Releé la oracion completa antes de continuar: si suena repetitiva,
     redundante, o forzada, reescribila desde cero cambiando el sujeto o
     la estructura de la oracion, NUNCA cambiando el keyword.

2. SUBTITULOS (H2) — EL KEYWORD DEBE ESTAR PRESENTE:
   - Dividi el cuerpo en al menos 3-4 subtitulos H2 (##).
   - AL MENOS UNO de esos subtitulos (idealmente el primero o el segundo)
     debe contener el focus keyword completo, o una VARIACION cercana del
     mismo (ej: cambiando el orden de las palabras, usando plural/singular,
     o reemplazando una preposicion por otra equivalente), siempre y cuando
     el subtitulo se siga leyendo natural, como un titulo real de seccion.
   - Ejemplo con keyword "herramienta T3MP3ST para pruebas de penetracion
     con Claude Code":
     -> H2 correcto: "## Como funciona la herramienta T3MP3ST para pruebas
        de penetracion"
     -> H2 correcto (variacion): "## T3MP3ST y las pruebas de penetracion
        con Claude Code"
     -> H2 incorrecto: "## Caracteristicas principales" (generico, no
        menciona el keyword ni ninguna variacion suya)
   - Los demas H2 (los que no llevan el keyword) pueden ser mas libres y
     tematicos (ej: antecedentes, riesgos, contexto de la industria), pero
     igual deben estar relacionados directamente con el tema del articulo.
   - NUNCA repitas el keyword completo en mas de un H2 (para no sonar
     repetitivo); si necesitas reforzarlo en otro subtitulo, usa una
     variacion distinta a la ya usada, no el mismo string dos veces.

3. ESTRUCTURA GENERAL:
   - Parrafos cortos: maximo 3-4 lineas cada uno.

4. CONTENIDO:
   - NO copies frases textuales de la fuente; parafrasea completamente.
   - Evita frases genericas de relleno tipicas de IA.
   - Voz activa, tono profesional pero cercano (español).
   - No menciones en el cuerpo del articulo el nombre de otros medios/fuentes.

5. ENLACES:
   - El PRIMER enlace externo va exactamente sobre la mención del focus
     keyword en el primer párrafo (el mismo lugar del punto 1), asi:
     [{{el string exacto del keyword}}]({item['link']})
   {enlaces_instruccion}

===========================================
VALIDACION FINAL OBLIGATORIA (haceła antes de responder)
===========================================
Antes de entregar la respuesta, verificá vos mismo, CAMPO POR CAMPO:
1. ¿El FOCUS_KEYWORD es idéntico, carácter por carácter, en SEO_TITLE, H1,
   y en la primera mención dentro del ARTICULO? Si hay una sola diferencia
   (singular/plural, orden de palabras, articulo agregado/sacado), corregilo.
2. En el SEO_TITLE: ¿la primera palabra sustantiva del keyword se repite en
   la frase que lo envuelve? Si es así, reescribí el título completo.
3. En el H1: mismo chequeo que el punto 2.
4. En el ARTICULO: ¿la oración donde aparece el keyword por primera vez
   repite el mismo sujeto o el mismo sustantivo principal dos veces? Si es
   así, reescribí la oración completa.
5. ¿Al menos uno de los subtitulos H2 contiene el keyword completo o una
   variacion cercana del mismo? Contá los H2 uno por uno y confirmá que no
   sean todos genéricos sin relación textual con el keyword.
6. ¿Cada una de estas oraciones/títulos suena como la diría un periodista
   al leerla en voz alta, sin sonar forzada, robótica, o redundante?

===========================================
FORMATO DE SALIDA
===========================================
Devolveme EXCLUSIVAMENTE los campos de arriba (FOCUS_KEYWORD, SEO_TITLE, SLUG,
META_DESCRIPTION, H1, ARTICULO, ALT_TEXT) con esos encabezados exactos en
Markdown. No agregues explicaciones fuera de esa estructura.
Al final del ARTICULO, agrega: "Fuente: {fuentes_finales_str}"
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

    # 1. Triangulacion, paso 1: busqueda activa en la web (prioridad)
        print("🌐 Buscando fuentes relacionadas en la web...")
        fuentes_adicionales = buscar_fuentes_relacionadas(item)

        if not fuentes_adicionales:
        # 2. Si la web no devolvio nada util, caer al pool de RSS como respaldo
            print("🔗 Sin resultados en la web, probando en el pool de RSS...")
            fuente_rss = encontrar_fuente_secundaria(item, todos_los_candidatos)
            if fuente_rss:
                fuentes_adicionales = [fuente_rss]

    # 3. Extraer el texto completo de cada fuente adicional encontrada
        for f in fuentes_adicionales:
            print(f"📥 Extrayendo fuente adicional: {f['source']}...")
            full_sec = extract_full_article(f["link"])
            f["texto_completo"] = (
                full_sec["text"] if full_sec and full_sec.get("text")
                else f.get("summary", "")
            )

    # 4. Extraer articulo principal
        print("📥 Extrayendo fuente principal...")
        full_article = extract_full_article(item['link'])
        contenido_principal = (
            full_article['text'] if full_article and full_article.get('text')
            else item['summary']
        )

    # 5. Buscar imagen relevante con Google Custom Search
        print("🖼️ Buscando imagen relevante...")
        fallback_image = full_article.get('top_image') if full_article else None
        imagen_url = buscar_imagen_google(item['title'], fallback_url=fallback_image)

    # 6. Redactar con Gemini (con todo el contexto triangulado)
        try:
            prompt = build_prompt(
                item,
                contenido_principal,
                fuentes_adicionales=fuentes_adicionales,
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
