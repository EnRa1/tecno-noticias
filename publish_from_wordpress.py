import os
import sys

from generate_instagram_post import (
    generate_post_image,
    slugify,
)

from publish_to_instagram import (
    upload_image_to_imgbb,
    send_to_make_webhook,
    build_caption,
)


# ============================================================
# VARIABLES WORDPRESS
# ============================================================

IG_TITLE = os.getenv(
    "IG_TITLE",
    "",
).strip()

IG_IMAGE_URL = os.getenv(
    "IG_IMAGE_URL",
    "",
).strip()

IG_PERMALINK = os.getenv(
    "IG_PERMALINK",
    "",
).strip()

IG_EXCERPT = os.getenv(
    "IG_EXCERPT",
    "",
).strip()


# ============================================================
# LIMPIEZA
# ============================================================

def clean_excerpt(text):
    """
    Limpia HTML básico y espacios.
    """

    if not text:
        return ""

    import re

    # Eliminar etiquetas HTML
    text = re.sub(
        r"<[^>]+>",
        " ",
        text,
    )

    # Decodificación básica de entidades
    replacements = {
        "&nbsp;": " ",
        "&amp;": "&",
        "&quot;": '"',
        "&#039;": "'",
        "&lt;": "<",
        "&gt;": ">",
    }

    for old, new in replacements.items():

        text = text.replace(
            old,
            new,
        )

    # Normalizar espacios
    text = " ".join(
        text.split()
    )

    return text.strip()


# ============================================================
# CAPTION
# ============================================================

def build_caption_from_wp(
    title,
    excerpt,
    permalink,
):
    """
    Construye el caption desde WordPress.
    """

    excerpt = clean_excerpt(
        excerpt
    )

    return build_caption(
        title=title,
        excerpt=excerpt,
        permalink=permalink,
    )


# ============================================================
# VALIDACIÓN
# ============================================================

def validate_environment():

    errors = []

    if not IG_TITLE:
        errors.append(
            "IG_TITLE está vacío."
        )

    if not IG_IMAGE_URL:
        errors.append(
            "IG_IMAGE_URL está vacío."
        )

    if not os.getenv(
        "IMGBB_API_KEY"
    ):
        errors.append(
            "IMGBB_API_KEY no está configurada."
        )

    if not os.getenv(
        "MAKE_WEBHOOK_URL"
    ):
        errors.append(
            "MAKE_WEBHOOK_URL no está configurada."
        )

    if errors:

        print(
            "❌ Errores de configuración:"
        )

        for error in errors:
            print(
                f"   - {error}"
            )

        return False

    return True


# ============================================================
# MAIN
# ============================================================

def main():

    print(
        "=" * 60
    )

    print(
        "🚀 TECNO.AR — PUBLICACIÓN EN INSTAGRAM"
    )

    print(
        "=" * 60
    )

    print(
        f"📰 Título: {IG_TITLE}"
    )

    print(
        f"🔗 Imagen original: {IG_IMAGE_URL}"
    )

    if IG_PERMALINK:
        print(
            f"🔗 Artículo: {IG_PERMALINK}"
        )

    # --------------------------------------------------------
    # Validación
    # --------------------------------------------------------

    if not validate_environment():

        sys.exit(1)

    # --------------------------------------------------------
    # Nombre de archivo
    # --------------------------------------------------------

    filename = (
        f"{slugify(IG_TITLE)}.jpg"
    )

    output_path = os.path.join(
        "instagram_posts",
        filename,
    )

    # --------------------------------------------------------
    # Generar imagen
    # --------------------------------------------------------

    print(
        "\n🎨 Generando imagen..."
    )

    try:

        generated_path = generate_post_image(
            image_url=IG_IMAGE_URL,
            title=IG_TITLE,
            output_filename=output_path,
        )

    except Exception as error:

        print(
            "❌ Error generando la imagen:"
        )

        print(
            f"   {error}"
        )

        raise

    # --------------------------------------------------------
    # Verificar archivo
    # --------------------------------------------------------

    if not os.path.exists(
        generated_path
    ):

        raise FileNotFoundError(
            "La imagen final no fue generada."
        )

    file_size = (
        os.path.getsize(
            generated_path
        )
        / (1024 * 1024)
    )

    print(
        f"📦 Tamaño final: "
        f"{file_size:.2f} MB"
    )

    if file_size > 8:

        raise RuntimeError(
            "La imagen supera los 8 MB "
            "permitidos por Instagram."
        )

    # --------------------------------------------------------
    # Caption
    # --------------------------------------------------------

    clean = clean_excerpt(
        IG_EXCERPT
    )

    caption = build_caption_from_wp(
        title=IG_TITLE,
        excerpt=clean,
        permalink=IG_PERMALINK,
    )

    print(
        "\n📝 Caption:"
    )

    print(
        caption
    )

    # --------------------------------------------------------
    # ImgBB
    # --------------------------------------------------------

    print(
        "\n☁️ Subiendo JPEG a ImgBB..."
    )

    image_url = upload_image_to_imgbb(
        generated_path
    )

    # --------------------------------------------------------
    # Make
    # --------------------------------------------------------

    print(
        "\n📡 Enviando publicación a Make..."
    )

    send_to_make_webhook(
        image_url=image_url,
        caption=caption,
    )

    # --------------------------------------------------------
    # FIN
    # --------------------------------------------------------

    print(
        "\n" + "=" * 60
    )

    print(
        "✅ PUBLICACIÓN ENVIADA A MAKE"
    )

    print(
        "=" * 60
    )

    print(
        f"🖼️ URL: {image_url}"
    )

    print(
        f"📐 Formato: JPEG RGB 1080x1350"
    )

    print(
        "📱 Destino: Instagram"
    )

    print(
        "=" * 60
    )


if __name__ == "__main__":
    main()
