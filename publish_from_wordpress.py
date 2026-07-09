#!/usr/bin/env python3
"""
Se dispara cuando publicás un artículo en WordPress (vía WP Webhooks -> Make -> GitHub Actions).
Toma el título y la imagen destacada del post ya publicado, genera el gráfico
de Instagram con el template fijo, y lo publica a través de Make.
"""

import os
import re
import html

from generate_instagram_post import generate_post_image
from publish_to_instagram import upload_image_to_imgbb, send_to_make_webhook

TITLE = os.environ.get("IG_TITLE", "").strip()
IMAGE_URL = os.environ.get("IG_IMAGE_URL", "").strip()
PERMALINK = os.environ.get("IG_PERMALINK", "").strip()
EXCERPT_RAW = os.environ.get("IG_EXCERPT", "").strip()


def slugify(text):
    text = re.sub(r"[^\w\s-]", "", text.lower())
    return re.sub(r"[\s_-]+", "-", text).strip("-")[:60]


def clean_excerpt(raw_html, max_chars=600):
    """El excerpt de WordPress suele venir con tags HTML y entidades; lo limpiamos."""
    text = re.sub(r"<[^>]+>", "", raw_html)
    text = html.unescape(text).strip()
    if len(text) > max_chars:
        text = text[:max_chars].rsplit(" ", 1)[0] + "…"
    return text


def build_caption_from_wp(title, excerpt, permalink):
    hashtags = "#Tecnologia #TecnoAR #Argentina #Innovacion #Noticias"
    partes = []
    if excerpt:
        partes.append(excerpt)
    partes.append("📲 Nota completa en el link de la bio.")
    partes.append(hashtags)
    return "\n\n".join(partes)


def main():
    if not TITLE or not IMAGE_URL:
        raise RuntimeError(
            f"Faltan datos del webhook. TITLE='{TITLE}' IMAGE_URL='{IMAGE_URL}'"
        )

    print(f"📄 Generando post de Instagram para: {TITLE[:70]}...")

    filename = f"{slugify(TITLE)}.png"
    image_path = generate_post_image(IMAGE_URL, TITLE, filename)

    excerpt = clean_excerpt(EXCERPT_RAW)
    caption = build_caption_from_wp(TITLE, excerpt, PERMALINK)

    public_url = upload_image_to_imgbb(image_path)
    send_to_make_webhook(public_url, caption)

    print("✅ Listo, aviso enviado a Make para publicar en Instagram.")


if __name__ == "__main__":
    main()
