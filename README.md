# pokestock-tcg-bot

Bot que vigila la [tienda Pokémon TCG en Amazon.es](https://www.amazon.es/stores/page/70E78EA6-79CB-4678-9249-717F2A13EB77)
y, cuando un producto pasa de "sin stock" a "disponible", envía un mensaje al grupo
de Telegram **PokéStock TCG** con el enlace de afiliado insertado.

## Cómo funciona

1. `check_stock.py` abre la página de la tienda con un navegador headless
   (Playwright + Chromium) y descubre automáticamente todos los productos
   listados (no hace falta mantener una lista manual de URLs).
2. Para cada producto encontrado, abre su ficha individual y comprueba si
   está disponible (botón "Añadir a la cesta" / "Comprar ahora" presente y
   ausencia de frases tipo "no disponible").
3. Compara el resultado con `state.json` (estado de la ejecución anterior).
   Si un producto pasa de no-disponible a disponible, envía un mensaje al
   grupo de Telegram con el enlace `https://www.amazon.es/dp/<ASIN>?tag=enkairito-21`.
4. Guarda el nuevo estado en `state.json` (se commitea automáticamente desde
   el workflow).

Un [GitHub Actions workflow](.github/workflows/check_stock.yml) ejecuta el
script cada 15 minutos (cron `*/15 * * * *`), y también se puede lanzar a
mano desde la pestaña **Actions** → **Run workflow**.

## Configuración

### 1. Secrets del repo

`Settings` → `Secrets and variables` → `Actions` → `New repository secret`:

| Secret | Valor |
|---|---|
| `TELEGRAM_BOT_TOKEN` | Token del bot (revocar y sustituir el que quedó expuesto en el chat original antes de usarlo) |
| `TELEGRAM_CHAT_ID` | `-1004397926701` |

### 2. Repo privado

Este repo debería estar en **privado** (`Settings` → visibilidad) — contiene
la lógica del scraper y el estado de negocio; no hay motivo para tenerlo público.
Nota: si el repo es privado, el cron cada 15 min consume minutos de Actions
del plan gratuito (2.000 min/mes); si se agotan, ajustar la frecuencia del
cron en `.github/workflows/check_stock.yml`.

### 3. Tag de afiliado

Definido directamente en `check_stock.py` como `AFFILIATE_TAG = "enkairito-21"`.

## Desarrollo local

```bash
pip install -r requirements.txt
playwright install chromium
cp .env.example .env   # rellenar TELEGRAM_BOT_TOKEN
set -a; source .env; set +a
python check_stock.py
```

Para probar cambios sin enviar mensajes reales al grupo, usa `DRY_RUN=1`:

```bash
DRY_RUN=1 python check_stock.py
```

Con `DRY_RUN=1` el script imprime en consola qué mensaje habría enviado en
lugar de llamar a la API de Telegram.

## Riesgos conocidos

- **Bloqueo por IP**: los runners de GitHub Actions usan IPs de datacenter,
  que Amazon suele bloquear/CAPTCHAr con más agresividad que las IPs
  residenciales. El script mitiga esto con rotación de user-agent, locale
  `es-ES` y pausas aleatorias entre peticiones, pero no lo elimina del todo.
  Si el scraping falla de forma persistente, la alternativa es migrar a la
  [Keepa API](https://keepa.com/#!api) (soporte para Amazon.es, datos de
  stock/precio vía JSON, planes desde ~49€/mes).
- **Cambios de estructura HTML**: si Amazon cambia los selectores
  (`#productTitle`, `#availability`, `#add-to-cart-button`), el script deja
  de detectar stock correctamente hasta que se actualicen.
