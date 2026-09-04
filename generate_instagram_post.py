#!/usr/bin/env python3
"""
Genera imágenes para Instagram con el estilo fijo de tecno.ar:
foto de portada + logo + cinta diagonal azul + caja de título + línea degradada.
Soporta cualquier formato de imagen de origen (JPG, PNG, WEBP, GIF, BMP,
TIFF, ICO, HEIC/HEIF, SVG) y compone correctamente los PNG con transparencia.
Lo único que cambia entre posts es la foto de fondo y el título.
"""

import os
import re
import io
import requests

from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

# Soporte HEIC / HEIF
try:
    import pillow_heif
    pillow_heif.register_heif_opener()
except ImportError:
    pass

# Soporte SVG
try:
    import cairosvg
    CAIROSVG_AVAILABLE = True
except ImportError:
    CAIROSVG_AVAILABLE = False


# ============================================================
# CONFIGURACIÓN — estilo fijo de tecno.ar
# ============================================================

BASE_DIR = Path(__file__).parent
ASSETS_DIR = BASE_DIR / "assets"
OUTPUT_DIR = BASE_DIR / "instagram_posts"

LOGO_PATH = ASSETS_DIR / "logo.png"
FONT_PATH = ASSETS_DIR / "fonts" / "Poppins-Bold.ttf"

CANVAS_W = 1080
CANVAS_H = 1350

PHOTO_H = 850  # alto de la zona de foto
TITLE_BOX_H = CANVAS_H - PHOTO_H

COLOR_NAVY = (20, 32, 68)         # texto del título
COLOR_BLUE_RIBBON = (30, 80, 210)
COLOR_WHITE = (255, 255, 255)

PADDING_X = 60

RIBBON_W = 380
RIBBON_H = 90

JPEG_QUALITY = 90
REQUEST_TIMEOUT = 30


def slugify(text):
    """Convierte un título en un nombre de archivo seguro."""
    text = str(text or "").strip().lower()
    text = re.sub(r"[^\w\s-]", "", text, flags=re.UNICODE)
    text = re.sub(r"[\s_-]+", "-", text)
    text = text.strip("-")
    return text[:100] or "instagram-post"


# ============================================================
# DESCARGA Y APERTURA UNIVERSAL DE IMAGEN
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
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/131.0 Safari/537.36"
        ),
        "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
    }

    print(f"🖼️ Descargando imagen: {image_url}")
    response = requests.get(
        image_url, headers=headers, timeout=REQUEST_TIMEOUT, allow_redirects=True
    )
    response.raise_for_status()

    if not response.content:
        raise ValueError("La respuesta de la imagen está vacía.")

    print(f"✅ Imagen descargada: {len(response.content) / 1024:.1f} KB")
    return response.content


def open_image_from_bytes(data):
    """
    Abre JPG, PNG, WEBP, GIF, BMP, TIFF, ICO, HEIC/HEIF, AVIF (si Pillow
    lo soporta) y SVG (vía CairoSVG). Devuelve la imagen tal cual la abrió
    Pillow (la conversión a RGBA se hace aparte, en normalize_to_rgba).
    """
    if not data:
        raise ValueError("No se recibieron datos de imagen.")

    try:
        image = Image.open(io.BytesIO(data))

        try:
            if getattr(image, "is_animated", False):
                image.seek(0)
        except Exception:
            pass

        image.load()

        print(
            f"✅ Formato detectado: {image.format or 'desconocido'} "
            f"{image.width}x{image.height}"
        )

        return image.copy()

    except Exception as pillow_error:

        if CAIROSVG_AVAILABLE:
            stripped = data.lstrip()
            is_svg = (
                stripped.startswith(b"<svg")
                or b"<svg" in stripped[:1000].lower()
                or b'xmlns="http://www.w3.org/2000/svg"' in stripped[:2000]
            )

            if is_svg:
                try:
                    print("🔄 Imagen SVG detectada. Convirtiendo...")
                    png_data = cairosvg.svg2png(bytestring=data, output_width=CANVAS_W)
                    image = Image.open(io.BytesIO(png_data))
                    image.load()
                    print("✅ SVG convertido correctamente.")
                    return image.copy()
                except Exception as svg_error:
                    raise ValueError(
                        f"No se pudo procesar el SVG: {svg_error}"
                    ) from svg_error

        raise ValueError(
            f"No se pudo abrir la imagen. Pillow informó: {pillow_error}"
        ) from pillow_error


def normalize_to_rgba(image):
    """
    Todo pasa a RGBA. Esto es lo que permite que, más adelante, la
    transparencia de un PNG se componga correctamente contra el fondo
    blanco en vez de perderse (quedando negra).
    """
    if image.mode != "RGBA":
        image = image.convert("RGBA")
    return image


# ============================================================
# COVER-RESIZE (como object-fit: cover), preservando transparencia
# ============================================================

def cover_resize(image, target_w, target_h):
    """Recorta y escala la imagen para que cubra el área target sin deformarse."""
    src_ratio = image.width / image.height
    target_ratio = target_w / target_h

    if src_ratio > target_ratio:
        new_h = target_h
        new_w = int(new_h * src_ratio)
    else:
        new_w = target_w
        new_h = int(new_w / src_ratio)

    image = image.resize((new_w, new_h), Image.Resampling.LANCZOS)

    left = (new_w - target_w) // 2
    top = (new_h - target_h) // 2

    return image.crop((left, top, left + target_w, top + target_h))


# ============================================================
# FUENTE
# ============================================================

def load_font(size):
    """
    Carga Poppins-Bold. Si no está disponible, corta la ejecución en vez
    de degradar en silencio a una fuente ilegible: mejor que falle el job
    a que se publique un título roto en Instagram.
    """
    if not FONT_PATH.exists():
        raise RuntimeError(
            f"No se encontró la fuente en {FONT_PATH}. Verificá que "
            "'assets/fonts/Poppins-Bold.ttf' exista en el repo con ese "
            "nombre exacto (Linux distingue mayúsculas de minúsculas)."
        )
    return ImageFont.truetype(str(FONT_PATH), size)


def wrap_text(draw, text, font, max_width):
    words = str(text or "").split()
    lines = []
    current = ""
    for word in words:
        trial = f"{current} {word}".strip()
        bbox = draw.textbbox((0, 0), trial, font=font)
        if bbox[2] - bbox[0] <= max_width:
            current = trial
        else:
            if current:
                lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines


def fit_title_font(draw, title, max_width, max_lines=4, start_size=64, min_size=36):
    """Reduce el tamaño de fuente hasta que el título entre en max_lines líneas."""
    size = start_size
    while size >= min_size:
        font = load_font(size)
        lines = wrap_text(draw, title, font, max_width)
        if len(lines) <= max_lines:
            return font, lines
        size -= 2

    # Si ni al tamaño mínimo entra, se trunca con "..."
    font = load_font(min_size)
    lines = wrap_text(draw, title, font, max_width)[:max_lines]
    if lines:
        lines[-1] = lines[-1].rstrip() + "…"
    return font, lines


# ============================================================
# ELEMENTOS DE DISEÑO
# ============================================================

def draw_diagonal_ribbon(canvas, y_start):
    """Cinta triangular azul que separa la foto del cuadro de texto."""
    draw = ImageDraw.Draw(canvas, "RGBA")
    points = [
        (0, y_start),
        (RIBBON_W, y_start),
        (0, y_start + RIBBON_H),
    ]
    draw.polygon(points, fill=COLOR_BLUE_RIBBON + (255,))


def draw_gradient_line(canvas, y):
    """Línea horizontal con degradado azul, cerca del borde inferior."""
    line_w = CANVAS_W - (PADDING_X * 2)
    line_h = 6

    gradient = Image.new("RGB", (line_w, line_h), COLOR_WHITE)
    draw = ImageDraw.Draw(gradient)

    start_color = (10, 20, 120)
    end_color = (120, 180, 255)

    for x in range(line_w):
        ratio = x / line_w
        r = int(start_color[0] + (end_color[0] - start_color[0]) * ratio)
        g = int(start_color[1] + (end_color[1] - start_color[1]) * ratio)
        b = int(start_color[2] + (end_color[2] - start_color[2]) * ratio)
        draw.line([(x, 0), (x, line_h)], fill=(r, g, b))

    canvas.paste(gradient, (PADDING_X, y))


# ============================================================
# GENERACIÓN DEL DISEÑO
# ============================================================

def generate_post_image(image_url, title, output_filename):
    """
    Genera la placa de Instagram de tecno.ar.

    Soporta cualquier formato de imagen de origen. El resultado final
    SIEMPRE es JPEG RGB 1080x1350 (JPEG y no PNG para mantener compatible
    el resto del pipeline: subida a imgbb + validación de tamaño en MB
    antes de mandarlo a Make).
    """
    print("🎨 Generando imagen para Instagram...")

    OUTPUT_DIR.mkdir(exist_ok=True)

    canvas = Image.new("RGB", (CANVAS_W, CANVAS_H), COLOR_WHITE)

    # --------------------------------------------------------
    # Foto de fondo
    # --------------------------------------------------------

    try:
        data = download_image(image_url)
        photo = open_image_from_bytes(data)
        photo = normalize_to_rgba(photo)
        photo = cover_resize(photo, CANVAS_W, PHOTO_H)
    except Exception as error:
        print(f"⚠️ No se pudo preparar la imagen de portada ({error}), usando fondo sólido.")
        photo = Image.new("RGBA", (CANVAS_W, PHOTO_H), (30, 30, 40, 255))

    # `photo` es RGBA: se pasa también como máscara, así el canal alfa se
    # compone contra el blanco del canvas y las zonas transparentes de un
    # PNG (fondo de una foto de producto, por ejemplo) quedan blancas en
    # vez de negras.
    canvas.paste(photo, (0, 0), photo)

    # Degradado oscuro suave en la base de la foto
    gradient_overlay = Image.new("L", (CANVAS_W, 200), 0)
    grad_draw = ImageDraw.Draw(gradient_overlay)
    for y in range(200):
        alpha = int(120 * (y / 200))
        grad_draw.line([(0, y), (CANVAS_W, y)], fill=alpha)
    dark_layer = Image.new("RGB", (CANVAS_W, 200), (0, 0, 0))
    canvas.paste(dark_layer, (0, PHOTO_H - 200), gradient_overlay)

    # --------------------------------------------------------
    # Logo arriba a la derecha
    # --------------------------------------------------------

    if LOGO_PATH.exists():
        try:
            logo = Image.open(LOGO_PATH).convert("RGBA")
            logo_w = 90
            logo_h = int(logo.height * (logo_w / logo.width))
            logo = logo.resize((logo_w, logo_h), Image.Resampling.LANCZOS)
            canvas.paste(logo, (CANVAS_W - logo_w - 40, 40), logo)
        except Exception as error:
            print(f"⚠️ No se pudo cargar el logo: {error}")
    else:
        print(f"⚠️ No existe el logo: {LOGO_PATH}")

    # --------------------------------------------------------
    # Cinta diagonal azul, en el borde entre foto y caja de texto
    # --------------------------------------------------------

    draw_diagonal_ribbon(canvas, PHOTO_H - 30)

    # --------------------------------------------------------
    # Título
    # --------------------------------------------------------

    draw = ImageDraw.Draw(canvas)
    max_text_width = CANVAS_W - (PADDING_X * 2)
    font, lines = fit_title_font(draw, title, max_text_width)

    line_height = int(font.size * 1.3)
    text_block_height = line_height * len(lines)
    text_y = PHOTO_H + ((TITLE_BOX_H - 60 - text_block_height) // 2)

    for i, line in enumerate(lines):
        draw.text((PADDING_X, text_y + i * line_height), line, font=font, fill=COLOR_NAVY)

    # --------------------------------------------------------
    # Línea con degradado, cerca del borde inferior
    # --------------------------------------------------------

    draw_gradient_line(canvas, CANVAS_H - 40)

    # --------------------------------------------------------
    # Guardar y validar SIEMPRE como JPEG RGB 1080x1350
    # --------------------------------------------------------

    filename_only = Path(output_filename).name
    root, _ = os.path.splitext(filename_only)
    output_path = OUTPUT_DIR / (root + ".jpg")

    canvas.save(
        output_path,
        format="JPEG",
        quality=JPEG_QUALITY,
        optimize=True,
        progressive=True,
    )

    with Image.open(output_path) as validation:
        if validation.format != "JPEG":
            raise RuntimeError("ERROR: el archivo generado no es JPEG.")
        if validation.mode != "RGB":
            raise RuntimeError("ERROR: el JPEG generado no está en RGB.")
        if validation.size != (CANVAS_W, CANVAS_H):
            raise RuntimeError("ERROR: dimensiones incorrectas.")

    file_size = os.path.getsize(output_path) / 1024

    print("✅ Imagen Instagram generada:")
    print(f"   Archivo: {output_path}")
    print(f"   Formato: JPEG RGB")
    print(f"   Dimensiones: {CANVAS_W}x{CANVAS_H}")
    print(f"   Tamaño: {file_size:.1f} KB")

    return output_path


# ============================================================
# EJECUCIÓN DIRECTA
# ============================================================

if __name__ == "__main__":
    import sys

    if len(sys.argv) < 3:
        print("Uso:")
        print('python generate_instagram_post.py "URL_IMAGEN" "TITULO"')
        sys.exit(1)

    image_url = sys.argv[1]
    title = sys.argv[2]
    filename = f"{slugify(title)}.jpg"

    generate_post_image(image_url, title, filename)
