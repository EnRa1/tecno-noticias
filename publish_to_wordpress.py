"""
Publicador a WordPress para tecno.ar
======================================
Toma los borradores generados por pipeline.py (carpeta drafts/), los parsea,
y los sube a WordPress como POSTS EN ESTADO "draft" (borrador).

IMPORTANTE: nunca publica directo. El estado siempre queda en "draft" para
que un editor humano revise, ajuste, agregue la imagen destacada y el enlace
interno real, y recien ahi le de "Publicar" desde el propio WordPress.

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
# WORDPRESS API
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


def create_draft_post(fields, source_url):
    auth = (WP_USER, WP_APP_PASSWORD)

    html_content = md.markdown(fields["ARTICULO"])

    # Nota de revision al principio del contenido, bien visible para el editor.
    review_note = (
        f'<p style="background:#fff3cd;border:1px solid #ffc107;padding:10px;">'
        f"<strong>Borrador generado automaticamente.</strong> Revisar antes de publicar: "
        f"reemplazar el enlace interno sugerido, agregar imagen destacada con el "
        f"ALT_TEXT sugerido, y verificar el focus keyword en Rank Math.<br>"
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

    # Intento best-effort de setear los campos nativos de Rank Math.
    # Esto SOLO funciona si tu instalacion de Rank Math expone esos meta
    # keys via REST (no todas las versiones lo hacen por defecto). Si no
    # funciona, no rompe nada: el post igual se crea, solo sin esos metadatos
    # precargados, y los completas a mano en el editor (30 segundos).
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
# MAIN
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

        # Extraer la URL de la fuente original desde el comentario HTML inicial
        source_match = re.search(r"Fuente original: (\S+)", text)
        source_url = source_match.group(1) if source_match else ""

        fields = parse_draft(text)

        if not fields.get("ARTICULO"):
            print(f"[SKIP] {key}: no se pudo parsear el contenido, formato inesperado.")
            continue

        try:
            result = create_draft_post(fields, source_url)
            print(f"[OK] Subido como borrador: {result.get('link', result.get('id'))}")
            uploaded[key] = {"wp_id": result.get("id"), "wp_link": result.get("link")}
            subidos += 1
        except Exception as e:
            print(f"[ERROR] {key}: {e}")

    save_uploaded(uploaded)
    print(f"\nListo. {subidos} borrador(es) nuevo(s) subido(s) a WordPress como draft.")


if __name__ == "__main__":
    main()
