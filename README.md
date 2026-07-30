# pokestock-tcg-bot

Bot que vigila fichas de producto en Amazon.es (Pokémon TCG / Kayou Collect 151) y,
cuando un producto pasa de "sin stock" a "disponible", envía un mensaje al grupo de
Telegram **PokéStock TCG** con el enlace de afiliado de Amazon insertado.

## Cómo funciona

`check_stock.py` recorre `products.json`, descarga cada ficha con un user-agent
rotativo, y decide si el producto está disponible buscando el botón de compra y
descartando frases de no disponibilidad ("no disponible", "no hay existencias", etc.).
El resultado se compara contra `state.json` (estado de la ejecución anterior); si un
producto pasa de no disponible a disponible, se envía un mensaje de Telegram con la
URL + `?tag=<affiliate_tag>`. El nuevo estado se guarda en `state.json` y GitHub
Actions lo commitea de vuelta al repo.

## Configuración

### 1. Secrets de GitHub

En `Settings` → `Secrets and variables` → `Actions` → `New repository secret`:

- `TELEGRAM_BOT_TOKEN` — token del bot de Telegram (`PokeStockTCG_bot`)
- `TELEGRAM_CHAT_ID` — id del grupo, en este caso `-1004397926701`

⚠️ Si un token anterior quedó expuesto en algún momento, revócalo desde
@BotFather → `/mybots` → seleccionar el bot → `Revoke current token` antes de
generar y usar uno nuevo.

### 2. `products.json`

Sustituye los productos de ejemplo por los reales:

```json
[
  {
    "name": "Nombre del producto",
    "url": "https://www.amazon.es/dp/XXXXXXXXXX",
    "affiliate_tag": "tuTagAfiliado-21"
  }
]
```

### 3. Activar el workflow

En la pestaña **Actions** del repo, lanza `Check Amazon Stock` manualmente con
"Run workflow" para probar antes de esperar al cron (por defecto cada 15 minutos,
ajustable en `.github/workflows/check_stock.yml` cambiando la expresión cron).

### 4. Instalación local (opcional, para probar fuera de Actions)

```bash
pip install -r requirements.txt
export TELEGRAM_BOT_TOKEN=xxxx
export TELEGRAM_CHAT_ID=-1004397926701
python check_stock.py
```

## Notas

- **Bloqueos de Amazon**: hacer scraping frecuente puede provocar bloqueos de IP.
  El script mitiga esto con rotación de user-agent y pausas aleatorias entre
  peticiones, pero si los bloqueos son recurrentes, considera bajar la frecuencia
  del cron.
- **Alternativa Keepa API**: si el scraping falla de forma persistente, Keepa
  ofrece datos de stock/precio de Amazon.es vía API JSON (planes desde ~49€/mes).
  Se descarta de momento por coste; usar solo si el scraping deja de ser fiable.

