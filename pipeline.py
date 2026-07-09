#!/usr/bin/env python3
"""
Pipeline de automatizacion para tecno.ar (Hybrid 2.0 + Articulo Completo)
==========================================================================
1. Filtro rapido por reglas (gratis) -> reduce de cientos a ~20-30
2. Filtro contextual con Gemini (1 sola llamada, con retry) -> elige las 3 mejores
3. Extraccion del articulo completo desde la URL (trafilatura + readability)
4. Redaccion con Gemini usando el articulo completo (con retry, keyword semantico)
"""

import feedparser
import requests
import json
import os
import re
import time
import hashlib
from pathlib import Path
from datetime import datetime, timezone, timedelta

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

MAX_ITEMS_PER_RUN = 3  # <--- REDUCIDO A 3 para no consumir cuota
MAX_HOURS_OLD = 24

# Pausa de seguridad entre la fase de ranking y la fase de redacción,
# para no pisar el límite de requests-por-minuto de Gemini.
DELAY_ENTRE_FASES = 15  # segundos

# Reintentos ante rate limit (429) para cualquier llamada a Gemini.
GEMINI_MAX_RETRIES = 4
GEMINI_BASE_BACKOFF = 8  # segundos, crece exponencialmente: 8, 16, 32, 64...

# Headers realistas para no ser bloqueado por medios (The Verge, TechCrunch, etc. filtran bots)
REQUEST_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "es-AR,es;q=0.9,en-US;q=0.8,en;q=0.7",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

# Palabras clave para el filtro rapido (barrera de entrada)
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
# SISTEMA DE SCORING POR REGLAS (SOLO PARA PREFILTRAR)
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
# EXTRACCIÓN DEL ARTÍCULO COMPLETO (robusta, con fallback en cascada)
# ----------------------------------------------------------------------

def _fetch_html(url, timeout=15):
    """Descarga el HTML crudo con headers realistas y timeout corto."""
    resp = requests.get(url, headers=REQUEST_HEADERS, timeout=timeout, allow_redirects=True)
    resp.raise_for_status()
    resp.encoding = resp.apparent_encoding  # evita textos con caracteres rotos (acentos, ñ, etc.)
    return resp.text


def _extract_with_trafilatura(html, url):
    """Método principal: funciona bien tanto en medios grandes (The Verge, TechCrunch)
    como en medios argentinos (Xataka, La Nación, Infobae, etc.)."""
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
    """Fallback si trafilatura no consigue suficiente texto (sitios con markup atípico)."""
    try:
        doc = Document(html)
        title = doc.short_title()
        summary_html = doc.summary()
        soup = BeautifulSoup(summary_html, "html.parser")
        text = "\n\n".join(
            p.get_text(strip=True) for p in soup.find_all("p") if p.get_text(strip=True)
        )
        if text and len(text) >= 200:
            return {"title": title, "text": text, "authors": [], "publish_date": None, "top_image": None}
    except Exception:
        pass
    return None


def extract_full_article(url):
    """
    Extrae el contenido completo de un artículo desde su URL.
    Orden de intentos: trafilatura -> readability -> None (usa el resumen del RSS).
    Sirve tanto para medios internacionales como argentinos, sin reglas por sitio.
    """
    try:
        print(f"📄 Extrayendo artículo completo: {url[:60]}...")
        html = _fetch_html(url)
    except requests.exceptions.RequestException as e:
        print(f"⚠️ No se pudo descargar la URL ({type(e).__name__}): {e}")
        return None

    result = _extract_with_trafilatura(html, url)
    if result:
        print(f"✅ Extraído con trafilatura ({len(result['text'])} caracteres)")
        return result

    result = _extract_with_readability(html, url)
    if result:
        print(f"✅ Extraído con readability (fallback) ({len(result['text'])} caracteres)")
        return result

    print("⚠️ Ningún método pudo extraer texto útil, se usará el resumen del RSS.")
    return None

# ----------------------------------------------------------------------
# HELPER COMPARTIDO: LLAMADA A GEMINI CON RETRY/BACKOFF
# ----------------------------------------------------------------------

def call_gemini_api(payload, context="gemini", retries=GEMINI_MAX_RETRIES):
    """
    Hace POST a Gemini con reintentos ante 429 (rate limit) y errores
    transitorios de red (timeout, conexión). 'context' es solo para logs.
    Devuelve el dict de la respuesta JSON si tiene éxito, o lanza excepción
    si se agotan los reintentos.
    """
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
                last_error = RuntimeError(f"Error {resp.status_code} tras {retries} intentos ({context})")
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
    Eres un editor jefe de un blog de tecnología llamado tecno.ar, con audiencia argentina.
    Tu tarea es seleccionar las {MAX_ITEMS_PER_RUN} noticias MÁS RELEVANTES Y CON CONTEXTO de la siguiente lista.

    Criterios de selección (en orden de prioridad):
    1. **Lanzamientos oficiales** de hardware (celulares, procesadores, GPU, etc.) o software.
    2. **Avances reales en Inteligencia Artificial** (nuevos modelos, aplicaciones prácticas).
    3. **Tecnología argentina** o que impacte directamente en Argentina.
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

            if score_reglas < 3:
                continue

            candidatos.append({
                "hash": h,
                "title": entry.get("title", "Sin titulo"),
                "link": entry.get("link", ""),
                "summary": re.sub("<[^<]+?>", "", entry.get("summary", ""))[:600],
                "source": source_name,
                "published": entry.get("published", ""),
                "score_reglas": score_reglas,
                "categoria_reglas": categoria_reglas,
            })

    print(f"📰 Noticias que pasaron el filtro rapido: {len(candidatos)}")

    if not candidatos:
        return []

    if len(candidatos) > 30:
        candidatos.sort(key=lambda x: x["score_reglas"], reverse=True)
        candidatos = candidatos[:30]
        print("🔪 Limitando a 30 para el ranking contextual.")

    if len(candidatos) > MAX_ITEMS_PER_RUN:
        seleccionados_por_ia = rank_with_gemini(candidatos)
    else:
        seleccionados_por_ia = candidatos

    # Tope por fuente (equidad)
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

    return seleccionados_final

# ----------------------------------------------------------------------
# REDACCION CON ARTICULO COMPLETO
# ----------------------------------------------------------------------

def build_prompt(item, full_text):
    """
    Construye el prompt para Gemini usando el artículo completo extraído.
    """
    # Limitar el texto a 8000 caracteres para no exceder el contexto de Gemini
    text_limit = full_text[:8000] if full_text else item['summary']

    return f"""Actua como un redactor SEO senior especializado en tecnologia,
con dominio experto de los criterios de puntuacion de Rank Math para WordPress,
escribiendo para el sitio tecno.ar.

FUENTE DE REFERENCIA (USA ESTE TEXTO COMPLETO PARA REDACTAR, NO INVENTES DATOS):
Titulo original: {item['title']}
Medio: {item['source']}
URL original: {item['link']}

TEXTO COMPLETO DEL ARTICULO (basate en esto para redactar, sin inventar):
{text_limit}

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
- "rastreador Moto Tag 2"         (en vez de "Moto Tag 2 Motorola";
   aca "rastreador" es la categoria del producto, que SI se puede combinar
   naturalmente con el nombre propio)

REGLA GENERAL: si el nombre del producto ya es un sustantivo reconocible en
español (rastreador, auriculares, procesador, modelo, chatbot, app, robot,
notebook, etc.), combina ESA categoria con el nombre propio del producto.
Si el nombre propio no tiene una categoria clara en el texto, arma una frase
descriptiva de lo que HACE o ES la noticia (ej: "voz mas natural de ChatGPT",
en vez de encadenar los dos nombres propios de la marca y el producto).

CHECKLIST antes de definir el keyword final (verificalo vos mismo):
1. ¿Se puede leer el keyword dentro de una oracion completa sin sonar
   una lista de nombres propios pegados? Si no, reformulalo.
2. ¿Tiene al menos una palabra funcional en español (de, con, para, en) que
   conecte los sustantivos, salvo que sea una sola categoria + un nombre
   propio (ej: "rastreador Moto Tag 2")? Si son 2+ nombres propios de marcas
   o modelos distintos pegados sin conector, esta mal.
3. ¿Es asi como lo diria un periodista argentino en voz alta? Si suena a
   tag de metadata, esta mal.

===========================================
PASO 2: GENERA TODOS ESTOS CAMPOS (en este orden exacto)
===========================================

## FOCUS_KEYWORD
[el keyword elegido, ya validado con el checklist de arriba]

## SEO_TITLE
Titulo de 50-60 caracteres. Reglas:
- El focus keyword debe aparecer LO MAS CERCA POSIBLE DEL INICIO del titulo.

## SLUG
version-corta-en-minusculas-con-guiones-del-focus-keyword
(3-5 palabras maximo)

## META_DESCRIPTION
Entre 150 y 160 caracteres. Debe incluir el focus keyword.

## H1
El titulo visible del articulo. Debe incluir el focus keyword.

## ARTICULO
El cuerpo de la nota en Markdown (600-900 palabras), siguiendo ESTAS reglas:
1. ESTRUCTURA:
   - El focus keyword debe aparecer en el PRIMER PARRAFO, integrado en una
     oracion natural (nunca pegado como si fuera una etiqueta suelta).
   - Dividi el cuerpo en al menos 3-4 subtitulos H2 (##).
   - Parrafos cortos: maximo 3-4 lineas cada uno.
2. CONTENIDO:
   - NO copies frases textuales de la fuente; parafrasea completamente.
   - Agrega contexto, antecedentes o una perspectiva que no este en el resumen.
   - Evita frases genericas de relleno tipicas de IA.
   - Voz activa, tono profesional pero cercano (espanol).
   - COHERENCIA: relee mentalmente cada oracion donde aparece el focus
     keyword y confirma que se entiende igual que el resto del texto,
     sin cortes abruptos de sintaxis.
3. ENLACES:
   - Incluir al menos 1 ENLACE EXTERNO real hacia la fuente original:
     [texto del enlace]({item['link']})
4. IMAGEN:
   - Al final, sugeri un ALT_TEXT para la imagen destacada, de 8-12 palabras.

===========================================
FORMATO DE SALIDA
===========================================
Devolveme EXCLUSIVAMENTE los campos de arriba (FOCUS_KEYWORD, SEO_TITLE, SLUG,
META_DESCRIPTION, H1, ARTICULO, ALT_TEXT) con esos encabezados exactos en
Markdown. No agregues explicaciones fuera de esa estructura.
Al final del ARTICULO, agrega: "Fuente: {item['source']}"
"""


def call_gemini(prompt):
    """
    Redacta el artículo con Gemini, usando el helper compartido con
    retry/backoff ante 429 y errores transitorios.
    """
    payload = {
        "contents": [{"parts": [{"text": prompt}]}]
    }

    data = call_gemini_api(payload, context="redaccion")

    try:
        return data["candidates"][0]["content"]["parts"][0]["text"]
    except (KeyError, IndexError):
        raise RuntimeError(f"Respuesta inesperada de Gemini: {data}")

# ----------------------------------------------------------------------
# MAIN
# ----------------------------------------------------------------------

def main():
    print("🚀 Iniciando pipeline Hybrid 2.0 con artículos completos...")
    items = fetch_new_relevant_items()
    print(f"Encontrados {len(items)} items nuevos para procesar.")

    try:
        from publish_to_wordpress import generate_and_publish
        tiene_wp = True
        print("✅ Integración con WordPress activada.")
    except ImportError:
        print("⚠️ No se encontró 'publish_to_wordpress'. Los borradores se guardarán LOCALMENTE.")
        tiene_wp = False

    if items:
        print(f"⏳ Esperando {DELAY_ENTRE_FASES}s antes de redactar (evitar rate limit)...")
        time.sleep(DELAY_ENTRE_FASES)

    for item in items:
        print(f"\n📄 Procesando: {item['title'][:70]}...")

        # 1. Extraer el artículo completo
        full_article = extract_full_article(item['link'])

        # 2. Si no se pudo extraer, usar el resumen como fallback
        if full_article and full_article.get('text'):
            contenido_para_redactar = full_article['text']
            print(f"✅ Artículo completo extraído ({len(contenido_para_redactar)} caracteres)")
        else:
            contenido_para_redactar = item['summary']
            print(f"⚠️ Usando resumen del RSS ({len(contenido_para_redactar)} caracteres)")

        # 3. Redactar con Gemini
        try:
            prompt = build_prompt(item, contenido_para_redactar)
            article = call_gemini(prompt)

            if tiene_wp:
                try:
                    generate_and_publish(item, article)
                    print(f"✅ Borrador subido a WordPress: {item['title'][:50]}...")
                except Exception as wp_err:
                    print(f"⚠️ Error al subir a WP, guardando local: {wp_err}")
                    save_draft(item, article)
            else:
                save_draft(item, article)

        except Exception as e:
            print(f"[ERROR] No se pudo procesar '{item['title']}': {e}")
        time.sleep(6)  # Pausa entre items para respetar límites

    print("\n✅ Pipeline finalizado.")

# ----------------------------------------------------------------------
# GUARDADO DE BORRADORES LOCALES
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
    print(f"[OK] Borrador guardado localmente: {path}")

if __name__ == "__main__":
    main()
