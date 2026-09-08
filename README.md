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
   `products_snapshot.json`. El workflow persiste primero el estado y los
   eventos en el repositorio del bot; después publica el snapshot como
   `products.json` en `wheresthatstock`. Un fallo al publicar la web no
   impide conservar el estado de los avisos ya procesados.

La publicación usa `publish_updates.py`: aplica únicamente los archivos
generados en un worktree temporal basado en el remoto actual. Si otro
workflow publica durante ese intervalo, vuelve a obtener el remoto y
recalcula la fusión de accesorios y sitemap antes de reintentar. Después
de tres pushes fallidos, el paso falla explícitamente. Si falla la propia
persistencia del estado, o se interrumpe el scraper antes de guardarlo,
todavía pueden repetirse avisos en la siguiente ejecución.

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
público. El repo `wheresthatstock` también está en privado (desde que se
desplegó en Cloudflare en vez de GitHub Pages, ya no hace falta que sea
público) — la web en sí sigue siendo pública en https://wheresthatstock.com/.

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

Con `DRY_RUN=1` los seis scrapers consultan las tiendas e imprimen en consola
los avisos simulados, sin llamar a la API de Telegram ni guardar el estado,
los snapshots o el historial de eventos. Así, las alertas siguen pendientes
para la siguiente ejecución real. Los archivos de diagnóstico de errores
pueden seguir generándose en `debug/`.

## Pruebas

Con las dependencias de `requirements.txt` instaladas:

```bash
python -B -m unittest discover -s tests -v
```

En Windows se puede usar `py -3 -B -m unittest discover -s tests -v`.
Las pruebas simulan las tiendas y Telegram, y verifican la publicación
contra repositorios Git temporales locales. También se ejecutan en
GitHub Actions cuando cambia el código o los workflows.

## Fichas, actividad y salud del stock

`stock_logic.py` comparte las reglas de cambios de disponibilidad, precio y
unidades, además de la serialización de snapshots, entre los scrapers.
La exclusión de accesorios de las alertas de One Piece sigue en su scraper.

Al publicar la web, `publish_updates.py` ejecuta `build_catalog.py` sobre el
checkout remoto actualizado. Conserva un `catalog-*.json` y un feed
`activity-*.json` por origen, y genera fichas estáticas en `producto/` con
contenido inicial, metadatos y sitemap. Los productos ausentes se conservan
como «sin confirmar»: la ausencia en el listado no demuestra que estén agotados.
La inicialización no inventa eventos históricos; los nuevos juegos registran
cambios a partir de su siguiente publicación. Se conservan hasta 200 eventos
por origen. Los reintentos recombinan estos archivos con la versión remota.

`monitor_health.yml` comprueba cada hora y tras finalizar un scraper los
últimos resultados de GitHub Actions y los snapshots públicos. Falla y genera
un resumen con las fuentes afectadas cuando hay dos ejecuciones consecutivas
sin éxito, una fecha inválida o más de dos intervalos previstos más 30 minutos
sin una actualización correcta. Detecta también que el workflow termine pero
la web siga mostrando datos antiguos. No envía mensajes a Telegram.
El monitor usa solo permisos de lectura y se puede lanzar manualmente.
El cron de Pokémon se desplaza al minuto 17 para evitar el pico de inicio de
hora de GitHub Actions. La programación puede seguir sufriendo retrasos;
el monitor los señala, pero no los compensa enviando avisos automáticamente.
Tras una ejecución correcta se dan cinco minutos de margen al despliegue
antes de exigir que la publicación pública esté actualizada.

Accesorios conserva `source_updates` para separar la consulta diaria de la
aportación horaria de One Piece; una no rejuvenece los datos de la otra.
La interfaz aplica los mismos márgenes e indica la frecuencia de cada fuente.
Las fechas iniciales se recuperan de publicaciones reales del historial Git.
La migración a API/base de datos sigue aplazada.

## Riesgos conocidos

- **Consultas incompletas**: un error HTTP, captcha, ficha sin título o
  listado sin productos verificables hace fallar la ejecución de ese juego
  antes de enviar avisos o sustituir su estado/snapshot. La web conserva
  la publicación anterior con su fecha. Si la consulta sí devuelve
  productos y todos están agotados, se guarda ese estado y se publica un
  listado vacío correctamente. Una página sin tarjetas ni fichas
  verificables se trata de forma conservadora como fallo, no como stock cero.

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
