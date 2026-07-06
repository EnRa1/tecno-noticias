#!/usr/bin/env python3
"""
Pipeline de automatización para tecno.ar (Hybrid 2.0)
Diccionarios completos y expansivos para capturar todo el espectro tecnológico.
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

# ----------------------------------------------------------------------
# CONFIGURACIÓN
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

MAX_ITEMS_PER_RUN = 6
MAX_HOURS_OLD = 12

# ======================================================================
# DICCIONARIOS COMPLETOS Y EXPANSIVOS
# ======================================================================

# ----------------------------------------------------------------------
# 1. FILTRO DE RELEVANCIA GENERAL (KEYWORDS)
#    - Palabras que indican que la noticia es de tecnología.
#    - Debe ser amplio para no descartar nada relevante.
# ----------------------------------------------------------------------
KEYWORDS = [
    # Inteligencia Artificial
    "inteligencia artificial", "ai", "machine learning", "deep learning",
    "red neuronal", "llm", "gpt", "chatgpt", "gemini", "claude", "copilot",
    "modelo de lenguaje", "ia generativa", "stable diffusion", "midjourney",
    "dalle", "openai", "anthropic", "deepmind", "hugging face",

    # Ciberseguridad
    "ciberseguridad", "seguridad informatica", "hacker", "ransomware",
    "malware", "virus", "phishing", "zero-day", "vulnerabilidad",
    "firewall", "encriptacion", "privacidad", "filtracion", "data breach",
    "ciberataque", "autenticacion", "biometria",

    # Hardware y electrónica
    "hardware", "procesador", "chip", "cpu", "gpu", "nvidia", "amd", "intel",
    "apple silicon", "ryzen", "core ultra", "snapdragon", "mediatek",
    "semiconductor", "tsmc", "ram", "ddr5", "ssd", "almacenamiento",
    "monitor", "pantalla", "oled", "microled", "arm", "risc-v",
    "smartphone", "celular", "iphone", "samsung", "galaxy", "pixel",
    "xiaomi", "huawei", "fold", "flip", "plegable", "tablet", "ipad",
    "notebook", "laptop", "ultrabook", "smartwatch", "wearable",
    "auriculares", "airpods", "consola", "playstation", "xbox", "nintendo",
    "switch", "ps5", "game pass", "steam", "tarjeta grafica",
    "periferico", "teclado", "mouse", "smart tv", "televisor",
    "auto electrico", "vehiculo electrico", "ev", "bateria", "grafeno",

    # Software y sistemas operativos
    "software", "app", "aplicacion", "windows", "linux", "ubuntu", "macos",
    "ios", "android", "chrome os", "actualizacion", "parche", "bug",
    "microsoft", "apple", "google", "beta", "open source", "codigo abierto",
    "git", "github", "software libre", "framework", "api", "backend",
    "frontend", "devops", "microservicios", "contenedores", "docker",
    "kubernetes", "cloud", "nube", "aws", "azure", "gcp", "oracle",

    # Internet, Web y comunicaciones
    "internet", "web", "navegador", "chrome", "firefox", "safari", "edge",
    "5g", "wifi", "fibra optica", "starlink", "satelite", "telecomunicaciones",
    "redes", "protocolo", "tcp/ip", "dns", "cdn", "streaming", "netflix",
    "spotify", "youtube", "twitch", "podcast", "online", "digital",

    # Tecnología emergente y ciencia
    "metaverso", "realidad virtual", "realidad aumentada", "vr", "ar",
    "blockchain", "web3", "cripto", "bitcoin", "ethereum", "nft",
    "robot", "robotica", "dron", "automatizacion", "iot", "internet de las cosas",
    "smart home", "hogar inteligente", "asistente virtual", "siri", "alexa",
    "espacio", "nasa", "spacex", "starship", "ciencia", "investigacion",
    "descubrimiento", "fisica", "cuantico", "biotecnologia", "salud",
    "innovacion", "tecnologia", "tecnológico", "digital", "electrónico",
    "gadget", "dispositivo", "sensor", "wearable", "impresion 3d",

    # Negocios, startups y economía digital
    "startup", "emprendimiento", "inversion", "financiacion", "unicornio",
    "mercado", "acciones", "nasdaq", "tesla", "spacex", "amazon", "meta",
    "alphabet", "oracle", "salesforce", "uber", "mercado libre",
    "globant", "despegar", "tiendanube", "auth0", "satellogic",
    "economia digital", "fintech", "ecommerce", "comercio electronico",
]

# ----------------------------------------------------------------------
# 2. PALABRAS CLAVE DE HARDWARE (para puntuación)
# ----------------------------------------------------------------------
HARDWARE_KEYWORDS = [
    # Procesadores y chips
    "procesador", "chip", "cpu", "gpu", "nvidia", "amd", "intel",
    "apple silicon", "ryzen", "core ultra", "snapdragon", "mediatek",
    "semiconductor", "tsmc", "arm", "risc-v",

    # Componentes
    "ram", "ddr5", "ssd", "almacenamiento", "monitor", "pantalla",
    "oled", "microled", "tarjeta grafica", "placa de video", "motherboard",
    "placa madre", "disco duro", "hdd", "fuente de poder", "cooler",
    "ventilador", "gabinete", "mouse", "teclado", "auriculares",

    # Dispositivos móviles
    "smartphone", "celular", "iphone", "samsung", "galaxy", "pixel",
    "xiaomi", "huawei", "oneplus", "oppo", "vivo", "realme", "motorola",
    "fold", "flip", "plegable", "tablet", "ipad", "smartwatch", "wearable",

    # Laptops y PCs
    "notebook", "laptop", "ultrabook", "chromebook", "surface", "macbook",
    "pc", "computadora", "escritorio", "all-in-one", "mini pc", "workstation",

    # Gaming
    "consola", "playstation", "ps5", "ps4", "xbox", "nintendo", "switch",
    "steam", "xbox game pass", "psn", "joystick", "control", "gaming",

    # Televisores y audio
    "smart tv", "televisor", "oled", "qled", "microled", "samsung tv",
    "sony tv", "lg tv", "home theater", "soundbar", "bocina", "altavoz",

    # Automoción y energía
    "auto electrico", "vehiculo electrico", "ev", "tesla", "bateria",
    "grafeno", "litio", "carga rapida", "autonomia", "conduccion autonoma",
]

# ----------------------------------------------------------------------
# 3. PALABRAS CLAVE DE IA
# ----------------------------------------------------------------------
AI_KEYWORDS = [
    "inteligencia artificial", "modelo de ia", "llm", "chatgpt", "gemini",
    "claude", "openai", "anthropic", "copilot", "gpt-", "modelo de lenguaje",
    "machine learning", "deep learning", "red neuronal", "redes neuronales",
    "ai", "ml", "dl", "nlp", "procesamiento de lenguaje natural",
    "visión por computadora", "computer vision", "reconocimiento facial",
    "generación de texto", "generación de imágenes", "stable diffusion",
    "midjourney", "dalle", "hugging face", "transformers", "bert",
    "tensorflow", "pytorch", "keras", "ia generativa", "agentes ia",
    "autogpt", "ai agents", "seguridad ia", "etica ia", "regulacion ia",
]

# ----------------------------------------------------------------------
# 4. PALABRAS CLAVE DE ARGENTINA (y tecnología local)
# ----------------------------------------------------------------------
ARGENTINA_KEYWORDS = [
    "argentina", "argentino", "argentina tech", "buenos aires",
    "mercado libre", "mercadolibre", "globant", "ualá", "uala",
    "satellogic", "auth0", "despegar", "tiendanube", "meli",
    "argentine", "startup argentina", "unicornio argentino",
    "ecosistema tech argentino", "programadores argentinos",
    "desarrolladores argentinos", "software argentino", "hardware argentino",
    "ciencia argentina", "tecnologia argentina", "innovacion argentina",
]

# ----------------------------------------------------------------------
# 5. PALABRAS CLAVE DE LANZAMIENTOS
# ----------------------------------------------------------------------
LAUNCH_KEYWORDS = [
    "lanza", "lanzamiento", "presenta", "presento", "anuncia", "anuncio",
    "debuta", "revela", "sale a la venta", "disponible desde", "estrena",
    "launches", "unveils", "announces", "introduces", "debuts", "reveals",
    "nuevo modelo", "nueva version", "nuevo producto", "ahora disponible",
    "ya disponible", "preventa", "reserva", "llegada", "arribo",
]

# ----------------------------------------------------------------------
# 6. PALABRAS DE PENALIZACIÓN (clickbait, contenido vacío)
# ----------------------------------------------------------------------
PENALTY_KEYWORDS = [
    "lo que tenés que saber", "imperdible", "no te pierdas", "resumen del día",
    "lo mejor de", "top 5", "top 10", "los mejores", "los peores",
    "opinión", "análisis subjetivo", "por qué", "vale la pena",
    "guía definitiva", "tutorial completo", "paso a paso",
]

# ======================================================================
# FUNCIONES DEL SISTEMA
# ======================================================================

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

def rank_with_gemini(candidatos):
    if not candidatos or len(candidatos) <= MAX_ITEMS_PER_RUN:
        return candidatos

    if not GEMINI_API_KEY:
        print("⚠️ Sin API Key, usando orden por reglas.")
        return candidatos

    print(f"🧠 Enviando {len(candidatos)} noticias a Gemini para ranking contextual (1 sola llamada)...")

    lista_texto = ""
    for idx, item in enumerate(candidatos, 1):
        lista_texto += f"{idx}. Título: {item['title']}\n   Resumen: {item['summary'][:250]}\n\n"

    prompt = f"""
    Eres un editor jefe de un blog de tecnología llamado tecno.ar, con audiencia argentina.
    Tu tarea es seleccionar las 8 noticias MÁS RELEVANTES Y CON CONTEXTO de la siguiente lista.

    Criterios de selección (en orden de prioridad):
    1. **Lanzamientos oficiales** de hardware (celulares, procesadores, GPU, etc.) o software (nuevos sistemas operativos, apps).
    2. **Avances reales en Inteligencia Artificial** (nuevos modelos, aplicaciones prácticas, regulaciones).
    3. **Tecnología argentina** o que impacte directamente en Argentina (Mercado Libre, startups locales, leyes).
    4. **Ciberseguridad** (ataques reales, vulnerabilidades críticas).
    5. **Ciencia y tecnología** (descubrimientos, avances espaciales, biotecnología).
    6. **Economía digital y startups** (financiaciones, adquisiciones, nuevas empresas).
    7. Ignora artículos de opinión, análisis retrospectivos ("Por qué X fracasó"), o rumores sin fuentes concretas.
    8. Ignora tutoriales, guías, "cómo hacer", o contenido educativo básico.

    Lista de noticias:
    {lista_texto}

    Debes devolver SOLO un JSON con los números de los 8 índices seleccionados, en orden de prioridad.
    Formato exacto: {{"seleccionados": [3, 7, 12, 15, 18, 22, 25, 30]}}
    """

    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "response_mime_type": "application/json",
            "temperature": 0.1
        }
    }

    try:
        resp = requests.post(GEMINI_URL, json=payload, timeout=60)
        if resp.status_code == 200:
            data = resp.json()
            raw_text = data["candidates"][0]["content"]["parts"][0]["text"]
            result = json.loads(raw_text)
            indices = result.get("seleccionados", [])
            
            if not indices:
                print("⚠️ Gemini no devolvió índices, usando orden por reglas.")
                return candidatos

            seleccionados = []
            for i in indices:
                if 1 <= i <= len(candidatos):
                    seleccionados.append(candidatos[i-1])
                if len(seleccionados) >= MAX_ITEMS_PER_RUN:
                    break
            
            print(f"✅ Gemini seleccionó {len(seleccionados)} noticias por contexto.")
            return seleccionados
        else:
            print(f"⚠️ Error en Gemini (ranking): {resp.status_code}, usando orden por reglas.")
            return candidatos
    except Exception as e:
        print(f"⚠️ Excepción en Gemini (ranking): {e}, usando orden por reglas.")
        return candidatos

MAX_POR_FUENTE = max(2, MAX_ITEMS_PER_RUN // 3)

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
# REDACCION Y PUBLICACION (sin cambios)
# ----------------------------------------------------------------------

def build_prompt(item):
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
   - Extension total: entre 600 y 900 palabras.

2. DENSIDAD DE KEYWORD:
   - El focus keyword (o variaciones naturales/sinonimos cercanos) debe aparecer
     entre el 1% y el 1.5% del total de palabras. NO fuerces repeticiones.

3. CONTENIDO Y CALIDAD:
   - NO copies frases textuales de la fuente; parafrasea completamente.
   - Agrega contexto, antecedentes o una perspectiva que no este en el resumen.
   - Evita frases genericas de relleno tipicas de IA.
   - Voz activa, tono profesional pero cercano (espanol rioplatense).

4. ENLACES:
   - Incluir al menos 1 sugerencia de ENLACE INTERNO marcada como:
     [ENLACE INTERNO SUGERIDO: nota relacionada sobre <tema>]
   - Incluir al menos 1 ENLACE EXTERNO real hacia la fuente original:
     [texto del enlace]({item['link']})

5. IMAGEN:
   - Al final, sugeri un ALT_TEXT para la imagen destacada, de 8-12 palabras.

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

def main():
    print("🚀 Iniciando pipeline Hybrid 2.0 con diccionarios completos...")
    items = fetch_new_relevant_items()
    print(f"Encontrados {len(items)} items nuevos para procesar.")

    try:
        from publish_to_wordpress import generate_and_publish
        tiene_wp = True
        print("✅ Integración con WordPress activada.")
    except ImportError:
        print("⚠️ No se encontró 'publish_to_wordpress'. Los borradores se guardarán LOCALMENTE.")
        tiene_wp = False

    for item in items:
        print(f"\nRedactando: {item['title'][:70]}...")
        try:
            prompt = build_prompt(item)
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
        time.sleep(8)

    print("\n✅ Pipeline finalizado.")

if __name__ == "__main__":
    main()
