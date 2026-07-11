"""
Publicador a WordPress para tecno.ar
======================================
Toma los borradores generados por pipeline.py (carpeta drafts/), los parsea,
descarga la imagen sugerida y los sube a WordPress como POSTS EN ESTADO
"draft" (borrador), con esa imagen como destacada.

IMPORTANTE: nunca publica directo. El estado siempre queda en "draft" para
que un editor humano revise, ajuste, y recien ahi le de "Publicar" desde el
propio WordPress.

Requisitos:
    pip install requests markdown --break-system-packages

Variables de entorno necesarias:
    WP_URL           -> ej: https://tecno.ar  (sin barra final)
    WP_USER           -> tu usuario de WordPress (no el email)
    WP_APP_PASSWORD   -> el Application Password de 24 caracteres

Uso:
    python publish_to_wordpress.py
"""

import os
import re
import json
import requests
import markdown as md
from pathlib import Path

BASE_DIR = Path(__file__).parent
DRAFTS_DIR = BASE_DIR / "drafts"
UPLOADED_FILE = BASE_DIR / "uploaded_to_wp.json"

WP_URL = os.environ.get("WP_URL", "").rstrip("/")
WP_USER = os.environ.get("WP_USER")
WP_APP_PASSWORD = os.environ.get("WP_APP_PASSWORD")


# ----------------------------------------------------------------------
# UTILIDADES
# ----------------------------------------------------------------------

def load_uploaded():
    if UPLOADED_FILE.exists():
        return json.loads(UPLOADED_FILE.read_text(encoding="utf-8"))
    return {}


def save_uploaded(uploaded):
    UPLOADED_FILE.write_text(json.dumps(uploaded, ensure_ascii=False, indent=2), encoding="utf-8")


def slugify(text):
    text = re.sub(r"[^\w\s-]", "", text.lower())
    return re.sub(r"[\s_-]+", "-", text).strip("-")[:60]


def extract_field(text, field_name, next_fields):
    """
    Extrae el contenido entre '## FIELD_NAME' y el siguiente '## ' conocido.
    """
    pattern = rf"## {field_name}\s*\n(.*?)(?:\n## (?:{'|'.join(next_fields)})|\Z)"
    match = re.search(pattern, text, re.DOTALL)
    return match.group(1).strip() if match else ""


def parse_draft(md_text):
    """
    Parsea el borrador generado por pipeline.py, que tiene esta estructura:
    ## FOCUS_KEYWORD / ## SEO_TITLE / ## SLUG / ## META_DESCRIPTION /
    ## H1 / ## ARTICULO / ## ALT_TEXT
    """
    fields_order = [
        "FOCUS_KEYWORD", "SEO_TITLE", "SLUG", "META_DESCRIPTION",
        "H1", "ARTICULO", "ALT_TEXT",
    ]

    parsed = {}
    for i, field in enumerate(fields_order):
        remaining = fields_order[i + 1:]
        parsed[field] = extract_field(md_text, field, remaining)

    return parsed


# ----------------------------------------------------------------------
# WORDPRESS API — IMAGEN
# ----------------------------------------------------------------------

def upload_image_to_wp(image_url, alt_text, filename_hint="imagen"):
    """
    Descarga una imagen desde una URL y la sube a la biblioteca de medios
    de WordPress. Devuelve el media ID, o None si falla o no hay URL.
    """
    if not image_url:
        print("  ℹ️ No hay URL de imagen sugerida, se omite la subida de imagen.")
        return None

    auth = (WP_USER, WP_APP_PASSWORD)
    try:
        img_resp = requests.get(
            image_url, timeout=20,
            headers={"User-Agent": "Mozilla/5.0 (compatible; tecno.ar-bot)"},
        )
        img_resp.raise_for_status()

        content_type = img_resp.headers.get("Content-Type", "image/jpeg").split(";")[0].strip()
        ext_map = {
            "image/jpeg": "jpg", "image/jpg": "jpg",
            "image/png": "png", "image/webp": "webp",
        }
        ext = ext_map.get(content_type, "jpg")
        filename = f"{slugify(filename_hint) or 'imagen'}.{ext}"

        media_resp = requests.post(
            f"{WP_URL}/wp-json/wp/v2/media",
            data=img_resp.content,
            auth=auth,
            headers={
                "Content-Disposition": f"attachment; filename={filename}",
                "Content-Type": content_type,
            },
            timeout=30,
        )

        if media_resp.status_code in (200, 201):
            media_id = media_resp.json()["id"]
            print(f"  ✅ Imagen subida a WP (media ID {media_id})")
            if alt_text:
                requests.post(
                    f"{WP_URL}/wp-json/wp/v2/media/{media_id}",
                    json={"alt_text": alt_text},
                    auth=auth, timeout=15,
                )
            return media_id

        print(f"  ⚠️ Error subiendo imagen a WP: {media_resp.status_code} {media_resp.text[:200]}")
    except Exception as e:
        print(f"  ⚠️ Excepción subiendo imagen: {e}")

    return None


# ----------------------------------------------------------------------
# WORDPRESS API — TAGS Y POST
# ----------------------------------------------------------------------

def get_or_create_tag(auth, tag_name):
    """Busca un tag existente o lo crea. Devuelve su ID."""
    resp = requests.get(
        f"{WP_URL}/wp-json/wp/v2/tags",
        params={"search": tag_name},
        auth=auth, timeout=30,
    )
    if resp.ok and resp.json():
        return resp.json()[0]["id"]

    resp = requests.post(
        f"{WP_URL}/wp-json/wp/v2/tags",
        json={"name": tag_name},
        auth=auth, timeout=30,
    )
    if resp.ok:
        return resp.json()["id"]
    return None


def create_draft_post(fields, source_url, featured_media=None):
    auth = (WP_USER, WP_APP_PASSWORD)

    html_content = md.markdown(fields["ARTICULO"])

    review_note = (
        f'<p style="background:#fff3cd;border:1px solid #ffc107;padding:10px;">'
        f"<strong>Borrador generado automaticamente.</strong> Revisar antes de publicar: "
        f"reemplazar el enlace interno sugerido, confirmar la imagen destacada, "
        f"y verificar el focus keyword en Rank Math.<br>"
        f"<strong>Focus keyword sugerido:</strong> {fields['FOCUS_KEYWORD']}<br>"
        f"<strong>Alt text sugerido para la imagen:</strong> {fields['ALT_TEXT']}<br>"
        f'<strong>Fuente original:</strong> <a href="{source_url}">{source_url}</a>'
        f"</p>\n"
    )

    payload = {
        "title": fields["H1"] or fields["SEO_TITLE"],
        "slug": fields["SLUG"],
        "excerpt": fields["META_DESCRIPTION"],
        "content": review_note + html_content,
        "status": "draft",
    }

    if featured_media:
        payload["featured_media"] = featured_media

    # Intento best-effort de setear los campos nativos de Rank Math.
    # Esto SOLO funciona si tu instalacion de Rank Math expone esos meta
    # keys via REST. Si no funciona, no rompe nada: el post igual se crea.
    payload["meta"] = {
        "rank_math_focus_keyword": fields["FOCUS_KEYWORD"],
        "rank_math_description": fields["META_DESCRIPTION"],
        "rank_math_title": fields["SEO_TITLE"],
    }

    resp = requests.post(
        f"{WP_URL}/wp-json/wp/v2/posts",
        json=payload,
        auth=auth,
        timeout=30,
    )

    if resp.status_code not in (200, 201):
        raise RuntimeError(f"Error {resp.status_code} al crear el post: {resp.text[:500]}")

    return resp.json()


# ----------------------------------------------------------------------
# PUNTO DE ENTRADA REUTILIZABLE (por si otro script quiere llamarlo directo)
# ----------------------------------------------------------------------

def generate_and_publish(item, article_md, imagen_url=None):
    """
    Parsea el markdown de un articulo ya redactado, sube la imagen (si hay)
    y crea el post como borrador en WordPress. item debe tener al menos 'link'.
    """
    if not all([WP_URL, WP_USER, WP_APP_PASSWORD]):
        raise RuntimeError("Faltan variables de entorno: WP_URL, WP_USER, WP_APP_PASSWORD")

    fields = parse_draft(article_md)
    if not fields.get("ARTICULO"):
        raise RuntimeError("No se pudo parsear el contenido generado por Gemini")

    media_id = upload_image_to_wp(
        imagen_url, fields.get("ALT_TEXT", ""), filename_hint=fields.get("SLUG", "imagen")
    )

    return create_draft_post(fields, item["link"], featured_media=media_id)


# ----------------------------------------------------------------------
# MAIN — lee los borradores locales de drafts/ y los sube
# ----------------------------------------------------------------------

def main():
    if not all([WP_URL, WP_USER, WP_APP_PASSWORD]):
        raise RuntimeError(
            "Faltan variables de entorno: WP_URL, WP_USER, WP_APP_PASSWORD"
        )

    uploaded = load_uploaded()
    draft_files = sorted(DRAFTS_DIR.glob("*.md")) if DRAFTS_DIR.exists() else []

    if not draft_files:
        print("No hay borradores en drafts/ para subir.")
        return

    subidos = 0
    for path in draft_files:
        key = path.name
        if key in uploaded:
            continue

        text = path.read_text(encoding="utf-8")

        # Extraer la URL de la fuente original y la imagen sugerida
        # desde el comentario HTML inicial que escribe pipeline.py
        source_match = re.search(r"Fuente original: (\S+)", text)
        source_url = source_match.group(1) if source_match else ""

        imagen_match = re.search(r"Imagen sugerida: (\S+)", text)
        imagen_url = imagen_match.group(1) if imagen_match else None

        fields = parse_draft(text)

        if not fields.get("ARTICULO"):
            print(f"[SKIP] {key}: no se pudo parsear el contenido, formato inesperado.")
            continue

        try:
            print(f"📤 Subiendo: {key}")
            media_id = upload_image_to_wp(
                imagen_url, fields.get("ALT_TEXT", ""), filename_hint=fields.get("SLUG", key)
            )
            result = create_draft_post(fields, source_url, featured_media=media_id)
            print(f"[OK] Subido como borrador: {result.get('link', result.get('id'))}")
            uploaded[key] = {"wp_id": result.get("id"), "wp_link": result.get("link")}
            subidos += 1
        except Exception as e:
            print(f"[ERROR] {key}: {e}")

    save_uploaded(uploaded)
    print(f"\nListo. {subidos} borrador(es) nuevo(s) subido(s) a WordPress como draft.")


if __name__ == "__main__":
    main()
