#!/usr/bin/env python3
"""
Genera imágenes para Instagram con el estilo fijo de tecno.ar:
foto de portada + logo + cinta diagonal azul + caja de título + línea degradada.
Lo único que cambia entre posts es la foto de fondo y el título.
"""

import io
import requests
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont, ImageFilter

BASE_DIR = Path(__file__).parent
ASSETS_DIR = BASE_DIR / "assets"
OUTPUT_DIR = BASE_DIR / "instagram_posts"

LOGO_PATH = ASSETS_DIR / "logo.png"
FONT_PATH = ASSETS_DIR / "fonts" / "Poppins-Bold.ttf"

# Lienzo formato post de feed (proporción 4:5, la que más espacio ocupa en el feed)
CANVAS_W = 1080
CANVAS_H = 1350

PHOTO_H = 850          # alto de la zona de foto
TITLE_BOX_H = CANVAS_H - PHOTO_H

COLOR_NAVY = (20, 32, 68)        # texto del título
COLOR_BLUE_RIBBON = (30, 80, 210)
COLOR_WHITE = (255, 255, 255)

PADDING_X = 60


def _download_image(url):
    resp = requests.get(url, timeout=20, headers={"User-Agent": "Mozilla/5.0"})
    resp.raise_for_status()
    return Image.open(io.BytesIO(resp.content)).convert("RGB")


def _cover_resize(img, target_w, target_h):
    """Recorta y escala la imagen para que cubra el área target sin deformarse (como object-fit: cover)."""
    src_ratio = img.width / img.height
    target_ratio = target_w / target_h

    if src_ratio > target_ratio:
        new_h = target_h
        new_w = int(new_h * src_ratio)
    else:
        new_w = target_w
        new_h = int(new_w / src_ratio)

    img = img.resize((new_w, new_h), Image.LANCZOS)
    left = (new_w - target_w) // 2
    top = (new_h - target_h) // 2
    return img.crop((left, top, left + target_w, top + target_h))


def _draw_diagonal_ribbon(canvas, y_start):
    """Dibuja la cinta triangular azul que separa la foto del cuadro de texto."""
    draw = ImageDraw.Draw(canvas, "RGBA")
    ribbon_w = 380
    ribbon_h = 90
    points = [
        (0, y_start),
        (ribbon_w, y_start),
        (0, y_start + ribbon_h),
    ]
    draw.polygon(points, fill=COLOR_BLUE_RIBBON + (255,))


def _draw_gradient_line(canvas, y):
    """Línea horizontal con degradado azul, como en el template original."""
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


def _fit_title_font(draw, title, max_width, max_lines=4, start_size=64, min_size=36):
    """Reduce el tamaño de fuente hasta que el título entre en max_lines líneas."""
    size = start_size
    while size >= min_size:
        font = ImageFont.truetype(str(FONT_PATH), size)
        lines = _wrap_text(draw, title, font, max_width)
        if len(lines) <= max_lines:
            return font, lines
        size -= 2
    # Si ni al tamaño mínimo entra, se trunca con "..."
    font = ImageFont.truetype(str(FONT_PATH), min_size)
    lines = _wrap_text(draw, title, font, max_width)[:max_lines]
    if lines:
        lines[-1] = lines[-1].rstrip() + "…"
    return font, lines


def _wrap_text(draw, text, font, max_width):
    words = text.split()
    lines = []
    current = ""
    for word in words:
        trial = (current + " " + word).strip()
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


def generate_post_image(image_url, title, output_filename):
    """
    Genera el post de Instagram con el estilo fijo de tecno.ar.
    image_url: URL de la foto de portada del artículo (viene de trafilatura, campo top_image).
    title: el H1 del artículo generado por Gemini.
    output_filename: nombre del archivo .png a guardar (sin ruta).
    Devuelve el Path al archivo generado.
    """
    OUTPUT_DIR.mkdir(exist_ok=True)

    canvas = Image.new("RGB", (CANVAS_W, CANVAS_H), COLOR_WHITE)

    # 1. Foto de fondo (cover-resize + leve oscurecido inferior para contraste)
    try:
        photo = _download_image(image_url)
        photo = _cover_resize(photo, CANVAS_W, PHOTO_H)
    except Exception as e:
        print(f"⚠️ No se pudo descargar la imagen de portada ({e}), usando fondo sólido.")
        photo = Image.new("RGB", (CANVAS_W, PHOTO_H), (30, 30, 40))

    canvas.paste(photo, (0, 0))

    # Degradado oscuro suave en la base de la foto, para que el logo/cinta se lean bien
    gradient_overlay = Image.new("L", (CANVAS_W, 200), 0)
    grad_draw = ImageDraw.Draw(gradient_overlay)
    for y in range(200):
        alpha = int(120 * (y / 200))
        grad_draw.line([(0, y), (CANVAS_W, y)], fill=alpha)
    dark_layer = Image.new("RGB", (CANVAS_W, 200), (0, 0, 0))
    canvas.paste(dark_layer, (0, PHOTO_H - 200), gradient_overlay)

    # 2. Logo arriba a la derecha
    if LOGO_PATH.exists():
        logo = Image.open(LOGO_PATH).convert("RGBA")
        logo_w = 90
        logo_h = int(logo.height * (logo_w / logo.width))
        logo = logo.resize((logo_w, logo_h), Image.LANCZOS)
        canvas.paste(logo, (CANVAS_W - logo_w - 40, 40), logo)
    else:
        print("⚠️ No se encontró assets/logo.png, se omite el logo.")

    # 3. Cinta diagonal azul, en el borde entre foto y caja de texto
    _draw_diagonal_ribbon(canvas, PHOTO_H - 30)

    # 4. Caja blanca de título
    draw = ImageDraw.Draw(canvas)
    max_text_width = CANVAS_W - (PADDING_X * 2)
    font, lines = _fit_title_font(draw, title, max_text_width)

    line_height = int(font.size * 1.3)
    text_block_height = line_height * len(lines)
    text_y = PHOTO_H + ((TITLE_BOX_H - 60 - text_block_height) // 2)

    for i, line in enumerate(lines):
        draw.text((PADDING_X, text_y + i * line_height), line, font=font, fill=COLOR_NAVY)

    # 5. Línea con degradado, cerca del borde inferior
    _draw_gradient_line(canvas, CANVAS_H - 40)

    output_path = OUTPUT_DIR / output_filename
    canvas.save(output_path, "PNG", quality=95)
    print(f"✅ Imagen de Instagram generada: {output_path}")
    return output_path
