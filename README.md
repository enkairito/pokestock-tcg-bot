# pokestock-tcg-bot

Bot que vigila páginas de tienda de Pokémon TCG en **Amazon España**,
**Amazon Reino Unido** y **Amazon USA** y, cuando un producto pasa a estar disponible (o baja
su stock), envía un aviso al grupo de Telegram **PokéStockTCG** con foto,
precio, stock y enlace de afiliado. También publica un snapshot de todos los
productos rastreados para la web pública
[**Where's That Stock**](https://github.com/enkairito/wheresthatstock)
(https://wheresthatstock.com/).

Para el contexto completo de decisiones/arquitectura/issues abiertas (útil
para retomar el trabajo desde otro ordenador), ver [`CONTEXT.md`](CONTEXT.md).

## Cómo funciona

1. `check_stock.py` recorre cada tienda configurada en `MARKETPLACES`
   (Amazon.es, Amazon.co.uk y Amazon.com, cada una con su propio dominio, idioma, tag de
   afiliado y cookies) y, dentro de cada una, cada página de tienda listada.
   Lee **directamente de las tarjetas de producto** (`[data-asin]`) el
   nombre, precio, imagen y estado de disponibilidad — no hace falta
   mantener una lista manual de URLs ni visitar cada ficha de producto
   individual.
2. El estado se determina así:
   - `compra_directa`: la tarjeta tiene un botón funcional de "Añadir a la
     cesta" (`data-cy="add-to-cart"`).
   - `invitacion`: la tarjeta muestra el texto de "disponible por
     invitación" (patrón específico por idioma/tienda).
   - `no_disponible`: ninguna de las anteriores.
3. Algunos widgets de tienda no incluyen precio/disponibilidad en la
   tarjeta, solo un enlace al producto — esos ASIN se comprueban a mano
   visitando la ficha individual, **salvo en Amazon UK y Amazon USA**,
   donde está desactivado explícitamente (`allow_individual_fallback:
   False`) para no generar tráfico raro hacia fichas individuales en esos
   dominios.
4. En Amazon UK y Amazon USA, además, los productos que salen agotados en
   el scrape se descartan del todo (`exclude_out_of_stock: True`): no se
   guardan en `state.json` ni se publican en la web. En Amazon ES sí se
   mantienen, para poder filtrarlos en la web.
5. Compara el resultado con `state.json` (estado de la ejecución anterior).
   Si un producto pasa a `compra_directa`/`invitacion` desde otro estado, o
   si su stock baja, envía un aviso al grupo de Telegram con foto (marca de
   agua con la bandera del país en la esquina superior derecha) y el enlace
   de afiliado correspondiente a esa tienda.
6. Guarda el nuevo estado en `state.json` y un snapshot completo en
   `products_snapshot.json` (ambos se commitean automáticamente desde el
   workflow). El snapshot se copia además a `products.json` en el repo
   público `wheresthatstock` para alimentar la web.

Este enfoque (leer las tarjetas de la tienda en vez de visitar cada ficha
individual) reduce mucho el riesgo de bloqueo por parte de Amazon.

La automatización en producción corre en
[GitHub Actions](.github/workflows/check_stock.yml), cada hora, todo el día
(sin pausa nocturna) — cron `0 * * * *`. También se puede lanzar a mano
desde **Actions** → **Run workflow**.

Extraer todo directamente de las tarjetas de la tienda, combinado con
[patchright](https://github.com/Kaliiiiiiiiii-Vinyzu/patchright-python)
(fork de Playwright anti-detección), modo no-headless (vía `xvfb-run` en el
runner) y cookies de una sesión real (ver "Cookies de Amazon" abajo), hace
que esto funcione de forma fiable desde las IPs de datacenter de GitHub
Actions — confirmado en pruebas reales. El servidor propio con crontab
(sección "Producción alternativa" abajo) queda como respaldo si Amazon
vuelve a bloquear GitHub Actions en el futuro.

## Configuración

### 1. Secrets del repo

`Settings` → `Secrets and variables` → `Actions` → `New repository secret`:

| Secret | Valor |
|---|---|
| `TELEGRAM_BOT_TOKEN` | Token del bot |
| `TELEGRAM_CHAT_ID` | ID del grupo de Telegram |
| `AMAZON_COOKIES_JSON` | Contenido completo de `amazon_cookies.json` (sesión de Amazon.es) |
| `AMAZON_UK_COOKIES_JSON` | Contenido completo de `amazon_cookies_uk.json` (sesión de Amazon.co.uk) |
| `AMAZON_US_COOKIES_JSON` | Contenido completo de `amazon_cookies_us.json` (sesión de Amazon.com) |
| `WHERESTHATSTOCK_TOKEN` | Personal Access Token (fine-grained, permiso de escritura solo sobre el repo `wheresthatstock`) usado para publicar `products.json` |

### 2. Repo privado

Este repo debería estar en **privado** (`Settings` → visibilidad) — contiene
la lógica del scraper y el estado de negocio; no hay motivo para tenerlo
público. La web (`wheresthatstock`) sí es pública porque solo expone datos
no sensibles (stock/precio de Amazon).

### 3. Tags de afiliado y tiendas

Definidos en `MARKETPLACES` dentro de `check_stock.py` (uno por tienda:
`enkairito-21` para ES, `wtsuk-21` para UK, `wtsus-20` para USA). Añadir una tienda nueva es
añadir una entrada más a esa lista con su dominio, cookies, patrones de
idioma y páginas de tienda a vigilar.

### 4. Cookies de Amazon

El bot carga cookies de una sesión real de Amazon para parecer una
navegación humana (una por tienda). Para generarlas (o renovarlas si dejan
de funcionar):

```bash
python bootstrap_cookies.py
```

Esto abre un Chrome visible: navega normalmente por la tienda
correspondiente (acepta cookies, inicia sesión si quieres), pulsa Enter en
la terminal cuando termines, y se guarda el fichero de cookies en el
proyecto (nunca se sube a git, está en `.gitignore`). Copia su contenido
completo al secret correspondiente en GitHub para que el workflow lo use.

⚠️ Estos ficheros contienen tokens de sesión reales de tu cuenta de Amazon
(`session-token`, `at-acbes`, etc.) — trátalos como una contraseña.

## Producción alternativa (servidor con crontab)

```bash
git clone https://github.com/enkairito/pokestock-tcg-bot.git
cd pokestock-tcg-bot
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python -m patchright install --with-deps chromium

cat > .env <<'EOF'
TELEGRAM_BOT_TOKEN=tu_token_aqui
TELEGRAM_CHAT_ID=tu_chat_id_aqui
EOF

chmod +x run_local.sh
./run_local.sh   # prueba manual antes de automatizar
```

`crontab -e`, cada hora (hora local del servidor — confirma que el servidor
tenga la zona horaria correcta con `timedatectl`, ya que a diferencia del
cron de GitHub Actions, crontab sí respeta zonas horarias y DST):

```
0 * * * * cd /ruta/completa/a/pokestock-tcg-bot && ./run_local.sh >> cron.log 2>&1
```

`state.json` se actualiza localmente en el servidor en cada ejecución; si se
quiere mantener sincronizado con git, hay que añadir un `git add/commit/push`
al final de `run_local.sh` (no incluido por defecto).

## Desarrollo local

```bash
pip install -r requirements.txt
python -m patchright install chromium
# crea .env con TELEGRAM_BOT_TOKEN y TELEGRAM_CHAT_ID
python check_stock.py
```

En Windows, `run_local.ps1` carga `.env` y lanza el script directamente.

Para probar cambios sin enviar mensajes reales al grupo, usa `DRY_RUN=1`:

```bash
DRY_RUN=1 python check_stock.py
```

Con `DRY_RUN=1` el script imprime en consola qué mensaje habría enviado en
lugar de llamar a la API de Telegram.

## Riesgos conocidos

- **Cambios de estructura HTML**: si Amazon cambia los atributos de las
  tarjetas de producto (`data-asin`, `data-cy="add-to-cart"`, los textos de
  "disponible por invitación"/"no disponible"), el script deja de detectar
  el estado correctamente hasta que se actualicen los selectores.
- **Patrones de Amazon UK sin verificar del todo**: los patrones de texto en
  inglés (`invitation_marker`, `stock_count_re`, etc.) son la mejor
  estimación disponible, pendientes de confirmar contra un caso real de
  invitación/stock bajo en Amazon.co.uk.
- **Cuentas de Amazon Associates (DE, UK)**: riesgo de perder el estatus de
  afiliado por la regla de mínimo de ventas en 180 días si no hay tráfico
  establecido en esos mercados todavía.

Ver [`CONTEXT.md`](CONTEXT.md) para más detalle sobre decisiones de diseño,
issues abiertas y aprendizajes (p. ej. el bug de `git rebase`
`--ours`/`--theirs`).
