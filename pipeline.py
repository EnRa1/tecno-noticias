#!/usr/bin/env python3
"""
Pipeline de automatizacion para tecno.ar
==========================================
Flujo: RSS -> deduplicacion -> filtro rapido por reglas -> filtro IA (Gemini)
       -> redaccion con Gemini -> borrador para revision humana

Requisitos:
    pip install feedparser requests google-genai --break-system-packages

Variables de entorno necesarias:
    GEMINI_API_KEY   -> obtenida gratis en https://aistudio.google.com/apikey

Uso:
    python pipeline.py

Salida:
    - drafts/YYYY-MM-DD_slug.md  (borrador por noticia, listo para revisar)
    - seen.json                  (registro de items ya procesados)
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
from google import genai  # <--- NUEVA DEPENDENCIA para Gemini

# ----------------------------------------------------------------------
# CONFIGURACION
# ----------------------------------------------------------------------

BASE_DIR = Path(__file__).parent
FEEDS_FILE = BASE_DIR / "feeds.txt"
SEEN_FILE = BASE_DIR / "seen.json"
DRAFTS_DIR = BASE_DIR / "drafts"

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
GEMINI_MODEL = "gemini-2.5-flash"  # modelo del tier gratuito
GEMINI_URL = (
    f"https://generativelanguage.googleapis.com/v1beta/models/"
    f"{GEMINI_MODEL}:generateContent?key={GEMINI_API_KEY}"
)

MAX_ITEMS_PER_RUN = 8
MIN_SOURCES_PER_TOPIC = 1

# Palabras clave para filtro inicial (rápido)
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
# SISTEMA DE SCORING POR REGLAS (EQUITATIVO, SIN SESGO EXAGERADO)
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
    """
    Filtro rápido: devuelve un score de 0 a 10 usando reglas.
    Ahora es EQUITATIVO: Argentina solo suma +1 si es lanzamiento.
    """
    text = entry_text.lower()
    is_launch = any(kw in text for kw in LAUNCH_KEYWORDS)

    score = 1  # base
    categorias = []

    # Hardware
    if any(kw in text for kw in HARDWARE_KEYWORDS):
        score += 3
        if is_launch:
            score += 2
        categorias.append("hardware")

    # IA
    if any(kw in text for kw in AI_KEYWORDS):
        score += 3
        if is_launch:
            score += 2
        categorias.append("ia")

    # Argentina (bonus simbólico de +1 SOLO si es lanzamiento)
    if is_launch and any(kw in text for kw in ARGENTINA_KEYWORDS):
        score += 1
        categorias.append("argentina")

    # Penalizaciones
    if any(kw in text for kw in PENALTY_KEYWORDS):
        score -= 2

    return max(0, min(10, score)), (categorias[0] if categorias else "general")

# ----------------------------------------------------------------------
# FILTRO CON GEMINI (IA) – NUEVO
# ----------------------------------------------------------------------

def score_news_with_gemini(candidatos):
    """
    Envía una lista de noticias a Gemini para que las puntúe y categorice.
    Retorna la misma lista con los campos 'score_ia' y 'categoria_ia'.
    """
    if not candidatos or not GEMINI_API_KEY:
        # Si no hay API key, devolvemos los mismos con score bajo para no romper
        for item in candidatos:
            item['score_ia'] = 1
            item['categoria_ia'] = 'general'
        return candidatos

    client = genai.Client(api_key=GEMINI_API_KEY)

    # Construir prompt con ejemplos (few-shot) y las noticias a evaluar
    prompt = """Eres un editor jefe de tecnología. Evalúa cada noticia con un score del 1 al 10 (10 = excelente) y una categoría: "hardware", "ia", "argentina", o "general".

Ejemplos:
Título: Apple presenta el iPhone 17 con chip A18
Resumen: Nuevo modelo con cámara mejorada.
-> {"titulo": "Apple presenta el iPhone 17 con chip A18", "score": 10, "categoria": "hardware"}

Título: Nueva IA de Google supera a GPT-4
Resumen: Modelo más rápido y eficiente.
-> {"titulo": "Nueva IA de Google supera a GPT-4", "score": 9, "categoria": "ia"}

Título: Opinión: ¿Vale la pena el iPhone 17?
Resumen: Análisis subjetivo del autor.
-> {"titulo": "Opinión: ¿Vale la pena el iPhone 17?", "score": 2, "categoria": "general"}

Ahora evalúa estas noticias. Devuelve SOLO un JSON con una lista de resultados. FORMATO EXACTO:
{"resultados": [{"titulo": "...", "score": 8, "categoria": "..."}, ...]}

Noticias:
"""
    for idx, item in enumerate(candidatos, 1):
        prompt += f"\n{idx}. Título: {item['title']}\n   Resumen: {item['summary'][:300]}\n"

    try:
        response = client.models.generate_content(
            model="gemini-2.0-flash",  # modelo rápido y gratuito
            contents=prompt,
            config={
                "response_mime_type": "application/json",
                "temperature": 0.2,
            }
        )

        data = json.loads(response.text)
        resultados = data.get("resultados", [])

        # Asignar scores a cada candidato
        for item in candidatos:
            item['score_ia'] = 1
            item['categoria_ia'] = 'general'
            for res in resultados:
                if res.get("titulo") == item['title']:
                    item['score_ia'] = res.get('score', 1)
                    item['categoria_ia'] = res.get('categoria', 'general')
                    break

    except Exception as e:
        print(f"⚠️ Error en Gemini (scoring): {e}")
        # Fallback: asignar score 1 a todos
        for item in candidatos:
            item['score_ia'] = 1
            item['categoria_ia'] = 'general'

    return candidatos

# ----------------------------------------------------------------------
# PASO 1: INGESTA + DEDUPLICACION + FILTRO RÁPIDO + FILTRO IA
# ----------------------------------------------------------------------

MAX_POR_FUENTE = max(2, MAX_ITEMS_PER_RUN // 3)  # tope de equidad por fuente (se mantiene)

def fetch_new_relevant_items():
    seen = load_seen()
    candidatos = []

    # 1. Obtener noticias de todos los feeds
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
            if not is_relevant(entry):
                continue

            texto_completo = entry.get("title", "") + " " + entry.get("summary", "")
            score_reglas, categoria_reglas = compute_relevance_score(texto_completo)

            # Filtro rápido: solo pasan los que tienen al menos 3 puntos en reglas
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
                # Estos los llenará Gemini después
                "score_ia": 0,
                "categoria_ia": "general",
            })

    print(f"📰 Noticias que pasaron el filtro rápido: {len(candidatos)}")

    if not candidatos:
        return []

    # 2. Limitar a 30 para no saturar el contexto de Gemini
    if len(candidatos) > 30:
        # Ordenar por score_reglas y tomar los 30 mejores
        candidatos.sort(key=lambda x: x["score_reglas"], reverse=True)
        candidatos = candidatos[:30]
        print("🔪 Limitando a 30 noticias para enviar a Gemini.")

    # 3. Filtro con Gemini (scoring por IA)
    candidatos_con_score = score_news_with_gemini(candidatos)

    # 4. Ordenar por score_ia (mayor a menor)
    candidatos_con_score.sort(key=lambda x: x.get("score_ia", 0), reverse=True)

    # 5. Seleccionar los mejores (hasta MAX_ITEMS_PER_RUN) con tope por fuente
    seleccionados = []
    conteo_por_fuente = {}

    for item in candidatos_con_score:
        if len(seleccionados) >= MAX_ITEMS_PER_RUN:
            break
        fuente = item["source"]
        if conteo_por_fuente.get(fuente, 0) >= MAX_POR_FUENTE:
            continue

        seleccionados.append(item)
        conteo_por_fuente[fuente] = conteo_por_fuente.get(fuente, 0) + 1
        # Guardar en seen (marcar como procesado)
        seen[item["hash"]] = {"title": item["title"], "date": datetime.now().isoformat()}

    save_seen(seen)

    # Mostrar resultados
    print("\n🏆 NOTICIAS SELECCIONADAS POR GEMINI:")
    for it in seleccionados:
        print(f"  [{it.get('score_ia', 0):>2}pts | {it.get('categoria_ia', 'general'):<9}] {it['title'][:70]}")

    return seleccionados

# ----------------------------------------------------------------------
# PASO 2: REDACCION CON GEMINI (tu lógica original, intacta)
# ----------------------------------------------------------------------

def build_prompt(item):
    """
    Prompt disenado para maximizar el SEO Score de Rank Math (apuntando a 85+),
    sin sacrificar calidad editorial real ni caer en keyword stuffing.
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

# ----------------------------------------------------------------------
# MAIN (VERSIÓN CORREGIDA CON WORDPRESS)
# ----------------------------------------------------------------------

def main():
    print("🚀 Iniciando pipeline con filtro IA (Gemini)...")
    items = fetch_new_relevant_items()
    print(f"Encontrados {len(items)} items nuevos para procesar.")

    # Intentar importar la función de publicación de WordPress
    try:
        from publish_to_wordpress import generate_and_publish
        tiene_wp = True
    except ImportError:
        print("⚠️ No se encontró 'publish_to_wordpress'. Los borradores se guardarán LOCALMENTE.")
        tiene_wp = False

    for item in items:
        print(f"\nRedactando: {item['title'][:70]}...")
        try:
            prompt = build_prompt(item)
            article = call_gemini(prompt)

            # --- Publicar en WordPress (si existe) ---
            if tiene_wp:
                try:
                    generate_and_publish(item, article)
                    print(f"✅ Borrador subido a WordPress: {item['title'][:50]}...")
                except Exception as wp_err:
                    print(f"⚠️ Error al subir a WP, guardando local: {wp_err}")
                    save_draft(item, article)  # Fallback local
            else:
                # Si no hay WP, guardamos local como siempre
                save_draft(item, article)

        except Exception as e:
            print(f"[ERROR] No se pudo procesar '{item['title']}': {e}")
        time.sleep(4)  # respeta el limite de RPM del free tier

    print("\n✅ Pipeline finalizado.")

if __name__ == "__main__":
    main()
