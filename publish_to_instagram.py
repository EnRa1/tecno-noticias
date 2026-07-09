#!/usr/bin/env python3
"""
Sube la imagen generada a un hosting público gratis (imgbb) y
le avisa a Make (vía webhook) para que publique en Instagram.
Make maneja la conexión OAuth con Meta, así no tenemos que
gestionar tokens de acceso ni su renovación en el código.
"""

import os
import base64
import requests

IMGBB_API_KEY = os.environ.get("IMGBB_API_KEY")
MAKE_WEBHOOK_URL = os.environ.get("MAKE_WEBHOOK_URL")


def upload_image_to_imgbb(image_path):
    """Sube la imagen a imgbb (gratis, sin límite práctico) y devuelve la URL pública."""
    if not IMGBB_API_KEY:
        raise RuntimeError("Falta la variable de entorno IMGBB_API_KEY")

    with open(image_path, "rb") as f:
        image_b64 = base64.b64encode(f.read()).decode("utf-8")

    resp = requests.post(
        "https://api.imgbb.com/1/upload",
        data={"key": IMGBB_API_KEY, "image": image_b64},
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()

    if not data.get("success"):
        raise RuntimeError(f"Error subiendo a imgbb: {data}")

    url = data["data"]["url"]
    print(f"✅ Imagen subida a imgbb: {url}")
    return url


def build_caption(item, article_markdown, max_chars=800):
    """
    Arma el caption de Instagram: un fragmento del cuerpo redactado + CTA + hashtags.
    """
    lines = [l.strip() for l in article_markdown.splitlines() if l.strip()]
    parrafo = ""
    for line in lines:
        if line.startswith("#") or line.startswith("Fuente:"):
            continue
        parrafo = line
        break

    if len(parrafo) > max_chars:
        parrafo = parrafo[:max_chars].rsplit(" ", 1)[0] + "…"

    hashtags = "#Tecnologia #TecnoAR #Argentina #Innovacion #Noticias"

    return (
        f"{item['title']}\n\n"
        f"{parrafo}\n\n"
        f"📲 Nota completa en el link de la bio.\n\n"
        f"{hashtags}"
    )


def send_to_make_webhook(image_url, caption, retries=3):
    """
    Le avisa a Make que hay un post nuevo listo para publicar en Instagram.
    Make se encarga de la llamada real a la API de Meta.
    """
    if not MAKE_WEBHOOK_URL:
        raise RuntimeError("Falta la variable de entorno MAKE_WEBHOOK_URL")

    payload = {
        "image_url": image_url,
        "caption": caption,
    }

    for attempt in range(retries):
        resp = requests.post(MAKE_WEBHOOK_URL, json=payload, timeout=20)
        if resp.status_code == 200:
            print("🎉 Aviso enviado a Make, el post se publicará en Instagram en breve.")
            return True
        else:
            print(f"⚠️ Intento {attempt + 1}/{retries} falló: {resp.status_code} {resp.text}")

    raise RuntimeError("No se pudo notificar a Make tras varios intentos.")


def create_and_publish_instagram_post(item, article_markdown, image_path):
    """Función de conveniencia: sube la imagen y dispara el webhook de Make."""
    public_url = upload_image_to_imgbb(image_path)
    caption = build_caption(item, article_markdown)
    return send_to_make_webhook(public_url, caption)
