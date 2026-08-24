# Contexto del proyecto (para retomar en otro ordenador)

Este fichero resume el estado y las decisiones del proyecto para poder
retomar la conversación con Claude desde cualquier máquina. Se actualiza
según avanza el trabajo, no es documentación de usuario final (para eso
está el `README.md`).

## Qué es esto

Dos repos conectados:

- **`pokestock-tcg-bot`** (privado, este repo): scraper Python/Playwright
  (`check_stock.py`) que vigila tiendas de Amazon (España, Reino Unido y
  USA) y avisa por Telegram al grupo
  [PokéStockTCG](https://t.me/PokeStockTCG) cuando un producto pasa a
  disponible o baja el stock.
- **[`wheresthatstock`](https://github.com/enkairito/wheresthatstock)**
  (público): web estática en GitHub Pages
  (https://enkairito.github.io/wheresthatstock/) que muestra todos los
  productos rastreados. Se alimenta de `products.json`, que este repo
  publica automáticamente en cada ejecución del workflow (vía un PAT de
  ámbito reducido, secret `WHERESTHATSTOCK_TOKEN`).

Marca: ambos forman parte del ecosistema **"Where's That Shiny"**
(Instagram/TikTok @wheresthatshiny).

## Arquitectura del scraper

- `MARKETPLACES` en `check_stock.py`: lista de config por tienda (ES, UK,
  US) — dominio, tag de afiliado, cookies, patrones de idioma para
  detectar "invitación"/"no disponible"/stock bajo, páginas de tienda a
  visitar.
- Lee todo directamente de las tarjetas `[data-asin]` de la página de
  tienda (no visita fichas individuales de producto salvo fallback), lo
  que evita bloqueos de Amazon.
- **Amazon UK y USA tienen restricciones especiales**, pedidas
  explícitamente (mismo criterio aplicado a ambas por precaución, aunque
  el incidente concreto que lo motivó solo pasó en UK):
  - `allow_individual_fallback: False` — nunca visitan fichas de producto
    individuales (en UK causaron redirecciones raras a páginas de
    Barclays durante pruebas).
  - `exclude_out_of_stock: True` — los productos agotados ni siquiera se
    guardan en `state.json` ni se publican en la web (a diferencia de ES,
    donde sí se mantienen y son filtrables).
  - Amazon USA usa páginas de tienda con parámetro de búsqueda dentro del
    mismo store ID (`search?terms=tcg` y `search?terms=etb`, URLs directas
    proporcionadas por el usuario), no el formato `stores/page/<ID>`
    simple de ES/UK.
- **Filtro por nombre**: `EXCLUDED_NAME_KEYWORDS` (actualmente solo
  `"funda"`) descarta productos cuyo título contenga esas palabras,
  aplicado a todas las tiendas — necesario porque las páginas de búsqueda
  de USA (a diferencia de las páginas de tienda "normales" de ES/UK)
  devuelven accesorios (fundas/protectores de cartas) mezclados con los
  productos reales.
- Estado en `state.json` (claves `MARKETPLACE:ASIN`), snapshot completo
  para la web en `products_snapshot.json`.
- `patchright` (fork de Playwright anti-detección) + modo no-headless vía
  `xvfb-run` en GitHub Actions — así es como se consiguió que funcionara
  de forma fiable desde las IPs de datacenter de GitHub (antes fallaba).
- Cron: cada hora, sin pausa nocturna (decisión explícita del usuario).

### Fotos de Telegram con marca de agua

`check_stock.py` compone una bandera del país en la esquina **superior
derecha** de cada foto de producto antes de enviarla. Si falla la
composición, se envía la foto original sin marca de agua (nunca rompe el
envío). **No lleva el logo** — se probó y se quitó a petición del usuario,
se dejó solo la bandera.

Las banderas son **iconos PNG reales** descargados de flagcdn.com,
guardados en `assets/flags/{es,gb,us}.png` y mapeados en `FLAG_FILES`
(antes se dibujaban a mano con Pillow — la de USA en concreto se veía mal
a tamaño pequeño, se cambió a iconos reales a petición del usuario). Para
añadir una tienda nueva: descargar su icono a `assets/flags/<código>.png`
y añadir la entrada a `FLAG_FILES`.

### Precios "null"

Amazon a veces renderiza el precio tachado (`original_price`) como el
texto literal `"null"` cuando el producto no tiene precio de referencia
(visto en Amazon UK). `clean_price()` lo sanea a `None`.

## Web (`wheresthatstock`)

- Estática, sin backend, `fetch("products.json")`.
- Cabecera: avatar circular (`assets/logo.jpg`) + `@wheresthatshiny` +
  logo "Where's That Stock".
- Filtros en desplegable (mismo patrón para ambos, vía helper JS
  `setupFilterGroup`): **Disponibilidad** (Cómpralo ya / Con invitación /
  No disponible) y **Tienda** (Amazon ES 🇪🇸 / Amazon UK 🇬🇧 / Amazon USA 🇺🇸).
- Cada tarjeta de producto: la esquina superior izquierda de la imagen
  lleva la bandera de la tienda + la etiqueta de disponibilidad, pegadas
  a la foto (sin logo — se quitó a petición del usuario, a diferencia de
  Telegram donde tampoco lo lleva).
- El repo es público porque los datos (stock/precio de Amazon) no son
  sensibles; lo que sí es privado es la lógica del scraper.

## Decisiones/aprendizajes importantes

- **`git rebase` invierte `--ours`/`--theirs`** respecto a un merge
  normal — un `git checkout --ours` durante un rebase se queda con la
  rama upstream, no con los propios cambios. Esto causó una pérdida real
  de datos en `products.json` una vez (recuperado regenerando desde
  cero). Preferir regenerar/verificar datos antes que fiarse ciegamente
  de flags de checkout en conflictos de rebase.
- Amazon UK: patrones de detección de texto en inglés (`invitation_marker`,
  `stock_count_re`, etc.) son **best-guess sin verificar** contra una
  página real de invitación/bajo stock — revisar con datos reales la
  primera vez que aparezca un caso así.
- Tags de afiliado: `enkairito-21` (ES), `wtsuk-21` (UK), `wtsus-20` (USA),
  `wheresthatsto-21` (DE, pendiente de aprobación, no implementado en el
  scraper todavía).
- **Amazon USA: lanzada y en producción desde el 2026-08-10.** Anuncio
  enviado al grupo de Telegram, y el `workflow_dispatch` manual del mismo
  día (10:50 UTC) completó con éxito y ya mandó los avisos reales de los
  productos disponibles en ese momento (confirmado con `gh run list`). El
  estado de USA en `state.json` es real, no de pruebas — no reiniciarlo.
- **Cuidado al probar en local con `DRY_RUN=1`**: aunque no envía mensajes
  reales a Telegram, sí que escribe en `state.json` (el guardado de estado
  no está condicionado a `DRY_RUN`). Si se prueba una tienda nueva antes
  de su lanzamiento real, hay que descartar esas entradas de `state.json`
  antes de hacer commit para que el primer run real dispare los avisos de
  lanzamiento. Una vez lanzada de verdad, todo lo contrario: nunca pisar
  `state.json` local sobre el remoto sin comprobar antes con
  `git fetch` + `git log origin/main` si GitHub Actions ha corrido
  mientras tanto (por poco se pierde así el estado real de USA en esta
  misma sesión).
- Acciones que tocan sistemas compartidos (push, enviar mensajes de
  Telegram al grupo real) siempre se confirman con el usuario antes de
  ejecutarlas de verdad — se prueba primero con `DRY_RUN=1` o generando
  una imagen de ejemplo cuando aplica.
- **Las cookies de Amazon caducan de forma silenciosa** (2026-08-24): las
  de ES llevaban 18 días puestas (desde el 6 de agosto) y dejaron de
  detectar bien el estado `invitacion` — no por expiración técnica de la
  cookie (`session-token`/`at-acbes` seguían "vigentes" hasta 2027/2028),
  sino porque Amazon dejó de mostrarle la personalización a esa sesión
  (probablemente por falta de actividad "humana" real, al correr solo
  desde IPs de datacenter de GitHub Actions). El fallo no genera ningún
  error ni traza en los logs — simplemente empieza a devolver falsos
  `no_disponible`. Se confirmó comparando la detección del bot (con las
  cookies del secret) contra un scrape local con cookies recién
  exportadas del navegador real del usuario, que sí detectaba bien. Sin
  más datos históricos para fijar una cadencia exacta, se adopta como
  norma provisional: **refrescar las cookies de las 3 tiendas cada 2-3
  semanas** de forma proactiva (vía export manual de una extensión tipo
  Cookie-Editor, en formato `chrome.cookies` — `normalize_cookies()` en
  `check_stock.py` ya lo admite tal cual, sin conversión), en vez de
  esperar a que se note el fallo. Revisar si conviene ajustar esta
  cadencia según se acumulen más datos.

## Issues abiertas (revisar con `gh issue list`)

**pokestock-tcg-bot**
- #2 `[Partnership] El Corte Inglés` — afiliación vía Awin (bloqueada)
- #3 `[Feature] Web propia` — mayormente hecha, revisar si cerrar
- #4 `[Partnership] Carrefour` — afiliación vía Awin (bloqueada)
- #5 `[Partnership] Fnac.es` — afiliación vía Awin (bloqueada)

**wheresthatstock**
- #1 `[Future] Dificultar acceso directo a products.json` (seguridad)
- #2 `Eliminar productos sin actualizar hace más de 1 mes` (aún sin
  implementar — la idea es guardar `last_seen` por producto en el estado
  y excluir del snapshot publicado los que lo superen)

## Riesgos abiertos (sin resolver, solo para tener en cuenta)

- Las cuentas de Amazon Associates de DE, UK y USA podrían perder el
  estatus por la regla de 3 ventas/180 días si no hay tráfico establecido
  en esos mercados todavía — sin plan concreto de tráfico para esos
  países aparte de lo que ya genera el canal de Telegram/la web.
- **Legitimidad del scraping**: el scraping automatizado con cookies de
  sesión real técnicamente incumple las Condiciones de Uso de Amazon y el
  Associates Program Operating Agreement (prohíben acceso automatizado sin
  permiso). El riesgo real no es legal sino de cuenta (bloqueo/cierre,
  tanto de la cuenta de Amazon personal como del Associates). El bloqueo
  ya observado desde IPs de GitHub Actions confirma que sus sistemas
  anti-bot detectan parte de este tráfico. La alternativa "oficial" es la
  Product Advertising API (PA-API) de Amazon, gratuita para Associates con
  ventas cualificadas, pero requiere reescribir la lógica de scraping y
  tener ventas activas para cualificar — no evaluado en profundidad
  todavía. Por esto, al redactar las descripciones para las solicitudes de
  Associates (DE/UK/USA), se evita mencionar el mecanismo de scraping/bot
  y se describe el sitio en términos de audiencia y tráfico, no de
  infraestructura.
