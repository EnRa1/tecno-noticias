#!/usr/bin/env python3
"""
Pipeline de automatizacion para tecno.ar (Hybrid 4.4 - Categorias de menu + ranking en 2.5-flash)
==================================================================================
1. Filtro rapido por reglas (gratis) -> reduce de cientos a ~20-30
2. Filtro contextual con Gemini (1 sola llamada, con retry, modelo 2.5-flash) -> elige las mejores
3. Triangulacion de fuentes: grounding de Gemini (modelo fijo 2.5-flash) como
   metodo principal; cascada de Custom Search con filtro de relevancia como respaldo
4. Extraccion del articulo completo desde todas las fuentes (trafilatura + readability)
5. Busqueda de imagen relevante via Google Custom Search API
6. Redaccion con Gemini + VALIDACION PROGRAMATICA (no depende de autoevaluacion
   del modelo): detecta repeticion de palabras en titulo/H1 en Python puro y
   pide correccion especifica con reintentos si encuentra problemas
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
TITULOS_RECIENTES_FILE = BASE_DIR / "titulos_recientes.json"

MAX_TITULOS_RECIENTES = 15
MAX_REINTENTOS_TITULO = 2

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

GEMINI_MODEL = "gemini-3-flash-preview"
GEMINI_URL = (
    f"https://generativelanguage.googleapis.com/v1beta/models/"
    f"{GEMINI_MODEL}:generateContent?key={GEMINI_API_KEY}"
)

GEMINI_GROUNDING_MODEL = "gemini-2.5-flash"
GEMINI_GROUNDING_URL = (
    f"https://generativelanguage.googleapis.com/v1beta/models/"
    f"{GEMINI_GROUNDING_MODEL}:generateContent?key={GEMINI_API_KEY}"
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
SEARCH_MAX_RETRIES = 2
SEARCH_BASE_BACKOFF = 3

SIMILITUD_MINIMA = 0.18
UMBRAL_RELEVANCIA_CASCADA = 0.12

SITIOS_REFERENCIA_BUSQUEDA = [
    "techcrunch.com",
    "theverge.com",
    "wired.com",
    "arstechnica.com",
    "engadget.com",
    "gizmodo.com",
    "xataka.com",
    "genbeta.com",
    "hipertextual.com",
    "infobae.com",
    "clarin.com",
    "ambito.com",
    "blog.google",
    "news.microsoft.com",
    "openai.com",
    "reuters.com"
    "anthropic.com",
    "thehackernews.com",
    "bleepingcomputer.com",
    "krebsonsecurity.com",
    "9to5mac.com",
    "9to5google.com",
    "androidauthority.com",
]

DOMINIOS_EXCLUIDOS = {
    "twitter.com", "x.com", "facebook.com", "reddit.com",
    "youtube.com", "tecno.ar", "news.google.com",
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
    "videojuego", "videojuegos", "gaming", "nintendo", "playstation", "xbox",
    "auto electrico", "vehiculo electrico", "realidad aumentada",
    "realidad virtual", "criptomoneda", "bitcoin", "blockchain",
    "smart home", "domotica", "nasa", "espacio",
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

def load_titulos_recientes():
    if TITULOS_RECIENTES_FILE.exists():
        try:
            return json.loads(TITULOS_RECIENTES_FILE.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return []
    return []

def guardar_titulo_reciente(seo_title):
    if not seo_title:
        return
    titulos = load_titulos_recientes()
    titulos.append(seo_title)
    titulos = titulos[-MAX_TITULOS_RECIENTES:]
    TITULOS_RECIENTES_FILE.write_text(
        json.dumps(titulos, ensure_ascii=False, indent=2), encoding="utf-8"
    )

def extraer_campo(article_md, nombre_campo):
    """Extrae el contenido de un campo '## NOMBRE_CAMPO' del markdown generado."""
    match = re.search(
        rf"## {nombre_campo}\s*\n(.+?)(?:\n##|\Z)", article_md, re.DOTALL
    )
    return match.group(1).strip() if match else None

def extraer_seo_title(article_md):
    return extraer_campo(article_md, "SEO_TITLE")

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
    palabras = re.findall(r"[a-záéíóúñ0-9]+", texto.lower())
    return {p for p in palabras if p not in STOPWORDS_ES and len(p) > 2}

def similitud_texto(a, b):
    set_a, set_b = tokenizar(a), tokenizar(b)
    if not set_a or not set_b:
        return 0.0
    interseccion = len(set_a & set_b)
    union = len(set_a | set_b)
    return interseccion / union if union else 0.0

def _extraer_dominio(url):
    match = re.search(r"https?://(?:www\.)?([^/]+)", url)
    return match.group(1).lower() if match else ""

def _resolver_url_real(url_redirect, timeout=10):
    """
    Las URLs que devuelve el grounding de Gemini a veces son links de
    redireccion de Google (vertexaisearch.cloud.google.com/grounding-api-redirect/...).
    Sigue el redirect HTTP y devuelve la URL final real. Si no se puede
    resolver, devuelve None para que la fuente se descarte.
    """
    if "vertexaisearch.cloud.google.com" not in url_redirect:
        return url_redirect

    for metodo in ("head", "get"):
        try:
            if metodo == "head":
                resp = requests.head(
                    url_redirect, timeout=timeout, allow_redirects=True,
                    headers=REQUEST_HEADERS,
                )
            else:
                resp = requests.get(
                    url_redirect, timeout=timeout, allow_redirects=True,
                    headers=REQUEST_HEADERS, stream=True,
                )
                resp.close()

            if resp.status_code >= 400:
                print(f"    ⚠️ Redirect respondió {resp.status_code} con {metodo.upper()}, "
                      f"probando otro método...")
                continue

            if resp.url and "vertexaisearch.cloud.google.com" not in resp.url:
                return resp.url

        except requests.exceptions.RequestException as e:
            print(f"    ⚠️ Error resolviendo redirect con {metodo.upper()}: {e}")
            continue

    print("    ⚠️ No se pudo resolver el link de redirección "
          "(probablemente expiró). Se descarta esta fuente.")
    return url_redirect

def _google_search_con_reintentos(params, contexto=""):
    for intento in range(SEARCH_MAX_RETRIES + 1):
        try:
            resp = requests.get(GOOGLE_SEARCH_URL, params=params, timeout=15)
            if resp.status_code == 200:
                return resp.json()
            elif resp.status_code >= 500 and intento < SEARCH_MAX_RETRIES:
                wait = SEARCH_BASE_BACKOFF * (intento + 1)
                print(f"    ⚠️ Error {resp.status_code} en {contexto}, "
                      f"reintentando en {wait}s...")
                time.sleep(wait)
                continue
            else:
                print(f"    ⚠️ Error Google Search ({contexto}): "
                      f"{resp.status_code} — {resp.text[:200]}")
                return None
        except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as e:
            if intento < SEARCH_MAX_RETRIES:
                wait = SEARCH_BASE_BACKOFF * (intento + 1)
                print(f"    ⚠️ Error de red en {contexto} ({type(e).__name__}), "
                      f"reintentando en {wait}s...")
                time.sleep(wait)
                continue
            print(f"    ⚠️ Excepción de red agotó reintentos en {contexto}: {e}")
            return None
    return None

# ----------------------------------------------------------------------
# VALIDACION PROGRAMATICA DE TITULOS (determinista, no depende de la IA)
# ----------------------------------------------------------------------

def detectar_repeticion_titulo(titulo, focus_keyword):
    """
    Detecta programaticamente si un titulo (SEO_TITLE o H1) repite una
    palabra significativa del focus_keyword FUERA de la insercion del
    keyword en si.

    Devuelve una lista de palabras repetidas (vacia si no hay problema).
    """
    titulo_lower = titulo.lower()
    keyword_lower = focus_keyword.lower()

    idx = titulo_lower.find(keyword_lower)
    if idx == -1:
        return []

    resto_titulo = titulo_lower[:idx] + titulo_lower[idx + len(keyword_lower):]

    palabras_keyword = re.findall(r"[a-záéíóúñ]+", keyword_lower)
    palabras_resto = set(re.findall(r"[a-záéíóúñ]+", resto_titulo))

    repetidas = []
    for palabra in palabras_keyword:
        if palabra in STOPWORDS_ES or len(palabra) <= 3:
            continue
        if palabra in palabras_resto:
            repetidas.append(palabra)

    return repetidas

def validar_campos_generados(article_md):
    """
    Valida programaticamente el articulo generado por Gemini.
    """
    problemas = []

    focus_keyword = extraer_campo(article_md, "FOCUS_KEYWORD")
    seo_title = extraer_campo(article_md, "SEO_TITLE")
    h1 = extraer_campo(article_md, "H1")

    if not focus_keyword or not seo_title or not h1:
        problemas.append("No se pudo extraer FOCUS_KEYWORD, SEO_TITLE o H1 del markdown.")
        return problemas

    if focus_keyword.lower() not in seo_title.lower():
        problemas.append(
            f'El FOCUS_KEYWORD ("{focus_keyword}") no aparece de forma '
            f'identica dentro del SEO_TITLE ("{seo_title}").'
        )

    if focus_keyword.lower() not in h1.lower():
        problemas.append(
            f'El FOCUS_KEYWORD ("{focus_keyword}") no aparece de forma '
            f'identica dentro del H1 ("{h1}").'
        )

    rep_title = detectar_repeticion_titulo(seo_title, focus_keyword)
    if rep_title:
        problemas.append(
            f'El SEO_TITLE ("{seo_title}") repite la(s) palabra(s) '
            f'{rep_title} del keyword fuera de su unica insercion valida.'
        )

    rep_h1 = detectar_repeticion_titulo(h1, focus_keyword)
    if rep_h1:
        problemas.append(
            f'El H1 ("{h1}") repite la(s) palabra(s) {rep_h1} del keyword '
            f'fuera de su unica insercion valida.'
        )

    return problemas

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
# Las categorias de abajo estan alineadas al menu real de tecno.ar:
# Smartphones / Hardware / Gaming / Empresas / Ciencia / Vehiculos / Hogar /
# Mas Tecno (Cripto, Redes, Smartwatch, Gadgets) / RA / IA

LAUNCH_KEYWORDS = [
    "lanza", "lanzamiento", "presenta", "presento", "anuncia", "anuncio",
    "debuta", "revela", "sale a la venta", "disponible desde", "estrena",
    "launches", "unveils", "announces", "introduces", "debuts", "reveals",
]

HARDWARE_KEYWORDS = [
    "smartphone", "celular", "iphone", "procesador", "chip", "cpu", "gpu",
    "tarjeta grafica", "periferico", "teclado", "mouse", "auriculares",
    "smart tv", "televisor",
    "tablet", "notebook", "laptop", "smartwatch", "wearable", "consola",
    "placa de video", "motherboard", "placa madre", "bateria", "grafeno",
]

AI_KEYWORDS = [
    "inteligencia artificial", "modelo de ia", "llm", "chatgpt", "gemini",
    "claude", "openai", "anthropic", "copilot", "gpt-", "modelo de lenguaje",
    "machine learning", "deep learning", "red neuronal"
]

GAMING_KEYWORDS = [
    "nintendo", "playstation", "ps5", "ps6", "xbox", "videojuego", "videojuegos",
    "gaming", "steam", "epic games", "esports", "e-sports",
    "consola de videojuegos", "switch 2", "game pass",
]

VEHICULOS_KEYWORDS = [
    "auto electrico", "vehiculo electrico", "ev", "coche electrico",
    "auto autonomo", "vehiculo autonomo", "tesla", "moto electrica",
    "carga rapida", "autopilot", "conduccion autonoma",
]

RA_KEYWORDS = [
    "realidad aumentada", "realidad virtual", "metaverso", "gafas de ra",
    "gafas de rv", "vision pro", "quest 3", "lentes inteligentes", "xr",
]

CRIPTO_KEYWORDS = [
    "criptomoneda", "criptomonedas", "bitcoin", "ethereum", "blockchain",
    "cripto", "stablecoin", "web3", "nft",
]

CIENCIA_KEYWORDS = [
    "nasa", "espacio", "cientificos", "estudio cientifico",
    "investigacion cientifica", "fisica cuantica", "astronomia", "cohete",
    "spacex", "descubrimiento cientifico", "mision espacial",
]

HOGAR_KEYWORDS = [
    "smart home", "hogar inteligente", "domotica", "electrodomestico",
    "electrodomesticos", "aspiradora robot", "robot aspirador",
    "asistente de voz", "amazon echo", "google home",
]

EMPRESAS_KEYWORDS = [
    "adquiere", "adquisicion", "fusion", "ronda de inversion", "invierte en",
    "compra a", "acuerdo comercial", "alianza estrategica",
    "ronda de financiamiento",
]

ARGENTINA_KEYWORDS = [
    "argentina", "argentino", "buenos aires",
    "mercado libre", "mercadolibre", "globant", "uala", "ualá",
    "satellogic", "auth0", "despegar", "tiendanube",
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

    if any(kw in text for kw in GAMING_KEYWORDS):
        score += 3
        if is_launch:
            score += 2
        categorias.append("gaming")

    if any(kw in text for kw in VEHICULOS_KEYWORDS):
        score += 3
        if is_launch:
            score += 2
        categorias.append("vehiculos")

    if any(kw in text for kw in RA_KEYWORDS):
        score += 2
        if is_launch:
            score += 2
        categorias.append("ra")

    if any(kw in text for kw in CRIPTO_KEYWORDS):
        score += 2
        categorias.append("cripto")

    if any(kw in text for kw in CIENCIA_KEYWORDS):
        score += 2
        categorias.append("ciencia")

    if any(kw in text for kw in HOGAR_KEYWORDS):
        score += 2
        categorias.append("hogar")

    if any(kw in text for kw in EMPRESAS_KEYWORDS):
        score += 2
        categorias.append("empresas")

    if is_launch and any(kw in text for kw in ARGENTINA_KEYWORDS):
        score += 1
        categorias.append("argentina")

    if any(kw in text for kw in PENALTY_KEYWORDS):
        score -= 2

    return max(0, min(10, score)), (categorias[0] if categorias else "general")

# ----------------------------------------------------------------------
# TRIANGULACION - METODO PRINCIPAL: GROUNDING CON GOOGLE SEARCH DE GEMINI
# ----------------------------------------------------------------------

def call_gemini_grounding_api(payload, context="grounding", retries=GEMINI_MAX_RETRIES):
    if not GEMINI_API_KEY:
        raise RuntimeError("Falta la variable de entorno GEMINI_API_KEY")

    last_error = None

    for attempt in range(retries):
        try:
            resp = requests.post(GEMINI_GROUNDING_URL, json=payload, timeout=60)

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

def buscar_fuentes_con_grounding(item, max_fuentes=MAX_FUENTES_ADICIONALES):
    if not GEMINI_API_KEY:
        print("    ⚠️ Sin GEMINI_API_KEY, no se puede usar grounding.")
        return None

    prompt = f"""
Busca en la web noticias RECIENTES (ultimas 24-48 horas) que cubran el mismo
hecho o tema que esta noticia:

Titulo: {item['title']}
Resumen: {item['summary'][:300]}

Encuentra hasta {max_fuentes} articulos de medios DISTINTOS a "{item['source']}"
que hablen especificamente de este mismo evento (no articulos genericos sobre
el tema general, sino cobertura del mismo hecho puntual).

Devolveme SOLO un JSON con este formato exacto, sin texto adicional:
{{"fuentes": [{{"title": "titulo del articulo", "source": "nombre del medio",
"link": "URL completa del articulo"}}]}}

Si no encontras ninguna fuente adicional relevante, devolveme {{"fuentes": []}}.
"""

    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "tools": [{"google_search": {}}],
        "generationConfig": {"temperature": 0.1},
    }

    try:
        print(f"    🔎 Buscando con grounding de Gemini ({GEMINI_GROUNDING_MODEL}): "
              f"'{item['title'][:60]}...'")
        data = call_gemini_grounding_api(payload, context="grounding-triangulacion")
        raw_text = data["candidates"][0]["content"]["parts"][0]["text"]

        raw_text = re.sub(r"^```json\s*|\s*```$", "", raw_text.strip())
        result = json.loads(raw_text)
        fuentes_crudas = result.get("fuentes", [])

        fuentes = []
        dominio_origen = _extraer_dominio(item["link"])
        dominios_vistos = {dominio_origen}

        for f in fuentes_crudas:
            link_original = f.get("link", "")
            if not link_original:
                continue

            link = _resolver_url_real(link_original)
            if link is None:
                print(f"    ⏭️ Fuente descartada por link de redirección no resuelto: "
                      f"{f.get('source', 'desconocido')}")
                continue

            dominio = _extraer_dominio(link)

            if not dominio or dominio in dominios_vistos:
                continue
            if any(excl in dominio for excl in DOMINIOS_EXCLUIDOS):
                continue

            fuentes.append({
                "hash": item_hash({"link": link}),
                "title": f.get("title", "Sin titulo"),
                "link": link,
                "summary": "",
                "source": f.get("source", dominio),
            })
            dominios_vistos.add(dominio)

            if len(fuentes) >= max_fuentes:
                break

        if fuentes:
            print(f"    ✅ Grounding encontró {len(fuentes)} fuente(s): "
                  + ", ".join(f["source"] for f in fuentes))
        else:
            print("    ℹ️ Grounding no encontró fuentes adicionales relevantes.")

        return fuentes

    except Exception as e:
        print(f"    ⚠️ Excepción usando grounding ({e}), se probará la cascada de respaldo.")
        return None

# ----------------------------------------------------------------------
# TRIANGULACION - RESPALDO: CASCADA DE CUSTOM SEARCH + POOL DE RSS
# ----------------------------------------------------------------------

def encontrar_fuente_secundaria(item_principal, todos_los_candidatos):
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
        print(f"    🔗 Match en pool de RSS ({mejor_similitud:.0%} similitud): "
              f"{mejor_candidato['source']} — {mejor_candidato['title'][:60]}")
        return mejor_candidato

    return None

def _ejecutar_busqueda_texto(query, date_restrict, dominio_origen, max_fuentes, titulo_original=""):
    params = {
        "key": GOOGLE_SEARCH_API_KEY,
        "cx": GOOGLE_SEARCH_ENGINE_ID,
        "q": query,
        "num": 10,
        "safe": "active",
        "dateRestrict": date_restrict,
    }

    data = _google_search_con_reintentos(params, contexto=f"texto ({date_restrict})")
    if not data:
        return []

    items_resultado = data.get("items", [])
    if not items_resultado:
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

        titulo_resultado = r.get("title", "") + " " + r.get("snippet", "")
        if titulo_original:
            sim = similitud_texto(titulo_original, titulo_resultado)
            if sim < UMBRAL_RELEVANCIA_CASCADA:
                print(f"    ⏭️ Descartado por baja relevancia ({sim:.0%}): "
                      f"{dominio} — {r.get('title', '')[:50]}")
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

    return fuentes

def buscar_fuentes_cascada_respaldo(item, todos_los_candidatos, max_fuentes=MAX_FUENTES_ADICIONALES):
    if not GOOGLE_SEARCH_API_KEY or not GOOGLE_SEARCH_ENGINE_ID:
        print("    ⚠️ Sin credenciales de Google Search, saltando cascada de respaldo.")
        fuente_rss = encontrar_fuente_secundaria(item, todos_los_candidatos)
        return [fuente_rss] if fuente_rss else []

    query_base = " ".join(item["title"].split()[:10])
    dominio_origen = _extraer_dominio(item["link"])
    filtro_sitios = " OR ".join(f"site:{s}" for s in SITIOS_REFERENCIA_BUSQUEDA)
    titulo_original = item["title"]

    print("    [Respaldo 1] Sitios de referencia, últimas 24hs...")
    query_n1 = f"({filtro_sitios}) {query_base}"
    fuentes = _ejecutar_busqueda_texto(query_n1, "d1", dominio_origen, max_fuentes, titulo_original)
    if fuentes:
        print(f"    ✅ Respaldo 1 exitoso: {', '.join(f['source'] for f in fuentes)}")
        return fuentes

    print("    [Respaldo 2] Sitios de referencia, últimas 72hs...")
    fuentes = _ejecutar_busqueda_texto(query_n1, "d3", dominio_origen, max_fuentes, titulo_original)
    if fuentes:
        print(f"    ✅ Respaldo 2 exitoso: {', '.join(f['source'] for f in fuentes)}")
        return fuentes

    print("    [Respaldo 3] Web abierta (sin restricción de sitio), últimas 48hs...")
    fuentes = _ejecutar_busqueda_texto(query_base, "d2", dominio_origen, max_fuentes, titulo_original)
    if fuentes:
        print(f"    ✅ Respaldo 3 exitoso: {', '.join(f['source'] for f in fuentes)}")
        return fuentes

    print("    [Respaldo 4] Pool de RSS local...")
    fuente_rss = encontrar_fuente_secundaria(item, todos_los_candidatos)
    if fuente_rss:
        print(f"    ✅ Respaldo 4 exitoso: {fuente_rss['source']}")
        return [fuente_rss]

    print("    ℹ️ Cascada de respaldo completa sin resultados.")
    return []

# ----------------------------------------------------------------------
# BUSQUEDA DE IMAGEN VIA GOOGLE CUSTOM SEARCH
# ----------------------------------------------------------------------

def buscar_imagen_google(query, fallback_url=None):
    if not GOOGLE_SEARCH_API_KEY or not GOOGLE_SEARCH_ENGINE_ID:
        print("⚠️ Sin credenciales de Google Search, usando imagen de la fuente original.")
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

    data = _google_search_con_reintentos(params, contexto="imagen")
    if data:
        items = data.get("items", [])
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
# HELPER COMPARTIDO: LLAMADA A GEMINI CON RETRY/BACKOFF (redaccion/ranking)
# ----------------------------------------------------------------------

def call_gemini_api(payload, context="gemini", retries=GEMINI_MAX_RETRIES, url=None):
    """
    url permite apuntar a un modelo distinto del default (GEMINI_URL).
    Se usa, por ejemplo, para que el ranking pegue a GEMINI_GROUNDING_URL
    (gemini-2.5-flash, modelo estable) en vez del modelo preview de
    redaccion, que sufre mas 503/timeouts con prompts grandes.
    """
    if not GEMINI_API_KEY:
        raise RuntimeError("Falta la variable de entorno GEMINI_API_KEY")

    url = url or GEMINI_URL
    last_error = None

    for attempt in range(retries):
        try:
            resp = requests.post(url, json=payload, timeout=60)

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

    print(f"🧠 Enviando {len(candidatos)} noticias a Gemini ({GEMINI_GROUNDING_MODEL}) "
          f"para ranking contextual...")

    lista_texto = ""
    for idx, item in enumerate(candidatos, 1):
        lista_texto += f"{idx}. Título: {item['title']}\n   Resumen: {item['summary'][:250]}\n\n"

    prompt = f"""
Eres un editor jefe de un blog de tecnología llamado tecno.ar. Tu tarea es
seleccionar las {MAX_ITEMS_PER_RUN} noticias MÁS RELEVANTES de la lista al final
de este mensaje, aplicando los criterios de abajo EN ORDEN DE PRIORIDAD. Estos
criterios reflejan las secciones reales del menú de tecno.ar (Smartphones,
Hardware, Gaming, Empresas, Ciencia, Vehículos, Hogar, Cripto, RA, IA).

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
CRITERIO 4: GAMING
===========================================
Lanzamientos oficiales de juegos, consolas, o anuncios confirmados de
Nintendo, PlayStation, Xbox o estudios grandes.

SI CALIFICA: "Nintendo confirma la fecha de lanzamiento del nuevo Zelda"
NO CALIFICA: "Los mejores juegos indie de 2026" -> listicle, no un evento puntual

===========================================
CRITERIO 5: VEHÍCULOS
===========================================
Anuncios oficiales de autos eléctricos, autónomos, o novedades confirmadas
de fabricantes de vehículos con foco tecnológico.

SI CALIFICA: "Tesla presenta una actualización de Autopilot con nuevo hardware"
NO CALIFICA: "Por qué los autos eléctricos son el futuro" -> opinión/ensayo

===========================================
CRITERIO 6: REALIDAD AUMENTADA Y VIRTUAL
===========================================
Lanzamientos concretos de dispositivos o software de RA/RV/metaverso.

SI CALIFICA: "Meta lanza una actualización de software para Quest 3"
NO CALIFICA: "El metaverso todavía no despegó, ¿por qué?" -> análisis retrospectivo

===========================================
CRITERIO 7: CRIPTOMONEDAS
===========================================
Eventos concretos y confirmados: lanzamientos de producto cripto de una
empresa grande, regulación ya aprobada, movimientos de mercado relevantes
y verificados (no especulación de precio).

SI CALIFICA: "PayPal habilita pagos en Bitcoin para comercios en Argentina"
NO CALIFICA: "¿Subirá el Bitcoin esta semana?" -> especulación de precio

===========================================
CRITERIO 8: CIENCIA
===========================================
Descubrimientos científicos verificados o misiones espaciales concretas
con tecnología de por medio.

SI CALIFICA: "SpaceX confirma la fecha del próximo lanzamiento de Starship"
NO CALIFICA: "10 curiosidades sobre el universo" -> listicle genérico

===========================================
CRITERIO 9: HOGAR INTELIGENTE
===========================================
Lanzamientos concretos de productos domóticos o electrodomésticos
inteligentes.

SI CALIFICA: "Amazon presenta un nuevo Echo con IA integrada"
NO CALIFICA: "Guía para armar tu smart home" -> contenido educativo genérico

===========================================
CRITERIO 10 (mínima prioridad): CIBERSEGURIDAD
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
        data = call_gemini_api(payload, context="ranking", url=GEMINI_GROUNDING_URL)
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
    candidatos_para_triangular = []

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
# CONSTRUCCION DEL PROMPT (titulos con gancho real + antirepeticion)
# ----------------------------------------------------------------------

def build_prompt(item, full_text_principal, fuentes_adicionales=None, imagen_url=None, titulos_recientes=None):
    text_limit = full_text_principal[:6000] if full_text_principal else item['summary']
    fuentes_adicionales = fuentes_adicionales or []
    titulos_recientes = titulos_recientes or []

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

    bloque_titulos_previos = ""
    if titulos_recientes:
        lista_titulos = "\n".join(f"- {t}" for t in titulos_recientes)
        bloque_titulos_previos = f"""
===========================================
TITULOS YA PUBLICADOS RECIENTEMENTE (PROHIBIDO REPETIR SU ESTRUCTURA)
===========================================
Estos son los ultimos titulos publicados en tecno.ar. Tu nuevo SEO_TITLE y H1
NO pueden empezar con la misma formula, palabra de apertura, o estructura
sintactica que ninguno de estos, aunque el tema de hoy sea completamente
distinto.

{lista_titulos}
"""

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
Muchos keywords empiezan con un sustantivo generico o un nombre propio
(herramienta, funcion, modelo, chip, app, Google, OpenAI). Si armas un
titulo o una oracion que envuelve al keyword repitiendo esa MISMA palabra,
se genera una redundancia. Por ejemplo, con el keyword "easter egg de
Google por Marc Cucurella":
- MAL: "Google activa el easter egg de Google por Marc Cucurella..."
  (la palabra "Google" aparece dos veces: una en la plantilla del titulo
  y otra ya incluida dentro del keyword)
- BIEN: "Un buscador secreto homenajea a Marc Cucurella en pleno Mundial"
  (el keyword se inserta como parte natural de una oracion nueva, sin
  repetir "Google" fuera de la insercion del keyword)
Otro ejemplo, con el keyword "herramienta Open Notebook de IA local":
- MAL: "Open Notebook: la herramienta Open Notebook de IA local..."
- BIEN: "Esta herramienta de IA local procesa tus datos sin salir de tu PC"

REGLA GENERAL DE ORO: antes de escribir cualquier titulo, H1, o primera
oracion del cuerpo, identifica CADA PALABRA SIGNIFICATIVA del keyword
(nombres propios, sustantivos principales). Ninguna de esas palabras puede
volver a aparecer en la frase envolvente que rodea al keyword, salvo que
el keyword se inserte una unica vez sin ningun envoltorio adicional.

EJEMPLOS DE KEYWORDS INCORRECTOS COMO FRASE (rechazar siempre este patron):
- "GPT-Live OpenAI"        -> mal: dos nombres propios pegados, no es una frase
- "Apple Broadcom Chips"   -> mal: tres sustantivos en ingles sin conector
- "Apple demanda a OpenAI" -> mal: ya es una oracion con sujeto y verbo propios

EJEMPLOS DE KEYWORDS CORRECTOS:
- "modo de voz de ChatGPT"                          (sustantivo + complementos)
- "chips de Apple con Broadcom"                     (sustantivo + complementos)
- "demanda por secretos comerciales entre Apple y OpenAI"  (evento como sustantivo)

CHECKLIST antes de definir el keyword final (las 4 deben dar SI):
1. ¿Se puede leer el keyword dentro de una oracion completa sin sonar
   una lista de nombres propios pegados?
2. ¿Tiene al menos una palabra funcional en español (de, con, para, en, por, entre)?
3. ¿Es asi como lo diria un periodista en voz alta?
4. ¿El keyword es un SUSTANTIVO/EVENTO (no una oracion con sujeto+verbo propios)?

===========================================
PASO 2: GENERA TODOS ESTOS CAMPOS (en este orden exacto)
===========================================

## FOCUS_KEYWORD
[el keyword elegido, validado con el checklist de arriba. ESTE STRING EXACTO,
caracter por caracter, es el que vas a repetir en SEO_TITLE, H1, y en el primer
parrafo del ARTICULO. No lo conjugues, no le cambies el orden de las palabras,
no le agregues ni saques articulos.]

## SEO_TITLE
===========================================
COMO ESCRIBIR UN TITULO QUE REALMENTE ATRAIGA AL LECTOR (LO MAS IMPORTANTE)
===========================================
El titulo es lo unico que decide si alguien entra a leer la nota o la ignora
en el feed. Escribilo con estos principios, en este orden de importancia:

1. EMPEZA POR EL HECHO, NO POR UNA INTRODUCCION:
   El sujeto real de la noticia o la accion concreta van PRIMERO. Nunca
   antepongas una frase de relleno antes del hecho.
   MAL: "Todo sobre el nuevo lanzamiento de Google para IA"
   BIEN: "Google lanza una IA que edita fotos con un solo comando de voz"

2. USA UN VERBO FUERTE Y ACTIVO, NUNCA UNO DEBIL O GENERICO:
   Preferi verbos de accion concreta (lanza, presenta, confirma, revela,
   bloquea, dispara, rompe, supera, gana, pierde, demanda, ataca, corrige)
   por sobre verbos vagos (tiene que ver con, esta relacionado a, habla de).

3. INCLUI UN DATO O NUMERO CONCRETO SI LA NOTICIA LO TIENE:
   Una cifra, un porcentaje, un precio, o un nombre propio ancla el titulo
   en la realidad y genera mas curiosidad que una descripcion abstracta.

4. GENERA UNA BRECHA DE CURIOSIDAD SIN CAER EN CLICKBAIT ENGAÑOSO:
   El lector tiene que sentir que falta un dato que solo consigue si entra
   a leer, pero el titulo NUNCA debe prometer algo que el articulo no cumple.

5. LARGO Y RITMO:
   Entre 50 y 60 caracteres. Frases cortas y directas. Leelo en voz alta:
   si te trabas o suena a oracion de manual, reescribilo mas corto y directo.

6. VERIFICACION OBLIGATORIA ANTES DE FIJAR EL TITULO (hace esto SIEMPRE,
   no es opcional): tomá cada palabra significativa del FOCUS_KEYWORD
   (nombres propios, sustantivos principales) y confirmá que NINGUNA vuelve
   a aparecer en el resto del titulo fuera de la insercion del keyword. Si
   encontras una repetida, DESCARTA ese titulo por completo y escribi uno
   nuevo con un angulo distinto (no intentes "arreglar" el mismo titulo
   sacando una palabra suelta, reescribilo desde cero).

7. El FOCUS KEYWORD (string identico al definido arriba) tiene que aparecer
   lo mas cerca posible del inicio del titulo, integrado naturalmente.

EJEMPLOS DE ANTES/DESPUES (referencia de calidad esperada):
- ANTES (repite "Google"): "Google activa el easter egg de Google por Marc Cucurella"
  DESPUES (sin repetir): "Un buscador secreto de Google homenajea a Marc Cucurella"
- ANTES (repite "Open Notebook"): "Open Notebook: la herramienta Open Notebook de IA local"
  DESPUES (sin repetir): "Esta herramienta de IA local procesa tus datos sin subirlos a la nube"

{bloque_titulos_previos}

## SLUG
version-corta-en-minusculas-con-guiones-del-focus-keyword
(5-6 palabras maximo). Si el keyword tiene conectores (de, con, para, entre),
el slug tambien debe conservarlos como guion.

## META_DESCRIPTION
Entre 150 y 160 caracteres. Debe incluir el focus keyword (string identico).
NO usar asteriscos ni markdown de ningun tipo dentro de este campo.

## H1
El titulo visible del articulo. Debe incluir el focus keyword (string identico).
Aplica EXACTAMENTE los mismos 7 principios de gancho y verificacion que el
SEO_TITLE (puede ser identico al SEO_TITLE o una variacion minima).

## ARTICULO
El cuerpo de la nota en Markdown (600-900 palabras):

1. PRIMERA MENCION DEL KEYWORD (la mas importante):
   - Antes de escribir la oracion, preguntate DOS cosas:
     a) "¿el keyword ya trae su propio sujeto y verbo?" (Regla Critica #1)
     b) "¿el keyword ya trae palabras significativas que mi frase envolvente
        podria repetir?" (Regla Critica #2)
   - Si (a) es cierto, usa un sujeto distinto para tu oracion.
   - Si (b) es cierto, no repitas esas palabras en el texto que rodea
     directamente al keyword.
   - El keyword debe aparecer como STRING EXACTO dentro de una oracion que
     se lea 100% natural al leerla en voz alta.

2. SUBTITULOS (H2) — EL KEYWORD DEBE ESTAR PRESENTE:
   - Dividi el cuerpo en al menos 3-4 subtitulos H2 (##).
   - AL MENOS UNO de esos subtitulos debe contener el focus keyword completo,
     o una VARIACION cercana del mismo.
   - NUNCA repitas el keyword completo en mas de un H2.

3. ESTRUCTURA GENERAL:
   - Parrafos cortos: maximo 3-4 lineas cada uno.

4. CONTENIDO:
   - NO copies frases textuales de la fuente; parafrasea completamente.
   - Voz activa, tono profesional pero cercano (español).
   - No menciones en el cuerpo del articulo el nombre de otros medios/fuentes.

5. ENLACES:
   - El PRIMER enlace externo va exactamente sobre la mención del focus
     keyword en los párrafos finales, asi:
     [{{el string exacto del keyword}}]({item['link']})
   {enlaces_instruccion}

===========================================
FORMATO DE SALIDA
===========================================
Devolveme EXCLUSIVAMENTE los campos de arriba (FOCUS_KEYWORD, SEO_TITLE, SLUG,
META_DESCRIPTION, H1, ARTICULO, ALT_TEXT) con esos encabezados exactos en
Markdown. No agregues explicaciones fuera de esa estructura.
Al final del ARTICULO, agrega: "Fuente: {fuentes_finales_str}"
"""

# ----------------------------------------------------------------------
# REDACCION CON GEMINI + VALIDACION PROGRAMATICA CON REINTENTOS
# ----------------------------------------------------------------------

def call_gemini(prompt):
    payload = {"contents": [{"parts": [{"text": prompt}]}]}
    data = call_gemini_api(payload, context="redaccion")
    try:
        return data["candidates"][0]["content"]["parts"][0]["text"]
    except (KeyError, IndexError):
        raise RuntimeError(f"Respuesta inesperada de Gemini: {data}")

def redactar_con_validacion(prompt_base, item):
    """
    Llama a Gemini para redactar el articulo, valida programaticamente el
    resultado con validar_campos_generados (chequeo determinista en Python,
    no depende de que el modelo se autoevalue), y si encuentra problemas de
    repeticion en titulo/H1, le pide a Gemini que corrija especificamente
    ese error, hasta MAX_REINTENTOS_TITULO veces.
    """
    prompt_actual = prompt_base
    article = None

    for intento in range(MAX_REINTENTOS_TITULO + 1):
        article = call_gemini(prompt_actual)
        problemas = validar_campos_generados(article)

        if not problemas:
            if intento > 0:
                print(f"    ✅ Corregido tras {intento} reintento(s).")
            return article

        print(f"    ⚠️ Intento {intento + 1}: se detectaron {len(problemas)} problema(s):")
        for p in problemas:
            print(f"       - {p}")

        if intento < MAX_REINTENTOS_TITULO:
            correccion = "\n".join(f"- {p}" for p in problemas)
            prompt_actual = prompt_base + f"""

===========================================
CORRECCION OBLIGATORIA (intento anterior fallo esta validacion automatica)
===========================================
Tu respuesta anterior tenia estos problemas EXACTOS, detectados por un
chequeo automatico en codigo (no una opinion, un hecho verificado):

{correccion}

Volve a generar TODOS los campos desde cero, resolviendo especificamente
estos problemas. Si el problema es repeticion de palabra en el titulo,
reformula completamente esa oracion con una estructura distinta, no
cambies el FOCUS_KEYWORD.
"""

    print(f"    ⚠️ Persisten problemas tras {MAX_REINTENTOS_TITULO} reintentos, "
          f"se usa la ultima version generada de todos modos.")
    return article

# ----------------------------------------------------------------------
# GUARDADO DE BORRADORES LOCALES
# ----------------------------------------------------------------------

def save_draft(item, article_md, imagen_url=None):
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
    print("🚀 Iniciando pipeline Hybrid 4.4 (categorias de menu + ranking en 2.5-flash)...")
    print(f"DEBUG: GEMINI_API_KEY {'OK' if GEMINI_API_KEY else 'FALTA'}")
    print(f"DEBUG: GEMINI_MODEL (redacción) = {GEMINI_MODEL}")
    print(f"DEBUG: GEMINI_GROUNDING_MODEL (triangulación + ranking) = {GEMINI_GROUNDING_MODEL}")
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

        # 1. Triangulacion: grounding de Gemini primero (el modelo busca y evalua)
        print("🔎 Ejecutando triangulación con grounding de Gemini...")
        fuentes_adicionales = buscar_fuentes_con_grounding(item)

        if fuentes_adicionales is None:
            print("🔎 Grounding falló técnicamente, usando cascada de respaldo...")
            fuentes_adicionales = buscar_fuentes_cascada_respaldo(item, todos_los_candidatos)

        # 2. Extraer el texto completo de cada fuente adicional encontrada
        for f in fuentes_adicionales:
            print(f"📥 Extrayendo fuente adicional: {f['source']}...")
            full_sec = extract_full_article(f["link"])
            f["texto_completo"] = (
                full_sec["text"] if full_sec and full_sec.get("text")
                else f.get("summary", "")
            )

        # 3. Extraer articulo principal
        print("📥 Extrayendo fuente principal...")
        full_article = extract_full_article(item['link'])
        contenido_principal = (
            full_article['text'] if full_article and full_article.get('text')
            else item['summary']
        )

        # 4. Buscar imagen relevante con Google Custom Search
        print("🖼️ Buscando imagen relevante...")
        fallback_image = full_article.get('top_image') if full_article else None
        imagen_url = buscar_imagen_google(item['title'], fallback_url=fallback_image)

        # 5. Redactar con Gemini + validacion programatica con reintentos
        try:
            titulos_recientes = load_titulos_recientes()

            prompt = build_prompt(
                item,
                contenido_principal,
                fuentes_adicionales=fuentes_adicionales,
                imagen_url=imagen_url,
                titulos_recientes=titulos_recientes,
            )
            article = redactar_con_validacion(prompt, item)
            save_draft(item, article, imagen_url=imagen_url)

            seo_title_generado = extraer_seo_title(article)
            if seo_title_generado:
                guardar_titulo_reciente(seo_title_generado)
                print(f"📝 Título registrado en historial: {seo_title_generado}")

        except Exception as e:
            print(f"[ERROR] No se pudo procesar '{item['title']}': {e}")

        time.sleep(6)

    print("\n✅ Pipeline finalizado.")

if __name__ == "__main__":
    main()
