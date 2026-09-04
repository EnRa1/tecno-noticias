import os
import re
import io
import requests

from PIL import Image, ImageDraw, ImageFont, ImageOps

# Soporte HEIC / HEIF
try:
    import pillow_heif

    pillow_heif.register_heif_opener()
    HEIF_AVAILABLE = True
except ImportError:
    HEIF_AVAILABLE = False

# Soporte SVG
try:
    import cairosvg

    CAIROSVG_AVAILABLE = True
except ImportError:
    CAIROSVG_AVAILABLE = False


# ============================================================
# CONFIGURACIÓN
# ============================================================

INSTAGRAM_WIDTH = 1080
INSTAGRAM_HEIGHT = 1350

OUTPUT_DIR = "instagram_posts"

LOGO_PATH = os.path.join("assets", "logo.png")

FONT_PATH = os.path.join("assets", "Poppins-Bold.ttf")

JPEG_QUALITY = 90

REQUEST_TIMEOUT = 30


# ============================================================
# UTILIDADES
# ============================================================

def slugify(text):
    """
    Convierte un título en un nombre de archivo seguro.
    """

    text = str(text or "").strip().lower()

    text = re.sub(r"[^\w\s-]", "", text, flags=re.UNICODE)
    text = re.sub(r"[\s_-]+", "-", text)
    text = text.strip("-")

    return text[:100] or "instagram-post"


def ensure_output_dir():
    os.makedirs(OUTPUT_DIR, exist_ok=True)


# ============================================================
# DESCARGA DE IMAGEN
# ============================================================

def download_image(image_url):
    """
    Descarga una imagen desde una URL pública.

    Soporta redirecciones y distintos formatos.
    """

    if not image_url:
        raise ValueError("La URL de la imagen está vacía.")

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 "
            "(KHTML, like Gecko) "
            "Chrome/131.0 Safari/537.36"
        ),
        "Accept": (
            "image/avif,image/webp,image/apng,"
            "image/svg+xml,image/*,*/*;q=0.8"
        ),
    }

    print(f"🖼️ Descargando imagen: {image_url}")

    response = requests.get(
        image_url,
        headers=headers,
        timeout=REQUEST_TIMEOUT,
        allow_redirects=True,
    )

    response.raise_for_status()

    if not response.content:
        raise ValueError("La respuesta de la imagen está vacía.")

    print(
        f"✅ Imagen descargada: "
        f"{len(response.content) / 1024:.1f} KB"
    )

    return response.content


# ============================================================
# APERTURA UNIVERSAL DE IMAGEN
# ============================================================

def open_image_from_bytes(data):
    """
    Abre diferentes tipos de imágenes y devuelve una imagen PIL.

    Soporta:
    - JPG/JPEG
    - PNG
    - WEBP
    - GIF
    - BMP
    - TIFF
    - ICO
    - HEIC/HEIF
    - AVIF cuando Pillow lo soporte
    - SVG mediante CairoSVG
    """

    if not data:
        raise ValueError("No se recibieron datos de imagen.")

    # --------------------------------------------------------
    # Intento normal con Pillow
    # --------------------------------------------------------

    try:
        image = Image.open(io.BytesIO(data))

        # Si es animada, utilizamos el primer frame.
        try:
            if getattr(image, "is_animated", False):
                image.seek(0)
        except Exception:
            pass

        image.load()

        print(
            f"✅ Formato detectado por Pillow: "
            f"{image.format or 'desconocido'} "
            f"{image.width}x{image.height}"
        )

        return image.copy()

    except Exception as pillow_error:

        # ----------------------------------------------------
        # Intento SVG
        # ----------------------------------------------------

        if CAIROSVG_AVAILABLE:

            stripped = data.lstrip()

            is_svg = (
                stripped.startswith(b"<svg")
                or b"<svg" in stripped[:1000].lower()
                or b"xmlns=\"http://www.w3.org/2000/svg\"" in stripped[:2000]
            )

            if is_svg:

                try:

                    print("🔄 Imagen SVG detectada. Convirtiendo...")

                    png_data = cairosvg.svg2png(
                        bytestring=data,
                        output_width=INSTAGRAM_WIDTH,
                    )

                    image = Image.open(
                        io.BytesIO(png_data)
                    )

                    image.load()

                    print("✅ SVG convertido correctamente.")

                    return image.copy()

                except Exception as svg_error:
                    raise ValueError(
                        f"No se pudo procesar el SVG: {svg_error}"
                    ) from svg_error

        # ----------------------------------------------------
        # Error final
        # ----------------------------------------------------

        raise ValueError(
            "No se pudo abrir la imagen. "
            f"Pillow informó: {pillow_error}"
        ) from pillow_error


# ============================================================
# NORMALIZACIÓN
# ============================================================

def normalize_image(image):
    """
    Convierte la imagen a RGBA para poder trabajar
    correctamente con transparencias.
    """

    # Algunos formatos pueden tener modos especiales.
    if image.mode in ("P", "LA", "L", "CMYK", "I", "F"):
        image = image.convert("RGBA")

    elif image.mode == "RGB":
        image = image.convert("RGBA")

    elif image.mode == "RGBA":
        pass

    else:
        image = image.convert("RGBA")

    return image


# ============================================================
# AJUSTE DE IMAGEN PARA INSTAGRAM
# ============================================================

def crop_to_instagram_ratio(image):
    """
    Recorta la imagen al formato 4:5.
    """

    target_ratio = INSTAGRAM_WIDTH / INSTAGRAM_HEIGHT

    current_ratio = image.width / image.height

    if abs(current_ratio - target_ratio) < 0.001:
        return image

    if current_ratio > target_ratio:

        # Imagen demasiado ancha
        new_width = int(image.height * target_ratio)

        left = (image.width - new_width) // 2

        image = image.crop(
            (
                left,
                0,
                left + new_width,
                image.height,
            )
        )

    else:

        # Imagen demasiado alta
        new_height = int(image.width / target_ratio)

        top = (image.height - new_height) // 2

        image = image.crop(
            (
                0,
                top,
                image.width,
                top + new_height,
            )
        )

    return image


def prepare_background(image):
    """
    Prepara la fotografía para el diseño final.
    """

    image = normalize_image(image)

    image = crop_to_instagram_ratio(image)

    image = ImageOps.fit(
        image,
        (
            INSTAGRAM_WIDTH,
            INSTAGRAM_HEIGHT,
        ),
        method=Image.Resampling.LANCZOS,
        centering=(0.5, 0.5),
    )

    return image


# ============================================================
# FUENTE
# ============================================================

def load_font(size):
    """
    Carga Poppins-Bold.
    """

    if os.path.exists(FONT_PATH):

        try:
            return ImageFont.truetype(
                FONT_PATH,
                size=size,
            )
        except Exception as error:
            print(
                f"⚠️ No se pudo cargar Poppins-Bold: {error}"
            )

    print(
        "⚠️ No se encontró Poppins-Bold.ttf. "
        "Se utilizará una fuente alternativa."
    )

    return ImageFont.load_default()


# ============================================================
# AJUSTE AUTOMÁTICO DEL TÍTULO
# ============================================================

def wrap_text(draw, text, font, max_width):
    """
    Divide el título en líneas según el ancho disponible.
    """

    words = str(text or "").split()

    if not words:
        return ""

    lines = []
    current_line = ""

    for word in words:

        candidate = (
            f"{current_line} {word}".strip()
        )

        bbox = draw.textbbox(
            (0, 0),
            candidate,
            font=font,
        )

        width = bbox[2] - bbox[0]

        if width <= max_width:

            current_line = candidate

        else:

            if current_line:
                lines.append(current_line)

            current_line = word

    if current_line:
        lines.append(current_line)

    return "\n".join(lines)


def fit_title(draw, title, max_width, max_height):
    """
    Encuentra automáticamente un tamaño de fuente
    que permita colocar el título.
    """

    for size in range(64, 25, -2):

        font = load_font(size)

        wrapped = wrap_text(
            draw,
            title,
            font,
            max_width,
        )

        bbox = draw.multiline_textbbox(
            (0, 0),
            wrapped,
            font=font,
            spacing=8,
        )

        width = bbox[2] - bbox[0]
        height = bbox[3] - bbox[1]

        if (
            width <= max_width
            and height <= max_height
        ):
            return font, wrapped

    font = load_font(26)

    return (
        font,
        wrap_text(
            draw,
            title,
            font,
            max_width,
        ),
    )


# ============================================================
# GENERACIÓN DEL DISEÑO
# ============================================================

def generate_post_image(
    image_url,
    title,
    output_filename,
):
    """
    Genera la placa de Instagram de Tecno.ar.

    IMPORTANTE:
    independientemente del formato original,
    el resultado final SIEMPRE es JPEG RGB 1080x1350.
    """

    print("🎨 Generando imagen para Instagram...")

    # --------------------------------------------------------
    # Descargar
    # --------------------------------------------------------

    data = download_image(image_url)

    # --------------------------------------------------------
    # Abrir
    # --------------------------------------------------------

    source_image = open_image_from_bytes(data)

    # --------------------------------------------------------
    # Preparar fotografía
    # --------------------------------------------------------

    photo = prepare_background(source_image)

    # --------------------------------------------------------
    # Lienzo
    # --------------------------------------------------------

    canvas = Image.new(
        "RGB",
        (
            INSTAGRAM_WIDTH,
            INSTAGRAM_HEIGHT,
        ),
        "white",
    )

    photo_rgb = photo.convert("RGB")

    canvas.paste(
        photo_rgb,
        (0, 0),
    )

    draw = ImageDraw.Draw(canvas)

    # --------------------------------------------------------
    # Overlay suave sobre la parte inferior
    # --------------------------------------------------------

    overlay = Image.new(
        "RGBA",
        canvas.size,
        (0, 0, 0, 0),
    )

    overlay_draw = ImageDraw.Draw(overlay)

    overlay_draw.rectangle(
        (
            0,
            850,
            INSTAGRAM_WIDTH,
            INSTAGRAM_HEIGHT,
        ),
        fill=(0, 0, 0, 125),
    )

    canvas = Image.alpha_composite(
        canvas.convert("RGBA"),
        overlay,
    )

    draw = ImageDraw.Draw(canvas)

    # --------------------------------------------------------
    # Caja blanca del título
    # --------------------------------------------------------

    box_left = 55
    box_right = INSTAGRAM_WIDTH - 55
    box_top = 885
    box_bottom = 1265

    draw.rounded_rectangle(
        (
            box_left,
            box_top,
            box_right,
            box_bottom,
        ),
        radius=28,
        fill=(255, 255, 255, 245),
    )

    # --------------------------------------------------------
    # Cinta azul superior
    # --------------------------------------------------------

    ribbon_height = 16

    draw.rectangle(
        (
            box_left,
            box_top,
            box_right,
            box_top + ribbon_height,
        ),
        fill=(18, 91, 211, 255),
    )

    # --------------------------------------------------------
    # Título
    # --------------------------------------------------------

    title_max_width = (
        box_right - box_left - 70
    )

    title_max_height = 285

    font, wrapped_title = fit_title(
        draw,
        title,
        title_max_width,
        title_max_height,
    )

    title_x = box_left + 35
    title_y = box_top + 48

    draw.multiline_text(
        (
            title_x,
            title_y,
        ),
        wrapped_title,
        font=font,
        fill=(20, 27, 45, 255),
        spacing=8,
    )

    # --------------------------------------------------------
    # Logo
    # --------------------------------------------------------

    if os.path.exists(LOGO_PATH):

        try:

            logo = Image.open(
                LOGO_PATH
            ).convert("RGBA")

            # Tamaño máximo del logo
            max_logo_width = 240
            max_logo_height = 100

            logo.thumbnail(
                (
                    max_logo_width,
                    max_logo_height,
                ),
                Image.Resampling.LANCZOS,
            )

            logo_x = (
                INSTAGRAM_WIDTH
                - logo.width
                - 50
            )

            logo_y = 40

            canvas.alpha_composite(
                logo,
                (
                    logo_x,
                    logo_y,
                ),
            )

        except Exception as error:

            print(
                f"⚠️ No se pudo cargar el logo: {error}"
            )

    else:

        print(
            f"⚠️ No existe el logo: {LOGO_PATH}"
        )

    # --------------------------------------------------------
    # Línea inferior azul
    # --------------------------------------------------------

    draw = ImageDraw.Draw(canvas)

    draw.rectangle(
        (
            box_left,
            box_bottom - 12,
            box_right,
            box_bottom,
        ),
        fill=(18, 91, 211, 255),
    )

    # --------------------------------------------------------
    # Convertir SIEMPRE a RGB
    # --------------------------------------------------------

    final_image = canvas.convert("RGB")

    # --------------------------------------------------------
    # Asegurar 1080x1350
    # --------------------------------------------------------

    if final_image.size != (
        INSTAGRAM_WIDTH,
        INSTAGRAM_HEIGHT,
    ):

        final_image = final_image.resize(
            (
                INSTAGRAM_WIDTH,
                INSTAGRAM_HEIGHT,
            ),
            Image.Resampling.LANCZOS,
        )

    # --------------------------------------------------------
    # Asegurar extensión JPG
    # --------------------------------------------------------

    root, _ = os.path.splitext(
        output_filename
    )

    if not root:
        root = output_filename

    output_filename = (
        root + ".jpg"
    )

    # --------------------------------------------------------
    # Guardar JPEG REAL
    # --------------------------------------------------------

    ensure_output_dir()

    final_image.save(
        output_filename,
        format="JPEG",
        quality=JPEG_QUALITY,
        optimize=True,
        progressive=True,
    )

    # --------------------------------------------------------
    # Validación
    # --------------------------------------------------------

    with Image.open(
        output_filename
    ) as validation:

        if validation.format != "JPEG":
            raise RuntimeError(
                "ERROR: el archivo generado no es JPEG."
            )

        if validation.mode != "RGB":
            raise RuntimeError(
                "ERROR: el JPEG generado no está en RGB."
            )

        if validation.size != (
            INSTAGRAM_WIDTH,
            INSTAGRAM_HEIGHT,
        ):
            raise RuntimeError(
                "ERROR: dimensiones incorrectas."
            )

    file_size = (
        os.path.getsize(output_filename)
        / 1024
    )

    print(
        "✅ Imagen Instagram generada:"
    )

    print(
        f"   Archivo: {output_filename}"
    )

    print(
        f"   Formato: JPEG RGB"
    )

    print(
        f"   Dimensiones: "
        f"{INSTAGRAM_WIDTH}x{INSTAGRAM_HEIGHT}"
    )

    print(
        f"   Tamaño: {file_size:.1f} KB"
    )

    return output_filename


# ============================================================
# EJECUCIÓN DIRECTA
# ============================================================

if __name__ == "__main__":

    import sys

    if len(sys.argv) < 3:

        print(
            "Uso:"
        )

        print(
            "python generate_instagram_post.py "
            "\"URL_IMAGEN\" \"TITULO\""
        )

        sys.exit(1)

    image_url = sys.argv[1]
    title = sys.argv[2]

    filename = os.path.join(
        OUTPUT_DIR,
        f"{slugify(title)}.jpg",
    )

    generate_post_image(
        image_url,
        title,
        filename,
    )
