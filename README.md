# pokestock-tcg-bot

Bot que vigila la [tienda Pokémon TCG en Amazon.es](https://www.amazon.es/stores/page/70E78EA6-79CB-4678-9249-717F2A13EB77)
y, cuando un producto pasa de "sin stock" a "disponible", envía un mensaje al grupo
de Telegram **PokéStock TCG** con el enlace de afiliado insertado.

## Cómo funciona

1. `check_stock.py` abre la página de la tienda con un navegador (Playwright
   + patchright/Chromium) y lee **directamente de las tarjetas de producto
   del listado** el nombre, el precio y el estado de disponibilidad de cada
   producto (no hace falta mantener una lista manual de URLs, ni visitar cada
   ficha de producto individual — todo está ya en la propia tarjeta).
2. El estado se determina así:
   - `compra_directa`: la tarjeta tiene un botón funcional de "Añadir a la
     cesta" (`data-cy="add-to-cart"`).
   - `invitacion`: la tarjeta muestra el texto "Disponible por invitación".
   - `no_disponible`: la tarjeta muestra "No disponible." (sin precio).
3. Compara el resultado con `state.json` (estado de la ejecución anterior).
   Si un producto pasa a `compra_directa` o `invitacion` desde un estado
   distinto, envía un mensaje al grupo de Telegram con el enlace
   `https://www.amazon.es/dp/<ASIN>?tag=enkairito-21`.
4. Guarda el nuevo estado en `state.json` (se commitea automáticamente desde
   el workflow).

Este enfoque (una sola carga de página en vez de una por producto) reduce
mucho el riesgo de bloqueo por parte de Amazon frente al enfoque anterior de
visitar cada ficha individual.

La automatización en producción corre en
[GitHub Actions](.github/workflows/check_stock.yml), cada 30 minutos,
pausado entre las 2:00 y las 6:00 (hora de España) — cron `*/30 4-23 * * *`
en UTC. GitHub Actions no soporta zonas horarias ni DST en cron, así que
este horario está calculado para CEST (UTC+2, horario de verano) y hay que
ajustarlo manualmente cuando España pase a CET en octubre (ver comentario en
el workflow). También se puede lanzar a mano desde **Actions** →
**Run workflow**.

El extraer todo directamente de las tarjetas de la tienda (en vez de
visitar cada ficha de producto) combinado con
[patchright](https://github.com/Kaliiiiiiiiii-Vinyzu/patchright-python),
modo no-headless (vía `xvfb-run` en el runner) y cookies de una sesión real
(ver sección "Cookies de Amazon" abajo) hace que esto funcione de forma
fiable desde las IPs de datacenter de GitHub Actions — confirmado en
pruebas reales. El servidor propio con crontab (sección "Producción
alternativa" abajo) queda como alternativa/respaldo si Amazon vuelve a
bloquear GitHub Actions en el futuro.

## Configuración

### 1. Secrets del repo

`Settings` → `Secrets and variables` → `Actions` → `New repository secret`:

| Secret | Valor |
|---|---|
| `TELEGRAM_BOT_TOKEN` | Token del bot (revocar y sustituir el que quedó expuesto en el chat original antes de usarlo) |
| `TELEGRAM_CHAT_ID` | `-1004397926701` |
| `AMAZON_COOKIES_JSON` | Contenido completo de `amazon_cookies.json` (ver sección "Cookies de Amazon" abajo) |

### 2. Repo privado

Este repo debería estar en **privado** (`Settings` → visibilidad) — contiene
la lógica del scraper y el estado de negocio; no hay motivo para tenerlo público.

### 3. Tag de afiliado

Definido directamente en `check_stock.py` como `AFFILIATE_TAG = "enkairito-21"`.

### 4. Cookies de Amazon

El bot carga cookies de una sesión real de Amazon.es para parecer una
navegación humana. Para generarlas (o renovarlas si dejan de funcionar):

```bash
python bootstrap_cookies.py
```

Esto abre un Chrome visible: navega normalmente por amazon.es (acepta
cookies, inicia sesión si quieres), pulsa Enter en la terminal cuando
termines, y se guarda `amazon_cookies.json` en el proyecto (nunca se sube a
git, está en `.gitignore`). Copia su contenido completo al secret
`AMAZON_COOKIES_JSON` en GitHub para que el workflow lo use.

⚠️ Este archivo contiene tokens de sesión reales de tu cuenta de Amazon
(`session-token`, `at-acbes`, etc.) — trátalo como una contraseña.

## Producción alternativa (servidor con crontab)

```bash
git clone https://github.com/enkairito/pokestock-tcg-bot.git
cd pokestock-tcg-bot
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python -m patchright install --with-deps chromium

cat > .env <<'EOF'
TELEGRAM_BOT_TOKEN=tu_token_aqui
TELEGRAM_CHAT_ID=-1004397926701
EOF

chmod +x run_local.sh
./run_local.sh   # prueba manual antes de automatizar
```

`crontab -e`, cada 30 minutos pausado entre las 2:00 y las 6:00 (hora local
del servidor — confirma que el servidor tenga la zona horaria en
`Europe/Madrid` con `timedatectl`, ya que a diferencia del cron de GitHub
Actions, crontab sí respeta zonas horarias y DST):

```
*/30 0-1,6-23 * * * cd /ruta/completa/a/pokestock-tcg-bot && ./run_local.sh >> cron.log 2>&1
```

`state.json` se actualiza localmente en el servidor en cada ejecución; si se
quiere mantener sincronizado con git, hay que añadir un `git add/commit/push`
al final de `run_local.sh` (no incluido por defecto).

## Desarrollo local

```bash
pip install -r requirements.txt
python -m patchright install chromium
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

- **Bloqueo por IP en datacenters**: confirmado en la práctica — los runners
  de GitHub Actions (IPs de datacenter, normalmente en EE.UU.) reciben de
  Amazon.es un 404 genérico ("Documento no encontrado") o un interstitial
  "Haz clic en el botón de abajo para seguir comprando" en vez del contenido
  real, mientras que la misma URL funciona sin problema desde una IP
  residencial. Por eso la automatización en producción corre desde un
  servidor propio (ver "Producción" arriba) en vez de GitHub Actions.
  El script usa [patchright](https://github.com/Kaliiiiiiiiii-Vinyzu/patchright-python)
  (fork de Playwright con parches anti-detección) y hace clic automático en
  el interstitial "seguir comprando" cuando aparece, pero ninguna de las dos
  cosas evita el bloqueo específico de las IPs de GitHub Actions.
  Si el scraping falla de forma persistente incluso desde IP residencial, la
  alternativa es migrar a la [Keepa API](https://keepa.com/#!api) (soporte
  para Amazon.es, datos de stock/precio vía JSON, planes desde ~49€/mes).
- **Cambios de estructura HTML**: si Amazon cambia los atributos de las
  tarjetas de producto (`data-asin`, `data-cy="add-to-cart"`, el texto
  "Disponible por invitación"/"No disponible."), el script deja de detectar
  el estado correctamente hasta que se actualicen los selectores.
