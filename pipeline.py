#!/usr/bin/env python3
"""
Pipeline de automatizacion para tecno.ar (Hybrid 4.8 - 5 items/corrida + cap con relleno
+ historial de categorias para diversidad tematica + dedup tematico manejado por IA
+ manejo robusto de errores por item)
==================================================================================
1. Filtro rapido por reglas (gratis) -> reduce de cientos a ~20-30
2. Filtro contextual con Gemini (1 sola llamada, con retry, modelo 2.5-flash) -> devuelve
   un pool priorizado mas grande que MAX_ITEMS_PER_RUN, para poder aplicar el cap por
   fuente sin perder noticias importantes si se concentran en un mismo medio.
   Ademas recibe el HISTORIAL DE CATEGORIAS de las ultimas notas publicadas, para usarlo
   como criterio de DESEMPATE (no como cuota dura) y evitar que el feed se concentre
   siempre en las mismas 1-2 categorias. Tambien recibe el HISTORIAL DE TEMAS ya
   publicados y la propia lista a evaluar, para descartar duplicados tematicos
   (mismo hecho cubierto por medios distintos) directamente en el criterio del modelo,
   sin depender de similitud de texto en Python.
3. Triangulacion de fuentes: el grounding de Gemini con Google Search, restringido a
   UNA SOLA fuente de MAXIMA AUTORIDAD (ver FUENTES_MAXIMA_AUTORIDAD), es el metodo
   PRIORITARIO por su precision semantica; si no encuentra nada dentro de esa
   whitelist chica (para no gastar tokens de mas), cae como RESPALDO a la busqueda
   directa deterministica con Google Custom Search (web abierta sin restriccion de
   sitio primero, despues sitios de referencia, despues pool de RSS local), que no
   consume tokens de Gemini.
4. Extraccion del articulo completo desde todas las fuentes (trafilatura + readability)
5. Busqueda de imagen relevante via Google Custom Search API
6. Redaccion con Gemini + VALIDACION PROGRAMATICA (no depende de autoevaluacion
   del modelo): detecta repeticion de palabras en titulo/H1 en Python puro y
   pide correccion especifica con reintentos si encuentra problemas

Cada item se procesa dentro de un try/except individual en main(), de forma que
un fallo en cualquier etapa (triangulacion, extraccion, imagen, redaccion) para
UNA sola noticia no interrumpe el procesamiento del resto de la corrida.

NOTA SOBRE RATE LIMITS: ademas del backoff reactivo que ya existia ante
errores 429/5xx, se agregaron esperas PROACTIVAS antes de cada llamada a la
API de Gemini y antes de cada llamada a Google Custom Search, mas esperas
entre los pasos sucesivos de la cascada de triangulacion, entre la
extraccion de cada fuente adicional, y entre el procesamiento de cada item.
El objetivo es espaciar las llamadas salientes para reducir al maximo la
probabilidad de pegar contra un limite de cuota, no solo reaccionar cuando
ya ocurrio.
"""

import feedparser
import requests
import json
import os
import re
import time
import hashlib
from collections import Counter
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
CATEGORIAS_RECIENTES_FILE = BASE_DIR / "categorias_recientes.json"
TEMAS_RECIENTES_FILE = BASE_DIR / "temas_recientes.json"

MAX_TITULOS_RECIENTES = 15
MAX_CATEGORIAS_RECIENTES = 20  # ventana de "memoria" para el desempate por diversidad
MAX_TEMAS_RECIENTES = 15       # ventana de "memoria" para dedup tematico (mismo hecho)
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

MAX_ITEMS_PER_RUN = 5
MAX_HOURS_OLD = 12
DELAY_ENTRE_FASES = 15
GEMINI_MAX_RETRIES = 4
GEMINI_BASE_BACKOFF = 8

# ----------------------------------------------------------------------
# ESPERAS PROACTIVAS ANTI RATE-LIMIT
# ----------------------------------------------------------------------
# Estas esperas se suman ADEMAS del backoff reactivo que ya existe ante
# errores 429/5xx (GEMINI_BASE_BACKOFF, SEARCH_BASE_BACKOFF). La idea es
# espaciar las llamadas de salida ANTES de que ocurra cualquier error, para
# reducir al maximo la probabilidad de pegar contra un limite de cuota.
GEMINI_CALL_DELAY = 5           # espera antes de CADA llamada a la API de Gemini
SEARCH_CALL_DELAY = 2           # espera antes de CADA llamada a Google Custom Search
DELAY_ENTRE_PASOS_CASCADA = 3   # espera entre pasos sucesivos de la cascada de triangulacion
DELAY_ENTRE_FUENTES_EXTRA = 2   # espera entre la extraccion de cada fuente adicional
DELAY_ENTRE_ITEMS = 12          # espera entre el procesamiento de cada item (antes: 6s)

# Cuantos indices priorizados le pedimos a Gemini en el ranking contextual.
# Tiene que ser MAYOR a MAX_ITEMS_PER_RUN para que, si el cap por fuente
# descarta algun candidato del top, todavia haya "suplentes" priorizados
# para llenar ese lugar en vez de publicar menos de MAX_ITEMS_PER_RUN.
RANKING_POOL_SIZE = MAX_ITEMS_PER_RUN + 5

MAX_FUENTES_ADICIONALES = 1  # 1 sola fuente externa de máxima autoridad, además de la oficial.
SEARCH_MAX_RETRIES = 2
SEARCH_BASE_BACKOFF = 3

# Medios a los que se restringe el grounding de Gemini cuando actúa como
# metodo PRIORITARIO. Son fuentes de maxima autoridad, no el listado amplio
# de SITIOS_REFERENCIA_BUSQUEDA (ese se sigue usando solo en la cascada de
# respaldo con Custom Search).
FUENTES_MAXIMA_AUTORIDAD = [
    "reuters.com",
    "apnews.com",
    "bloomberg.com",
    "nytimes.com",
    "techcrunch.com",
    "theverge.com",
    "wired.com",
    "arstechnica.com",
    "bleepingcomputer.com",
    "thehackernews.com",
    "krebsonsecurity.com",
    "infobae.com",
    "clarin.com",
    "ambito.com",
]

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
    # Terminos generales de tecnologia
    "tecnologia", "technology", "tech", "innovacion", "innovation",
    "gadget", "gadgets", "digital", "electronica", "electronics",
    "dispositivo", "device", "actualizacion", "update", "firmware",

    # Inteligencia artificial
    "inteligencia artificial", "ai", "artificial intelligence", "ia",
    "machine learning", "aprendizaje automatico", "deep learning",
    "aprendizaje profundo", "red neuronal", "neural network",
    "llm", "modelo de lenguaje", "language model", "chatbot",
    "chatgpt", "gemini", "claude", "copilot", "gpt-", "openai",
    "anthropic", "meta ai", "grok", "perplexity", "midjourney",
    "generative ai", "ia generativa", "agentic ai", "ia agentica",
    "computer vision", "vision artificial", "nlp", "procesamiento de lenguaje natural",

    # Ciberseguridad
    "ciberseguridad", "seguridad informatica", "cybersecurity",
    "cyber security", "infosec", "hacker", "hackers", "hacking",
    "hackeo", "hackearon", "vulnerabilidad", "vulnerabilidades",
    "vulnerability", "vulnerabilities", "exploit", "zero-day", "zero day",
    "ransomware", "malware", "spyware", "phishing", "cve",
    "filtracion de datos", "data breach", "brecha de datos", "data leak",
    "ciberataque", "ciberataques", "cyberattack", "hackeado",
    "parche de seguridad", "security patch", "grupo de hackers",
    "vpn", "firewall", "encriptacion", "encryption", "autenticacion",
    "authentication", "contraseña", "password", "botnet", "ddos",
    "troyano", "trojan", "spoofing", "cracker",

    # Hardware / smartphones / electronica de consumo
    "smartphone", "smartphones", "celular", "iphone", "android",
    "procesador", "processor", "chip", "chips", "cpu", "gpu",
    "tarjeta grafica", "graphics card", "periferico", "peripheral",
    "teclado", "keyboard", "mouse", "auriculares", "headphones",
    "earbuds", "smart tv", "televisor", "tv", "tablet", "notebook",
    "laptop", "smartwatch", "wearable", "consola", "console",
    "placa de video", "motherboard", "placa madre", "bateria", "battery",
    "grafeno", "graphene", "semiconductor", "semiconductores", "chipset",
    "pantalla", "display", "screen", "camara", "camera", "sensor",
    "carga rapida", "fast charging", "usb-c", "bluetooth", "5g", "6g",
    "wifi", "wi-fi", "router",

    # Software y plataformas
    "software", "app", "apps", "aplicacion", "application",
    "sistema operativo", "operating system", "windows", "macos",
    "linux", "ios", "actualizacion de software", "software update",
    "beta", "codigo abierto", "open source", "api", "sdk",
    "programacion", "programming", "desarrollador", "developer",

    # Empresas / negocios tech
    "google", "microsoft", "apple", "amazon", "meta", "facebook",
    "instagram", "whatsapp", "samsung", "sony", "lg", "huawei",
    "xiaomi", "oneplus", "motorola", "nvidia", "intel", "amd",
    "qualcomm", "ibm", "oracle", "salesforce", "tesla", "spacex",
    "twitter", "x corp", "tiktok", "startup", "startups",
    "adquiere", "adquisicion", "acquisition", "fusion", "merger",
    "ronda de inversion", "funding round", "invierte en", "invests in",
    "ronda de financiamiento", "ipo", "salida a bolsa",

    # Gaming
    "videojuego", "videojuegos", "videogame", "videogames", "gaming",
    "gamer", "nintendo", "playstation", "ps5", "ps6", "xbox",
    "steam", "epic games", "esports", "e-sports", "esport",
    "switch", "game pass", "unreal engine", "unity",

    # Vehiculos / movilidad
    "auto electrico", "vehiculo electrico", "electric vehicle", "ev",
    "coche electrico", "auto autonomo", "vehiculo autonomo",
    "self-driving", "autonomous vehicle", "tesla", "moto electrica",
    "carga rapida", "autopilot", "conduccion autonoma", "robotaxi",

    # Realidad aumentada / virtual
    "realidad aumentada", "augmented reality", "ar", "realidad virtual",
    "virtual reality", "vr", "metaverso", "metaverse", "gafas de ra",
    "gafas de rv", "vision pro", "quest", "lentes inteligentes",
    "smart glasses", "xr", "mixed reality", "realidad mixta",

    # Cripto / blockchain
    "criptomoneda", "criptomonedas", "cryptocurrency", "crypto",
    "bitcoin", "ethereum", "blockchain", "stablecoin", "web3",
    "nft", "defi", "billetera cripto", "crypto wallet", "exchange cripto",

    # Ciencia / espacio
    "nasa", "espacio", "cientificos", "scientists",
    "estudio cientifico", "scientific study", "investigacion cientifica",
    "fisica cuantica", "quantum physics", "computacion cuantica",
    "quantum computing", "astronomia", "astronomy", "cohete", "rocket",
    "spacex", "descubrimiento cientifico", "scientific discovery",
    "mision espacial", "space mission", "satelite", "satellite",
    "telescopio", "telescope", "marte", "mars", "luna", "moon",

    # Hogar inteligente / IoT
    "smart home", "hogar inteligente", "domotica", "iot",
    "internet de las cosas", "internet of things", "electrodomestico",
    "electrodomesticos", "aspiradora robot", "robot aspirador",
    "asistente de voz", "voice assistant", "amazon echo", "alexa",
    "google home", "google assistant", "siri",

    # Cloud / infraestructura
    "nube", "cloud", "cloud computing", "computacion en la nube",
    "data center", "centro de datos", "servidor", "server",
    "almacenamiento", "storage", "streaming",
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

def load_categorias_recientes():
    """
    Devuelve la lista de categorias (string, ej: 'ia', 'ciberseguridad',
    'gaming') de las ultimas notas publicadas, en orden cronologico
    (la mas vieja primero). Se usa como contexto de diversidad para el
    ranking de Gemini, no como filtro duro.
    """
    if CATEGORIAS_RECIENTES_FILE.exists():
        try:
            return json.loads(CATEGORIAS_RECIENTES_FILE.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return []
    return []

def guardar_categoria_reciente(categoria):
    if not categoria:
        return
    categorias = load_categorias_recientes()
    categorias.append(categoria)
    categorias = categorias[-MAX_CATEGORIAS_RECIENTES:]
    CATEGORIAS_RECIENTES_FILE.write_text(
        json.dumps(categorias, ensure_ascii=False, indent=2), encoding="utf-8"
    )

def load_temas_recientes():
    """
    Devuelve una lista de dicts {'title': ..., 'summary': ...} de las
    ultimas notas publicadas, para que Gemini pueda detectar si una
    noticia candidata cubre el MISMO HECHO que algo ya publicado
    recientemente (aunque venga de un medio distinto o tenga un titulo
    distinto). Es un chequeo semantico hecho por el modelo, no una
    comparacion de similitud de texto en Python.
    """
    if TEMAS_RECIENTES_FILE.exists():
        try:
            return json.loads(TEMAS_RECIENTES_FILE.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return []
    return []

def guardar_tema_reciente(item):
    temas = load_temas_recientes()
    temas.append({
        "title": item["title"],
        "summary": item["summary"][:250],
    })
    temas = temas[-MAX_TEMAS_RECIENTES:]
    TEMAS_RECIENTES_FILE.write_text(
        json.dumps(temas, ensure_ascii=False, indent=2), encoding="utf-8"
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
    time.sleep(SEARCH_CALL_DELAY)
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
# Mas Tecno (Cripto, Redes, Smartwatch, Gadgets) / RA / IA / Ciberseguridad

LAUNCH_KEYWORDS = [
    "lanza", "lanzamiento", "presenta", "presento", "anuncia", "anuncio",
    "debuta", "revela", "sale a la venta", "disponible desde", "estrena",
    "confirma", "confirmo", "confirmacion",
    "launches", "launch", "unveils", "unveil", "announces", "announce",
    "announcement", "introduces", "introduce", "debuts", "debut",
    "reveals", "reveal", "releases", "release", "rolls out", "confirms",
]

HARDWARE_KEYWORDS = [
    "smartphone", "celular", "iphone", "procesador", "chip", "cpu", "gpu",
    "tarjeta grafica", "periferico", "teclado", "mouse", "auriculares",
    "smart tv", "televisor",
    "tablet", "notebook", "laptop", "smartwatch", "wearable", "consola",
    "placa de video", "motherboard", "placa madre", "bateria", "grafeno",
    # ingles
    "smartphones", "processor", "graphics card", "peripheral", "keyboard",
    "headphones", "earbuds", "television", "computer", "monitor",
    "console", "motherboard", "battery", "graphene", "semiconductor",
    "semiconductors", "chipset", "display", "screen", "camera sensor",
    "fast charging", "usb-c",
]

AI_KEYWORDS = [
    "inteligencia artificial", "modelo de ia", "llm", "chatgpt", "gemini",
    "claude", "openai", "anthropic", "copilot", "gpt-", "modelo de lenguaje",
    "machine learning", "deep learning", "red neuronal",
    # ingles
    "artificial intelligence", "ai model", "language model",
    "neural network", "generative ai", "ia generativa", "agentic ai",
    "ia agentica", "computer vision", "vision artificial", "nlp",
    "natural language processing", "meta ai", "grok", "perplexity",
    "midjourney", "stable diffusion", "large language model",
]

GAMING_KEYWORDS = [
    "nintendo", "playstation", "ps5", "ps6", "xbox", "videojuego", "videojuegos",
    "gaming", "steam", "epic games", "esports", "e-sports",
    "consola de videojuegos", "switch 2", "game pass",
    # ingles
    "videogame", "videogames", "gamer", "gamers", "console gaming",
    "unreal engine", "unity engine", "multiplayer", "esport",
]

VEHICULOS_KEYWORDS = [
    "auto electrico", "vehiculo electrico", "ev", "coche electrico",
    "auto autonomo", "vehiculo autonomo", "tesla", "moto electrica",
    "carga rapida", "autopilot", "conduccion autonoma",
    # ingles
    "electric vehicle", "electric car", "self-driving", "self driving",
    "autonomous vehicle", "autonomous driving", "robotaxi",
    "electric motorcycle", "fast charging",
]

RA_KEYWORDS = [
    "realidad aumentada", "realidad virtual", "metaverso", "gafas de ra",
    "gafas de rv", "vision pro", "quest 3", "lentes inteligentes", "xr",
    # ingles
    "augmented reality", "virtual reality", "metaverse", "smart glasses",
    "mixed reality", "realidad mixta", "quest headset",
]

CRIPTO_KEYWORDS = [
    "criptomoneda", "criptomonedas", "bitcoin", "ethereum", "blockchain",
    "cripto", "stablecoin", "web3", "nft",
    # ingles
    "cryptocurrency", "crypto", "defi", "crypto wallet", "crypto exchange",
]

CIENCIA_KEYWORDS = [
    "nasa", "espacio", "cientificos", "estudio cientifico",
    "investigacion cientifica", "fisica cuantica", "astronomia", "cohete",
    "spacex", "descubrimiento cientifico", "mision espacial",
    # ingles
    "space", "scientists", "scientific study", "scientific research",
    "quantum physics", "quantum computing", "computacion cuantica",
    "astronomy", "rocket", "scientific discovery", "space mission",
    "satellite", "satelite", "telescope", "telescopio", "mars", "marte",
]

HOGAR_KEYWORDS = [
    "smart home", "hogar inteligente", "domotica", "electrodomestico",
    "electrodomesticos", "aspiradora robot", "robot aspirador",
    "asistente de voz", "amazon echo", "google home",
    # ingles
    "voice assistant", "alexa", "google assistant", "siri",
    "smart appliance", "iot", "internet de las cosas", "internet of things",
    "robot vacuum",
]

CIBERSEGURIDAD_KEYWORDS = [
    "ciberseguridad", "seguridad informatica", "vulnerabilidad", "vulnerabilidades",
    "ransomware", "malware", "phishing", "exploit", "zero-day", "cve",
    "filtracion de datos", "brecha de datos", "hackeo", "hackearon",
    "ciberataque", "ciberataques", "parche de seguridad", "grupo de hackers",
    # ingles
    "cybersecurity", "cyber security", "infosec", "vulnerability",
    "vulnerabilities", "zero day", "data breach", "data leak",
    "cyberattack", "cyber attack", "security patch", "hacking group",
    "spyware", "trojan", "botnet", "ddos", "spoofing", "encryption",
]

CIBERSEGURIDAD_EVENTO_KEYWORDS = [
    "ataca", "ataco", "vulnera", "vulneraron", "expone", "expusieron",
    "filtra", "filtraron", "hackea", "hackearon", "compromete",
    "comprometieron", "parche disponible", "ya hay parche",
    # ingles
    "attacks", "attacked", "exposes", "exposed", "leaks", "leaked",
    "hacks", "hacked", "compromises", "compromised", "patch available",
    "patch released", "breached",
]

EMPRESAS_KEYWORDS = [
    "adquiere", "adquisicion", "fusion", "ronda de inversion", "invierte en",
    "compra a", "acuerdo comercial", "alianza estrategica",
    "ronda de financiamiento",
    # ingles
    "acquires", "acquisition", "merger", "funding round", "invests in",
    "investment round", "strategic partnership", "strategic alliance",
    "ipo",
]

ARGENTINA_KEYWORDS = [
    "argentina", "argentino", "buenos aires",
    "mercado libre", "mercadolibre", "globant", "uala", "ualá",
    "satellogic", "auth0", "despegar", "tiendanube",
]

PENALTY_KEYWORDS = [
    "lo que tenés que saber", "imperdible", "no te pierdas", "resumen del día",
    "lo mejor de", "top 5", "top 10",
    # ingles
    "what you need to know", "don't miss", "everything you need to know",
    "best of", "top 5", "top 10", "roundup",
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

    if any(kw in text for kw in CIBERSEGURIDAD_KEYWORDS):
        score += 3
        if any(kw in text for kw in CIBERSEGURIDAD_EVENTO_KEYWORDS):
            score += 2
        categorias.append("ciberseguridad")

    if any(kw in text for kw in RA_KEYWORDS):
        score += 3
        if is_launch:
            score += 2
        categorias.append("ra")

    if any(kw in text for kw in CRIPTO_KEYWORDS):
        score += 3
        if is_launch:
            score += 2
        categorias.append("cripto")

    if any(kw in text for kw in CIENCIA_KEYWORDS):
        score += 2
        categorias.append("ciencia")

    if any(kw in text for kw in HOGAR_KEYWORDS):
        score += 3
        if is_launch:
            score += 2   
        categorias.append("hogar")

    if any(kw in text for kw in EMPRESAS_KEYWORDS):
        score += 3
        if is_launch:
            score += 2
        categorias.append("empresas")

    if is_launch and any(kw in text for kw in ARGENTINA_KEYWORDS):
        score += 2
        categorias.append("argentina")

    if any(kw in text for kw in PENALTY_KEYWORDS):
        score -= 2

    return max(0, min(10, score)), (categorias[0] if categorias else "general")

# ----------------------------------------------------------------------
# TRIANGULACION - METODO PRIORITARIO: GROUNDING CON GOOGLE SEARCH DE GEMINI
# Restringido a UNA SOLA fuente de MAXIMA AUTORIDAD para no gastar tokens
# de mas (ver FUENTES_MAXIMA_AUTORIDAD). Si no encuentra nada dentro de esa
# whitelist chica, se cae a la cascada de Custom Search como respaldo (ver
# buscar_fuentes_triangulacion).
# ----------------------------------------------------------------------

def call_gemini_grounding_api(payload, context="grounding", retries=GEMINI_MAX_RETRIES):
    if not GEMINI_API_KEY:
        raise RuntimeError("Falta la variable de entorno GEMINI_API_KEY")

    time.sleep(GEMINI_CALL_DELAY)
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

    filtro_dominios = ", ".join(FUENTES_MAXIMA_AUTORIDAD)

    prompt = f"""
Busca en la web UNA SOLA noticia RECIENTE (ultimas 24-48 horas) que cubra el mismo
hecho o tema que esta noticia, publicada por un medio de MAXIMA AUTORIDAD:

Titulo: {item['title']}
Resumen: {item['summary'][:300]}

Restringi la busqueda EXCLUSIVAMENTE a estos dominios (si encontras cobertura
en mas de uno, quedate con el de mas autoridad y mas especifico sobre el
hecho puntual, no una nota generica sobre el tema general): {filtro_dominios}

El medio tiene que ser DISTINTO a "{item['source']}" y tiene que hablar
especificamente de este mismo evento puntual.

Devolveme SOLO un JSON con este formato exacto, sin texto adicional, con
COMO MAXIMO 1 fuente:
{{"fuentes": [{{"title": "titulo del articulo", "source": "nombre del medio",
"link": "URL completa del articulo"}}]}}

Si ninguno de esos dominios tiene cobertura de este hecho puntual, devolveme
{{"fuentes": []}}. No inventes una fuente de un dominio fuera de la lista.
"""

    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "tools": [{"google_search": {}}],
        "generationConfig": {"temperature": 0.1},
    }

    try:
        print(f"    🔎 Grounding de Gemini ({GEMINI_GROUNDING_MODEL}) restringido a "
              f"fuentes de máxima autoridad: '{item['title'][:60]}...'")
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
            # Blindaje en Python: aunque el prompt ya restringe el dominio,
            # no confiamos ciegamente en que el modelo respete la instrucción.
            if not any(autoridad in dominio for autoridad in FUENTES_MAXIMA_AUTORIDAD):
                print(f"    ⏭️ Fuente descartada por no pertenecer a la whitelist "
                      f"de máxima autoridad: {dominio}")
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
            print(f"    ✅ Grounding encontró {len(fuentes)} fuente(s) de máxima autoridad: "
                  + ", ".join(f["source"] for f in fuentes))
        else:
            print("    ℹ️ Grounding no encontró fuentes de máxima autoridad para este hecho.")

        return fuentes

    except Exception as e:
        print(f"    ⚠️ Excepción usando grounding ({e}), se probará la cascada de respaldo.")
        return None

# ----------------------------------------------------------------------
# TRIANGULACION - METODO DE RESPALDO: BUSQUEDA DIRECTA CON CUSTOM SEARCH
# (web abierta primero, sitios de referencia despues, pool de RSS al final)
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

def buscar_fuentes_busqueda_directa(item, todos_los_candidatos, max_fuentes=MAX_FUENTES_ADICIONALES):
    """
    Metodos de triangulacion basados en Google Custom Search (deterministicos,
    no dependen de que un modelo de IA interprete o busque nada), probados en
    ESTE ORDEN, y usados como RESPALDO solo si el grounding de Gemini (metodo
    prioritario) no encontro nada dentro de su whitelist de maxima autoridad:

    1. Web abierta, SIN restriccion de sitio (maxima cobertura posible de
       toda la web, no solo un listado fijo de medios).
    2. Sitios de referencia, ultimas 24hs.
    3. Sitios de referencia, ultimas 72hs.
    4. Pool de RSS local (similitud de texto, sin llamar a ninguna API externa).
    """
    query_base = " ".join(item["title"].split()[:10])
    dominio_origen = _extraer_dominio(item["link"])
    titulo_original = item["title"]

    if GOOGLE_SEARCH_API_KEY and GOOGLE_SEARCH_ENGINE_ID:
        print("    [Método 1] Web abierta (sin restricción de sitio), últimas 48hs...")
        fuentes = _ejecutar_busqueda_texto(query_base, "d2", dominio_origen, max_fuentes, titulo_original)
        if fuentes:
            print(f"    ✅ Método 1 exitoso: {', '.join(f['source'] for f in fuentes)}")
            return fuentes
        time.sleep(DELAY_ENTRE_PASOS_CASCADA)

        filtro_sitios = " OR ".join(f"site:{s}" for s in SITIOS_REFERENCIA_BUSQUEDA)
        query_sitios = f"({filtro_sitios}) {query_base}"

        print("    [Método 2] Sitios de referencia, últimas 24hs...")
        fuentes = _ejecutar_busqueda_texto(query_sitios, "d1", dominio_origen, max_fuentes, titulo_original)
        if fuentes:
            print(f"    ✅ Método 2 exitoso: {', '.join(f['source'] for f in fuentes)}")
            return fuentes
        time.sleep(DELAY_ENTRE_PASOS_CASCADA)

        print("    [Método 3] Sitios de referencia, últimas 72hs...")
        fuentes = _ejecutar_busqueda_texto(query_sitios, "d3", dominio_origen, max_fuentes, titulo_original)
        if fuentes:
            print(f"    ✅ Método 3 exitoso: {', '.join(f['source'] for f in fuentes)}")
            return fuentes
        time.sleep(DELAY_ENTRE_PASOS_CASCADA)
    else:
        print("    ⚠️ Sin credenciales de Google Search, saltando métodos 1-3.")

    print("    [Método 4] Pool de RSS local...")
    fuente_rss = encontrar_fuente_secundaria(item, todos_los_candidatos)
    if fuente_rss:
        print(f"    ✅ Método 4 exitoso: {fuente_rss['source']}")
        return [fuente_rss]

    print("    ℹ️ Búsqueda directa sin resultados en ningún método.")
    return []


def buscar_fuentes_triangulacion(item, todos_los_candidatos, max_fuentes=MAX_FUENTES_ADICIONALES):
    """
    Orquesta la triangulacion completa de fuentes adicionales para un item:

    1. PRIMERO prueba el grounding de Gemini con Google Search, restringido
       a 1 sola fuente de maxima autoridad (ver FUENTES_MAXIMA_AUTORIDAD).
       Es el metodo semanticamente mas preciso para encontrar cobertura del
       mismo hecho, pero tambien el que mas cuota de tokens de Gemini
       consume, por eso queda limitado a 1 fuente y a una whitelist chica.
    2. Solo si el grounding no encontro nada (o no hay API key), cae como
       RESPALDO a la cascada deterministica con Google Custom Search (web
       abierta -> sitios de referencia -> pool de RSS), que no consume
       tokens de Gemini.
    """
    print("    [Método 1 - prioritario] Grounding de Gemini, 1 fuente de máxima autoridad...")
    fuentes_grounding = buscar_fuentes_con_grounding(item, max_fuentes=max_fuentes)
    if fuentes_grounding:
        return fuentes_grounding

    time.sleep(DELAY_ENTRE_PASOS_CASCADA)
    print("    [Método de respaldo] Grounding sin resultados, se prueba la cascada "
          "de búsqueda directa con Google Custom Search...")
    fuentes = buscar_fuentes_busqueda_directa(item, todos_los_candidatos, max_fuentes=max_fuentes)
    if fuentes:
        return fuentes

    print("    ℹ️ Ningún método de triangulación (grounding ni búsqueda directa) encontró fuentes.")
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
    """
    Extrae texto y metadata con trafilatura. Blindado con try/except propio
    porque trafilatura.extract_metadata() puede fallar internamente en
    lxml/htmldate (ej. lxml.etree.SerialisationError: IO_ENCODER) con
    ciertas paginas de estructura o encoding problematico. Si la extraccion
    de TEXTO funciono pero la de METADATA falla, igual devolvemos el texto
    con metadata vacia en vez de perder el articulo completo. Si extract()
    en si mismo falla, devolvemos None para que el caller pruebe con
    readability como fallback.
    """
    try:
        text = trafilatura.extract(
            html,
            url=url,
            favor_precision=True,
            include_comments=False,
            include_tables=False,
        )
    except Exception as e:
        print(f"  ⚠️ Trafilatura.extract() falló ({type(e).__name__}: {e}), "
              f"se prueba con readability.")
        return None

    if not text or len(text) < 200:
        return None

    try:
        metadata = trafilatura.extract_metadata(html, default_url=url)
    except Exception as e:
        print(f"  ⚠️ Trafilatura extrajo el texto pero extract_metadata() falló "
              f"({type(e).__name__}: {e}), se continúa con metadata vacía.")
        metadata = None

    return {
        "title": metadata.title if metadata else "",
        "text": text,
        "authors": [metadata.author] if metadata and metadata.author else [],
        "publish_date": metadata.date if metadata else None,
        "top_image": metadata.image if metadata else None,
    }

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
    Se usa para que el ranking pegue a GEMINI_GROUNDING_URL (gemini-2.5-flash,
    modelo estable) en vez del modelo preview de redaccion, que sufre mas
    503/timeouts con prompts grandes.
    """
    if not GEMINI_API_KEY:
        raise RuntimeError("Falta la variable de entorno GEMINI_API_KEY")

    url = url or GEMINI_URL
    time.sleep(GEMINI_CALL_DELAY)
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

def rank_with_gemini(candidatos, categorias_recientes=None, temas_recientes=None):
    """
    Le pide a Gemini que devuelva un pool de RANKING_POOL_SIZE indices
    ordenados por prioridad (no solo los MAX_ITEMS_PER_RUN finales). Esto
    le da a fetch_new_relevant_items "suplentes" priorizados para poder
    aplicar el cap por fuente sin perder noticias importantes cuando varias
    de las mejores del dia se concentran en un mismo medio.

    categorias_recientes: lista de categorias (str) de las ultimas notas
    publicadas. Se usa SOLO como criterio de desempate para favorecer
    diversidad tematica cuando dos o mas candidatos quedan parejos en
    merito periodistico — nunca como cuota dura ni para descartar una
    noticia fuerte por su categoria.

    temas_recientes: lista de dicts {'title', 'summary'} de las ultimas
    notas YA PUBLICADAS. Se usa para que el propio Gemini descarte
    candidatos que cubran el MISMO HECHO que algo ya publicado, y tambien
    para deduplicar entre si los candidatos de la lista actual (ej. tres
    medios distintos cubriendo la misma noticia en la misma corrida).
    Esto es un chequeo semantico hecho por el modelo, no una comparacion
    de similitud de texto en Python.
    """
    if not candidatos:
        return candidatos

    categorias_recientes = categorias_recientes or []
    temas_recientes = temas_recientes or []

    # Si hay pocos candidatos Y no hay historial de temas que chequear,
    # no vale la pena gastar una llamada a Gemini: no hay nada para
    # rankear ni para deduplicar.
    if len(candidatos) <= MAX_ITEMS_PER_RUN and not temas_recientes:
        return candidatos

    if not GEMINI_API_KEY:
        print("⚠️ Sin API Key, usando orden por reglas.")
        return candidatos

    pool_objetivo = min(len(candidatos), RANKING_POOL_SIZE)
    conteo_categorias = Counter(categorias_recientes)
    if conteo_categorias:
        resumen_categorias = ", ".join(
            f"{cat}: {n}" for cat, n in conteo_categorias.most_common()
        )
    else:
        resumen_categorias = "sin datos previos"

    print(f"🧠 Enviando {len(candidatos)} noticias a Gemini ({GEMINI_GROUNDING_MODEL}) "
          f"para ranking contextual (pool priorizado de hasta {pool_objetivo})...")

    lista_texto = ""
    for idx, item in enumerate(candidatos, 1):
        lista_texto += f"{idx}. Título: {item['title']}\n   Resumen: {item['summary'][:250]}\n\n"

    bloque_temas_previos = ""
    if temas_recientes:
        lista_temas = "\n".join(
            f"- {t['title']}: {t['summary'][:150]}" for t in temas_recientes
        )
        bloque_temas_previos = f"""
===========================================
TEMAS YA PUBLICADOS RECIENTEMENTE (NO REPETIR)
===========================================
Estas son las últimas {len(temas_recientes)} notas ya publicadas en tecno.ar:

{lista_temas}

Si alguna noticia de la lista a evaluar cubre el MISMO HECHO que una de estas
(mismo evento, mismo anuncio, mismo caso puntual — aunque venga de un medio
distinto o tenga un título distinto), NO la incluyas en tu ranking, sin
importar su mérito periodístico. Ya fue cubierta y no debe repetirse.
"""

    prompt = f"""
Eres un editor jefe de un blog de tecnología llamado tecno.ar. Tu tarea es
ORDENAR POR RELEVANCIA REAL las {pool_objetivo} noticias más importantes de
la lista al final de este mensaje.

IMPORTANTE: no selecciones solo un puñado fijo. Devolveme un RANKING de
hasta {pool_objetivo} noticias ordenadas de mas a menos relevante, porque
despues un proceso automatico va a aplicar un limite de diversidad por
medio sobre tu ranking, y necesita "suplentes" priorizados por si alguna
noticia del tope queda descartada por venir del mismo medio que otra mejor
rankeada.

===========================================
REGLA DE ORO: NINGUNA CATEGORÍA VALE MÁS QUE OTRA POR DEFAULT
===========================================
tecno.ar cubre Smartphones, Hardware, Gaming, Empresas, Ciencia, Vehículos,
Hogar, Cripto, RA/RV, IA y Ciberseguridad. Estas categorías son TODAS de
igual jerarquía de partida. NO existe una tabla de prioridad fija donde
"lanzamiento de hardware" le gane automáticamente a "ciberseguridad" o
viceversa. Una vulnerabilidad crítica real, un ransomware que afectó a
miles de usuarios, o una filtración de datos masiva DEBEN poder superar en
el ranking a un lanzamiento de producto menor, si su impacto real es mayor.
Evaluá cada noticia por su propio mérito periodístico, no por a qué
categoría pertenece.

Para eso, aplicá estos CUATRO EJES a CADA noticia, sin importar su tema:

EJE 1 — CONFIRMACIÓN Y CONCRETITUD (¿pasó de verdad, o es especulación?)
Puntuación alta: hecho confirmado oficialmente por la empresa/organismo
involucrado, o evento ya ocurrido y verificable (un ataque real, una
vulnerabilidad con CVE o parche confirmado, un lanzamiento oficial, una
fusión firmada, una misión ejecutada).
Puntuación baja o descarte: rumores, filtraciones sin confirmar,
especulación ("se espera que", "podría", "estaría preparando"),
reflexiones genéricas sobre el futuro de una tecnología.

EJE 2 — IMPACTO Y ALCANCE REAL
¿A cuánta gente afecta, o qué tan grande es la empresa/sistema involucrado?
Una vulnerabilidad crítica en software usado por millones, un ransomware
que tumbó una aerolínea, o una adquisición multimillonaria tienen el MISMO
nivel de impacto que el lanzamiento de un fabricante líder — no menos. Un
anuncio menor de una empresa poco conocida, o un parche de una
vulnerabilidad de bajo riesgo, tienen impacto bajo, sea cual sea su
categoría.

EJE 3 — ACTUALIDAD
Qué tan reciente y puntual es el hecho dentro de la ventana de tiempo
cubierta. Más reciente y más "de hoy" puntúa mejor que algo que ya se viene
arrastrando hace días.

EJE 4 — VALOR INFORMATIVO PARA EL LECTOR
¿Le aporta algo concreto al lector (una fecha, un riesgo de seguridad que
debería conocer y mitigar, un producto que puede comprar, un dato
verificable)? Se descarta siempre, sin importar el tema: opinión o análisis
retrospectivo ("por qué X importa", "lo que aprendimos de..."), rankings y
listicles ("top 10", "lo mejor de la semana"), reviews de productos que ya
llevan tiempo en el mercado, y contenido educativo genérico sin un hecho
puntual detrás — un "10 consejos para protegerte de hackers" se descarta
exactamente igual que un "10 curiosidades sobre el universo".

===========================================
CÓMO SE VE UN EJE 1 ALTO EN CADA CATEGORÍA (ejemplos, no jerarquía)
===========================================
- Hardware/Smartphones: "Samsung presenta el Galaxy S26 con nuevo procesador propio"
- IA: "OpenAI lanza GPT-6 con capacidades de razonamiento mejoradas"
- Empresas: "Microsoft adquiere la startup de ciberseguridad XDR por USD 500 millones"
- Gaming: "Nintendo confirma la fecha de lanzamiento del nuevo Zelda"
- Vehículos: "Tesla presenta una actualización de Autopilot con nuevo hardware"
- RA/RV: "Meta lanza una actualización de software para Quest 3"
- Cripto: "PayPal habilita pagos en Bitcoin para comercios en Argentina"
- Ciencia: "SpaceX confirma la fecha del próximo lanzamiento de Starship"
- Hogar: "Amazon presenta un nuevo Echo con IA integrada"
- Ciberseguridad: "Ransomware ataca los servidores de una aerolínea europea" /
  "Descubren vulnerabilidad crítica en routers Cisco, ya hay parche
  disponible" / "Filtración expone datos de 2 millones de usuarios de una
  app de delivery"

Ejemplos de EJE 1 bajo o descarte en cualquier categoría: "Se filtran
posibles specs del próximo iPhone" (rumor), "5 cosas que esperamos ver en
el próximo Galaxy Unpacked" (especulación/preview), "Los ciberataques más
comunes en 2026" (listicle genérico), "¿Podría Apple comprar Netflix?"
(especulación sin negociación real).

===========================================
CÓMO DECIDIR ENTRE VARIAS NOTICIAS CON PUNTAJE SIMILAR
===========================================
Si varias noticias quedan parejas tras aplicar los 4 ejes, priorizá la que
tenga: (a) confirmación oficial más directa, (b) mayor impacto o alcance
(más usuarios/empresas afectadas pesa más que un caso aislado), (c) mayor
actualidad dentro de la ventana de tiempo. Esto aplica igual entre dos
noticias de la misma categoría o de categorías distintas.

===========================================
CONTEXTO DE DIVERSIDAD TEMATICA (criterio de DESEMPATE, no reemplaza los
4 ejes de arriba)
===========================================
Estas son las categorías de las últimas {len(categorias_recientes)} notas
publicadas en tecno.ar (de más vieja a más reciente): {resumen_categorias}

Esto NO es una cuota ni un límite. Los 4 ejes siguen siendo el criterio
principal, y una categoría sobrerrepresentada puede perfectamente tener hoy
la noticia más importante del lote — en ese caso va primero igual. Usá este
contexto SOLO para desempatar cuando dos o más noticias queden realmente
parejas en mérito periodístico: en ese caso, dale prioridad en el ranking a
la que pertenezca a una categoría menos representada en el historial
reciente, para que el feed no se concentre siempre en las mismas 1-2
categorías.
{bloque_temas_previos}
===========================================
DEDUPLICACION DENTRO DE ESTA MISMA LISTA
===========================================
Ademas de descartar temas ya publicados, revisá si DOS O MÁS noticias de la
lista a evaluar (mas abajo) hablan del MISMO HECHO puntual entre sí (mismo
anuncio, mismo evento, mismo caso — aunque vengan de medios distintos y con
títulos distintos). Si eso pasa, NO incluyas a todas en el ranking: elegí
solo la mejor cubierta (la más completa, con fuente más oficial, o más
reciente) y descartá al resto como si no existieran. El ranking final no
puede tener dos entradas sobre el mismo hecho.

===========================================
LISTA DE NOTICIAS A EVALUAR
===========================================
{lista_texto}

===========================================
FORMATO DE SALIDA (obligatorio)
===========================================
Devolvé SOLO un JSON con los números de los índices que sobrevivan a la
deduplicación (temas ya publicados + duplicados internos), ORDENADOS DE MAS
A MENOS RELEVANTE (el primero de la lista es el mas relevante). Puede haber
menos de {pool_objetivo} índices si se descartaron duplicados; eso es
correcto y esperado. No agregues texto explicativo antes ni después del
JSON. Formato exacto: {{"seleccionados": [3, 7, 12, 1, 9]}}
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
            print("⚠️ Gemini no devolvió índices (puede que haya descartado todo por "
                  "duplicado), usando orden por reglas como respaldo.")
            return candidatos[:pool_objetivo]

        seleccionados = []
        for i in indices:
            if 1 <= i <= len(candidatos):
                seleccionados.append(candidatos[i - 1])
            if len(seleccionados) >= pool_objetivo:
                break

        print(f"✅ Gemini devolvió un ranking priorizado de {len(seleccionados)} noticias "
              f"(deduplicado contra historial y contra sí mismo).")
        return seleccionados

    except Exception as e:
        print(f"⚠️ No se pudo rankear con Gemini ({e}), usando orden por reglas.")
        return candidatos[:pool_objetivo]

# ----------------------------------------------------------------------
# INGESTA + FILTROS
# ----------------------------------------------------------------------

MAX_POR_FUENTE = max(1, MAX_ITEMS_PER_RUN // 2)

def _seleccionar_final_con_relleno(ranking_priorizado):
    """
    Recorre el ranking priorizado (ya ordenado de mas a menos relevante)
    en DOS pasadas:

    1ra pasada: respeta MAX_POR_FUENTE (cap de diversidad por medio).
    2da pasada (solo si hacen falta mas items para llegar a
    MAX_ITEMS_PER_RUN): recorre los candidatos que quedaron afuera por el
    cap, en el mismo orden de prioridad, IGNORANDO el cap, para no
    desperdiciar un lugar cuando no hay suplentes de otras fuentes
    disponibles. Prioriza llenar los MAX_ITEMS_PER_RUN lugares por sobre
    mantener la diversidad artificial.
    """
    seleccionados_final = []
    descartados_por_cap = []
    conteo_por_fuente = {}

    for item in ranking_priorizado:
        if len(seleccionados_final) >= MAX_ITEMS_PER_RUN:
            break
        fuente = item["source"]
        if conteo_por_fuente.get(fuente, 0) >= MAX_POR_FUENTE:
            descartados_por_cap.append(item)
            continue

        seleccionados_final.append(item)
        conteo_por_fuente[fuente] = conteo_por_fuente.get(fuente, 0) + 1

    if len(seleccionados_final) < MAX_ITEMS_PER_RUN and descartados_por_cap:
        faltan = MAX_ITEMS_PER_RUN - len(seleccionados_final)
        print(f"    ℹ️ Faltan {faltan} noticia(s) para llegar a {MAX_ITEMS_PER_RUN}; "
              f"se completa con candidatos que habían quedado afuera por el cap "
              f"de diversidad por fuente, respetando el orden de prioridad.")
        for item in descartados_por_cap:
            if len(seleccionados_final) >= MAX_ITEMS_PER_RUN:
                break
            seleccionados_final.append(item)

    return seleccionados_final

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

    categorias_recientes = load_categorias_recientes()
    temas_recientes = load_temas_recientes()

    ranking_priorizado = rank_with_gemini(
        candidatos,
        categorias_recientes=categorias_recientes,
        temas_recientes=temas_recientes,
    )

    seleccionados_final = _seleccionar_final_con_relleno(ranking_priorizado)

    for item in seleccionados_final:
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

1. MENCION DEL KEYWORD (la mas importante):
   - Antes de escribir la oracion, preguntate DOS cosas:
     a) "¿el keyword ya trae su propio sujeto y verbo?" (Regla Critica #1)
     b) "¿el keyword ya trae palabras significativas que mi frase envolvente
        podria repetir?" (Regla Critica #2)
   - Si (a) es cierto, usa un sujeto distinto para tu oracion.
   - Si (b) es cierto, no repitas esas palabras en el texto que rodea
     directamente al keyword.
   - El keyword debe aparecer como STRING EXACTO dentro de una oracion que
     se lea 100% natural al leerla en voz alta.
   - El keyword debe tener una densidad dentro del cuerpo de aproximadamente %1,3.

2. SUBTITULOS (H2) — EL KEYWORD DEBE ESTAR PRESENTE:
   - Dividi el cuerpo en al menos 3-4 subtitulos H2 (##).
   - AL MENOS UNO de esos subtitulos debe contener el focus keyword completo.
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
    print("🚀 Iniciando pipeline Hybrid 5.0 (5 items/corrida + cap con relleno "
          "+ diversidad de categorias + dedup tematico por IA + manejo robusto "
          "de errores por item + grounding con 1 fuente de maxima autoridad como "
          "metodo prioritario / Custom Search como respaldo + esperas anti "
          "rate-limit)...")
    print(f"DEBUG: GEMINI_API_KEY {'OK' if GEMINI_API_KEY else 'FALTA'}")
    print(f"DEBUG: GEMINI_MODEL (redacción) = {GEMINI_MODEL}")
    print(f"DEBUG: GEMINI_GROUNDING_MODEL (grounding prioritario + ranking) = {GEMINI_GROUNDING_MODEL}")
    print(f"DEBUG: MAX_ITEMS_PER_RUN = {MAX_ITEMS_PER_RUN} | RANKING_POOL_SIZE = {RANKING_POOL_SIZE} "
          f"| MAX_POR_FUENTE = {MAX_POR_FUENTE}")
    print(f"DEBUG: MAX_FUENTES_ADICIONALES = {MAX_FUENTES_ADICIONALES} "
          f"| FUENTES_MAXIMA_AUTORIDAD = {len(FUENTES_MAXIMA_AUTORIDAD)} dominios")
    print(f"DEBUG: MAX_CATEGORIAS_RECIENTES (ventana de diversidad) = {MAX_CATEGORIAS_RECIENTES}")
    print(f"DEBUG: MAX_TEMAS_RECIENTES (ventana de dedup tematico) = {MAX_TEMAS_RECIENTES}")
    print(f"DEBUG: GOOGLE_SEARCH_API_KEY {'OK' if GOOGLE_SEARCH_API_KEY else 'FALTA'}")
    print(f"DEBUG: GOOGLE_SEARCH_ENGINE_ID {'OK' if GOOGLE_SEARCH_ENGINE_ID else 'FALTA'}")
    print(f"DEBUG: GEMINI_CALL_DELAY = {GEMINI_CALL_DELAY}s | SEARCH_CALL_DELAY = {SEARCH_CALL_DELAY}s | "
          f"DELAY_ENTRE_PASOS_CASCADA = {DELAY_ENTRE_PASOS_CASCADA}s | "
          f"DELAY_ENTRE_FUENTES_EXTRA = {DELAY_ENTRE_FUENTES_EXTRA}s | "
          f"DELAY_ENTRE_ITEMS = {DELAY_ENTRE_ITEMS}s")

    items, todos_los_candidatos = fetch_new_relevant_items()
    print(f"Encontrados {len(items)} items nuevos para procesar.")

    if items:
        print(f"⏳ Esperando {DELAY_ENTRE_FASES}s antes de redactar (evitar rate limit)...")
        time.sleep(DELAY_ENTRE_FASES)

    for item in items:
        print(f"\n{'='*60}")
        print(f"📄 Procesando: {item['title'][:70]}...")

        # Todo el procesamiento de este item vive dentro de un unico
        # try/except: si falla cualquier etapa (triangulacion, extraccion
        # de fuentes, extraccion del articulo principal, busqueda de
        # imagen, redaccion o validacion), se descarta SOLO esta noticia
        # y el pipeline sigue con la siguiente. Antes, un fallo en la
        # extraccion del articulo principal (ej. error de trafilatura/lxml
        # al buscar metadata) quedaba fuera de este bloque y tumbaba toda
        # la corrida, perdiendo tambien los borradores de items anteriores
        # que todavia no se habian subido a WordPress.
        try:
            # 1. Triangulacion: grounding de Gemini restringido a 1 fuente de
            # maxima autoridad como metodo prioritario; si no encuentra nada,
            # cae como respaldo a la busqueda directa con Google Custom Search
            # (web abierta -> sitios de referencia -> pool de RSS).
            print("🔎 Ejecutando triangulación (grounding de Gemini prioritario, "
                  "Custom Search como respaldo)...")
            fuentes_adicionales = buscar_fuentes_triangulacion(item, todos_los_candidatos)

            # 2. Extraer el texto completo de cada fuente adicional encontrada
            for idx_f, f in enumerate(fuentes_adicionales):
                if idx_f > 0:
                    time.sleep(DELAY_ENTRE_FUENTES_EXTRA)
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

            # Registramos la categoria de esta nota en el historial de
            # diversidad, para que el proximo ranking la tenga en cuenta
            # como criterio de desempate.
            guardar_categoria_reciente(item.get("categoria_reglas"))
            print(f"🗂️ Categoría registrada en historial de diversidad: "
                  f"{item.get('categoria_reglas')}")

            # Registramos el tema (titulo + resumen) de esta nota en el
            # historial de dedup, para que el proximo ranking de Gemini
            # pueda descartar noticias que cubran el mismo hecho.
            guardar_tema_reciente(item)
            print(f"🧩 Tema registrado en historial de dedup: {item['title'][:60]}")

        except Exception as e:
            print(f"[ERROR] No se pudo procesar '{item['title']}': {type(e).__name__}: {e}")

        time.sleep(DELAY_ENTRE_ITEMS)

    print("\n✅ Pipeline finalizado.")

if __name__ == "__main__":
    main()
