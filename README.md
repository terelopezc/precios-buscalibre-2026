# 📚 Buscalibre Price Scraper

Actualiza automáticamente los precios de tu planilla de Google Sheets cada 6 horas usando GitHub Actions y Playwright (navegador headless real).

---

## ¿Por qué Playwright y no IMPORTXML?

Buscalibre carga sus precios con JavaScript *después* de que llega el HTML — por eso `IMPORTXML` siempre da error. Playwright lanza un navegador Chrome real (sin pantalla), espera que cargue el precio, y lo extrae. Es la misma técnica que usan las extensiones de Chrome que rastrean precios de Buscalibre.

---

## Estructura del repositorio

```
buscalibre-scraper/
├── .github/
│   └── workflows/
│       └── scraper.yml      ← Automatización cada 6 horas
├── scraper.py               ← Script principal
├── requirements.txt         ← Dependencias Python
└── README.md
```

---

## Configuración paso a paso

### Paso 1 — Crear una Service Account en Google Cloud

1. Ve a [console.cloud.google.com](https://console.cloud.google.com)
2. Crea un proyecto nuevo (o usa uno existente)
3. Activa las APIs:
   - **Google Sheets API**
   - **Google Drive API**
4. Ve a **IAM y administración → Cuentas de servicio**
5. Crea una cuenta de servicio (el nombre es libre, ej. `buscalibre-scraper`)
6. En la cuenta creada, ve a **Claves → Agregar clave → JSON**
7. Descarga el archivo `.json` — lo necesitarás en el Paso 3

### Paso 2 — Compartir tu Google Sheet con la Service Account

1. Abre el archivo `.json` descargado y copia el valor de `"client_email"` (se ve como `algo@proyecto.iam.gserviceaccount.com`)
2. Abre tu Google Sheet
3. Haz clic en **Compartir** (arriba a la derecha)
4. Pega el email de la service account y dale permisos de **Editor**
5. Copia el **ID de tu Sheet** desde la URL:
   `https://docs.google.com/spreadsheets/d/`**`ESTE_ES_EL_ID`**`/edit`

### Paso 3 — Agregar los Secrets en GitHub

1. Ve a tu repositorio en GitHub
2. **Settings → Secrets and variables → Actions → New repository secret**
3. Agrega estos 3 secrets:

| Nombre | Valor |
|--------|-------|
| `SHEET_ID` | El ID de tu Google Sheet (del Paso 2) |
| `SHEET_NAME` | El nombre de la pestaña, ej. `Hoja 1` |
| `GOOGLE_CREDENTIALS` | **Todo el contenido** del archivo `.json` descargado (pégalo tal cual) |

### Paso 4 — Verificar las columnas en `scraper.py`

Abre `scraper.py` y verifica estos valores al inicio del archivo:

```python
COL_URL           = 542   # Columna TV — donde están los URLs
COL_PRECIO_ACTUAL = 541   # Columna TU — donde se escribirá el precio
COL_PRECIO_MENOR  = 540   # Columna TS — precio menor (referencia)
FIRST_DATA_ROW    = 6     # Primera fila con datos
```

Para saber el número de columna de una letra: A=1, B=2, … Z=26, AA=27, …
TV en tu planilla = columna 542. Puedes verificarlo en Sheets con: `=COLUMN(TV1)`

### Paso 5 — Hacer push del código a GitHub

```bash
git init
git add .
git commit -m "feat: buscalibre scraper inicial"
git remote add origin https://github.com/TU_USUARIO/buscalibre-scraper.git
git push -u origin main
```

### Paso 6 — Primera prueba manual

1. Ve a tu repositorio en GitHub
2. Pestaña **Actions**
3. Haz clic en **"📚 Actualizar precios Buscalibre"**
4. Botón **"Run workflow"** → **"Run workflow"**
5. Observa los logs en tiempo real

---

## Ajustes de rendimiento

En `scraper.py` puedes cambiar:

```python
MAX_WORKERS = 10   # Páginas paralelas. Más = más rápido pero más chance de bloqueo.
PAGE_TIMEOUT = 20  # Segundos de espera por página antes de marcar como "timeout"
```

Con 528 libros y `MAX_WORKERS=10` el proceso tarda aproximadamente **15-25 minutos**.

---

## ¿Qué pasa si Buscalibre bloquea el scraper?

El script ya incluye varias defensas:
- User-Agent real de Chrome
- Locale `es-CL`
- Bloqueo de imágenes/fuentes para parecer menos bot
- 3 métodos de extracción (CSS selector, JSON-LD, regex)

Si aun así hay muchos errores, puedes bajar `MAX_WORKERS` a 5 o agregar un delay aleatorio entre peticiones.

---

## Horario de ejecución

El workflow corre 4 veces al día (hora UTC):

| UTC | Chile (verano, UTC-3) | Chile (invierno, UTC-4) |
|-----|----------------------|------------------------|
| 00:00 | 21:00 (día anterior) | 20:00 (día anterior) |
| 06:00 | 03:00 | 02:00 |
| 12:00 | 09:00 | 08:00 |
| 18:00 | 15:00 | 14:00 |

Para cambiar la frecuencia edita la línea `cron:` en `.github/workflows/scraper.yml`.
