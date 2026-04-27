"""
Buscalibre.cl Price Scraper
============================
Lee URLs desde Google Sheets, extrae precios con Playwright (navegador headless),
y escribe los resultados de vuelta en la hoja.

Columnas esperadas en el Sheet (índice base 1):
  A  = Libro
  B  = Autor
  TV = URL  (columna 542 — ajusta COL_URL si difiere)
  TU = Precio Actual  (columna 541 — ajusta COL_PRECIO_ACTUAL si difiere)
  TS = Precio Menor   (columna 540 — ajusta COL_PRECIO_MENOR si difiere)
"""

import asyncio
import re
import os
import json
import logging
from datetime import datetime

import gspread
from google.oauth2.service_account import Credentials
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeout

# ── Configuración ──────────────────────────────────────────────────────────────

SHEET_ID       = os.environ["SHEET_ID"]          # ID del Google Sheet
SHEET_NAME     = os.environ.get("SHEET_NAME", "Hoja 1")  # Nombre de la pestaña
GOOGLE_CREDS   = os.environ["GOOGLE_CREDENTIALS"]  # JSON de service account

# Columnas (base 1). Revisa tu planilla y ajusta si es necesario.
# TV = columna 542, TU = 541, TS = 540
COL_URL           = 542   # Columna con la URL del libro
COL_PRECIO_ACTUAL = 541   # Columna "PRECIO ACTUAL" (TU)
COL_PRECIO_MENOR  = 540   # Columna "PRECIO MENOR" (TS)
FIRST_DATA_ROW    = 6     # Primera fila con datos (fila 6 según tu planilla)

# Scrapers paralelos simultáneos. Con 528 libros y 10 workers → ~15-20 min.
# Sube a 15 si quieres más velocidad, baja a 5 si hay muchos errores.
MAX_WORKERS = 10

# Tiempo máximo de espera por página (segundos)
PAGE_TIMEOUT = 20

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)


# ── Conexión a Google Sheets ───────────────────────────────────────────────────

def conectar_sheet():
    creds_dict = json.loads(GOOGLE_CREDS)
    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]
    creds = Credentials.from_service_account_info(creds_dict, scopes=scopes)
    gc    = gspread.authorize(creds)
    sh    = gc.open_by_key(SHEET_ID)
    ws    = sh.worksheet(SHEET_NAME)
    return ws


# ── Extracción de precio ───────────────────────────────────────────────────────

PRECIO_SELECTORS = [
    # Selector principal — precio de venta actual
    "span.precioFinalSpan",
    "span.precio-final",
    "p.precio",
    # Respaldo: datos estructurados JSON-LD en el <head>
    # (se maneja por separado en extraer_precio_jsonld)
]

def limpiar_precio(texto: str) -> int | None:
    """Convierte '$18.300' → 18300. Retorna None si falla."""
    if not texto:
        return None
    solo_numeros = re.sub(r"[^\d]", "", texto)
    return int(solo_numeros) if solo_numeros else None


async def extraer_precio_jsonld(page) -> int | None:
    """Intenta leer el precio desde JSON-LD estructurado en el <head>."""
    try:
        scripts = await page.locator('script[type="application/ld+json"]').all()
        for s in scripts:
            raw = await s.inner_text()
            data = json.loads(raw)
            # Puede ser un objeto o lista
            if isinstance(data, list):
                data = data[0]
            offers = data.get("offers", {})
            price  = offers.get("price") or data.get("price")
            if price:
                return int(float(str(price)))
    except Exception:
        pass
    return None


async def obtener_precio(browser, url: str, semaforo: asyncio.Semaphore) -> str:
    """
    Abre la URL en una pestaña nueva, espera el precio y lo retorna como string
    con formato '$18.300', o un mensaje de error.
    """
    async with semaforo:
        context = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            locale="es-CL",
        )
        page = await context.new_page()
        # Bloquear recursos innecesarios para ir más rápido
        await page.route(
            "**/*.{png,jpg,jpeg,gif,svg,woff,woff2,ttf,mp4,webp}",
            lambda r: r.abort(),
        )
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT * 1000)

            precio_int = None

            # 1. Intentar selectores CSS
            for selector in PRECIO_SELECTORS:
                try:
                    el = page.locator(selector).first
                    await el.wait_for(timeout=5000)
                    texto = await el.inner_text()
                    precio_int = limpiar_precio(texto)
                    if precio_int:
                        break
                except PlaywrightTimeout:
                    continue

            # 2. Fallback: JSON-LD
            if not precio_int:
                precio_int = await extraer_precio_jsonld(page)

            # 3. Fallback: regex en el HTML completo
            if not precio_int:
                html = await page.content()
                match = re.search(r'"price"\s*:\s*"?(\d{4,7})"?', html)
                if match:
                    precio_int = int(match.group(1))

            if precio_int:
                return f"${precio_int:,}".replace(",", ".")
            return "no encontrado"

        except PlaywrightTimeout:
            return "timeout"
        except Exception as e:
            return f"error: {type(e).__name__}"
        finally:
            await context.close()


# ── Lógica principal ───────────────────────────────────────────────────────────

async def main():
    log.info("Conectando a Google Sheets…")
    ws = conectar_sheet()

    log.info("Leyendo datos de la planilla…")
    all_values = ws.get_all_values()

    # Armar lista de (fila_real, url) para filas que tengan URL
    tareas = []
    for i, row in enumerate(all_values):
        fila_real = i + 1
        if fila_real < FIRST_DATA_ROW:
            continue
        url_idx = COL_URL - 1
        if url_idx >= len(row):
            continue
        url = row[url_idx].strip()
        if url.startswith("https://www.buscalibre"):
            tareas.append((fila_real, url))

    log.info(f"Se procesarán {len(tareas)} libros con {MAX_WORKERS} workers paralelos")

    semaforo  = asyncio.Semaphore(MAX_WORKERS)
    resultados = {}  # fila → precio_str

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)

        jobs = [
            obtener_precio(browser, url, semaforo)
            for _, url in tareas
        ]

        completados = 0
        for coro, (fila, url) in zip(
            asyncio.as_completed(jobs),
            # as_completed no preserva orden, así que paired diferente:
            # Usamos gather con return_exceptions para mantener orden:
            # (reemplazado abajo)
            tareas,
        ):
            pass  # placeholder — usamos gather en su lugar

        # Usar gather para mantener orden fila↔resultado
        precios = await asyncio.gather(
            *[obtener_precio(browser, url, semaforo) for _, url in tareas],
            return_exceptions=True,
        )

        await browser.close()

    # Emparejar resultados con filas
    for (fila, url), precio in zip(tareas, precios):
        if isinstance(precio, Exception):
            precio = f"error: {type(precio).__name__}"
        resultados[fila] = precio
        completados += 1
        if completados % 50 == 0:
            log.info(f"  Procesados {completados}/{len(tareas)}…")

    log.info(f"✅ Scraping terminado. Escribiendo {len(resultados)} precios en Sheets…")

    # Escribir en lote para minimizar llamadas a la API
    updates = []
    for fila, precio_str in resultados.items():
        col_letra_actual = gspread.utils.rowcol_to_a1(fila, COL_PRECIO_ACTUAL).replace(str(fila), "")
        updates.append({
            "range": f"{col_letra_actual}{fila}",
            "values": [[precio_str]],
        })

    # Dividir en lotes de 500 (límite de Sheets API)
    for i in range(0, len(updates), 500):
        lote = updates[i : i + 500]
        ws.batch_update(lote)
        log.info(f"  Lote {i//500 + 1} escrito ({len(lote)} celdas)")

    # Escribir timestamp en celda A1 (opcional)
    now = datetime.now().strftime("%d/%m/%Y %H:%M")
    ws.update("A4", [[f"Última actualización: {now}"]])

    log.info("🎉 ¡Todo listo!")


if __name__ == "__main__":
    asyncio.run(main())
