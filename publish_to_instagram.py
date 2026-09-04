import base64
import os
import sys
import time

import requests


IMGBB_API_URL = "https://api.imgbb.com/1/upload"

REQUEST_TIMEOUT = 60


# ============================================================
# IMG.BB
# ============================================================

def upload_image_to_imgbb(image_path):
    """
    Sube el JPEG generado a ImgBB.

    El archivo enviado aquí debe ser JPEG.
    """

    api_key = os.getenv("IMGBB_API_KEY")

    if not api_key:
        raise RuntimeError(
            "Falta la variable de entorno IMGBB_API_KEY."
        )

    if not os.path.exists(image_path):
        raise FileNotFoundError(
            f"No existe la imagen: {image_path}"
        )

    print(
        f"☁️ Subiendo imagen a ImgBB: {image_path}"
    )

    with open(
        image_path,
        "rb",
    ) as file:

        encoded = base64.b64encode(
            file.read()
        ).decode("utf-8")

    response = requests.post(
        IMGBB_API_URL,
        params={
            "key": api_key,
        },
        data={
            "image": encoded,
        },
        timeout=REQUEST_TIMEOUT,
    )

    if response.status_code != 200:

        raise RuntimeError(
            "Error ImgBB "
            f"{response.status_code}: "
            f"{response.text[:500]}"
        )

    payload = response.json()

    if not payload.get("success"):

        raise RuntimeError(
            "ImgBB rechazó la imagen: "
            f"{payload}"
        )

    data = payload.get("data", {})

    image_url = data.get("url")

    if not image_url:

        raise RuntimeError(
            "ImgBB no devolvió una URL pública."
        )

    print(
        f"✅ Imagen alojada: {image_url}"
    )

    return image_url


# ============================================================
# CAPTION
# ============================================================

def build_caption(
    title,
    excerpt,
    permalink=None,
    max_chars=2200,
):
    """
    Construye el texto para Instagram.
    """

    title = str(title or "").strip()
    excerpt = str(excerpt or "").strip()

    # Limpiar saltos excesivos
    excerpt = " ".join(
        excerpt.split()
    )

    caption_parts = []

    if title:
        caption_parts.append(
            title
        )

    if excerpt:
        caption_parts.append(
            excerpt
        )

    caption_parts.append(
        "📲 Nota completa en el link de la bio."
    )

    caption_parts.append(
        "#Tecnologia #TecnoAR #Argentina "
        "#Innovacion #Noticias"
    )

    caption = "\n\n".join(
        caption_parts
    )

    # Instagram permite hasta 2200 caracteres.
    if len(caption) > max_chars:

        # Conservamos título + CTA + hashtags
        suffix = (
            "\n\n📲 Nota completa en el link de la bio."
            "\n\n#Tecnologia #TecnoAR #Argentina "
            "#Innovacion #Noticias"
        )

        available = (
            max_chars
            - len(title)
            - len(suffix)
            - 2
        )

        if available > 50:

            excerpt = excerpt[:available].rstrip()

            caption = (
                f"{title}\n\n"
                f"{excerpt}"
                f"{suffix}"
            )

        else:

            caption = (
                f"{title}"
                f"{suffix}"
            )

    return caption[:max_chars]


# ============================================================
# MAKE
# ============================================================

def send_to_make_webhook(
    image_url,
    caption,
    retries=3,
):
    """
    Envía la URL pública del JPEG y el caption
    al webhook de Make.
    """

    webhook_url = os.getenv(
        "MAKE_WEBHOOK_URL"
    )

    if not webhook_url:

        raise RuntimeError(
            "Falta la variable MAKE_WEBHOOK_URL."
        )

    payload = {
        "image_url": image_url,
        "caption": caption,
    }

    headers = {
        "Content-Type": "application/json",
        "User-Agent": "TecnoAR-Instagram/1.0",
    }

    last_error = None

    for attempt in range(
        1,
        retries + 1,
    ):

        try:

            print(
                f"📡 Enviando a Make "
                f"(intento {attempt}/{retries})..."
            )

            response = requests.post(
                webhook_url,
                json=payload,
                headers=headers,
                timeout=60,
            )

            if 200 <= response.status_code < 300:

                print(
                    "✅ Make recibió correctamente "
                    "la publicación."
                )

                return True

            last_error = RuntimeError(
                f"Make respondió "
                f"{response.status_code}: "
                f"{response.text[:500]}"
            )

            print(
                f"⚠️ {last_error}"
            )

        except Exception as error:

            last_error = error

            print(
                f"⚠️ Error enviando a Make: "
                f"{error}"
            )

        if attempt < retries:

            time.sleep(
                5 * attempt
            )

    raise RuntimeError(
        f"No se pudo enviar a Make: "
        f"{last_error}"
    )


# ============================================================
# FLUJO COMPLETO
# ============================================================

def create_and_publish_instagram_post(
    image_path,
    title,
    excerpt,
    permalink=None,
):
    """
    Flujo:

    JPEG → ImgBB → Make → Instagram
    """

    if not image_path:
        raise ValueError(
            "No se recibió image_path."
        )

    if not title:
        raise ValueError(
            "No se recibió título."
        )

    image_url = upload_image_to_imgbb(
        image_path
    )

    caption = build_caption(
        title=title,
        excerpt=excerpt,
        permalink=permalink,
    )

    send_to_make_webhook(
        image_url=image_url,
        caption=caption,
    )

    print(
        "🎉 Publicación enviada correctamente."
    )

    return {
        "image_url": image_url,
        "caption": caption,
    }


# ============================================================
# CLI
# ============================================================

if __name__ == "__main__":

    if len(sys.argv) < 4:

        print(
            "Uso:"
        )

        print(
            "python publish_to_instagram.py "
            "imagen.jpg \"Título\" \"Extracto\""
        )

        sys.exit(1)

    image_path = sys.argv[1]
    title = sys.argv[2]
    excerpt = sys.argv[3]

    permalink = (
        sys.argv[4]
        if len(sys.argv) > 4
        else None
    )

    create_and_publish_instagram_post(
        image_path=image_path,
        title=title,
        excerpt=excerpt,
        permalink=permalink,
    )
