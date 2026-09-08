# Contexto del proyecto (para retomar en otro ordenador)

Este fichero resume el estado y las decisiones del proyecto para poder
retomar la conversación con Claude desde cualquier máquina. Se actualiza
según avanza el trabajo, no es documentación de usuario final (para eso
está el `README.md`).

## Qué es esto

Dos repos conectados:

- **`pokestock-tcg-bot`** (privado, este repo): scraper Python/Playwright
  (`check_stock.py`) que vigila tiendas de Amazon (España, Reino Unido y
  USA) y de El Corte Inglés (España) y avisa por Telegram al grupo
  [PokéStockTCG](https://t.me/PokeStockTCG) cuando un producto pasa a
  disponible o baja el stock.
- **[`wheresthatstock`](https://github.com/enkairito/wheresthatstock)**
  (privado desde el 2026-08-25 — antes público, ver nota abajo): web
  estática desplegada en Cloudflare Workers/Pages
  (https://wheresthatstock.com/, dominio propio comprado vía Cloudflare
  Registrar) que muestra todos los
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
  - Amazon USA usa páginas de tienda con parámetro de búsqueda dentro del
    mismo store ID (`search?terms=tcg` y `search?terms=etb`, URLs directas
    proporcionadas por el usuario), no el formato `stores/page/<ID>`
    simple de ES/UK.
- `exclude_out_of_stock: True` en las tres tiendas Amazon (ES se sumó el
  2026-08-24, antes solo aplicaba a UK/US) — los productos agotados no se
  publican en la web. **Importante**: al excluir un agotado se escribe
  explícitamente `no_disponible` en `state.json` en vez de simplemente no
  tocarlo — antes de este fix (2026-08-24) el estado se quedaba congelado
  en su último valor real y el bot nunca detectaba el siguiente restock
  (confirmado con datos reales: 24 productos UK/US llevaban así desde que
  se lanzaron esas tiendas). Ver commit `bc8c49b`.
- **Filtro por nombre**: `EXCLUDED_NAME_KEYWORDS` (`"funda"`, `"sleeve"`,
  `"sleeves"`) descarta productos cuyo título contenga esas palabras,
  aplicado a todas las tiendas — necesario porque las páginas de búsqueda
  de USA (a diferencia de las páginas de tienda "normales" de ES/UK)
  devuelven accesorios (fundas/protectores de cartas) mezclados con los
  productos reales.

### El Corte Inglés (ES)

Añadido el 2026-08-24, no es Amazon — tiene su propia config (`ECI_STORE`)
y su propio discover (`discover_eci_products`) en vez de encajarlo en
`MARKETPLACES`:
- Búsqueda pública, sin cookies/sesión necesarias.
- Sin ASIN — el ID de producto sale del atributo `id="product-<ID>"` de
  cada `<article>`.
- Sin flujo de invitación — solo `compra_directa` (botón "Añadir"
  presente) o `no_disponible`. La detección de "no_disponible" es
  **best-guess sin verificar** contra un producto agotado real todavía
  (no hemos visto ninguno en las pruebas) — revisar la primera vez que
  aparezca un caso así, igual que con los patrones de UK/US.
- URL usada: la página de categoría **"Juguetes"**
  (`/juguetes/search-nwx/?s=pokemon+jcc&stype=text_box_multi`), no la
  búsqueda general "Todo" — esta última mezcla resultados de
  Libros/Videojuegos/etc. que no son el producto en sí.
- **Afiliación pendiente**: la solicitud vía Awin sigue sin respuesta
  (issue #2) — de momento el link es la URL directa del producto, sin
  tracking. Cuando se apruebe, añadir el parámetro/deep-link de Awin en
  `ECI_STORE["tag"]` y en la construcción del link.
- Reutiliza el pipeline entero (estado, exclusión de agotados, filtro por
  nombre, alertas de Telegram, snapshot web) — solo cambian el discover y
  el bloque de fusión en `main()`. Sí genera alertas de Telegram (igual
  que Amazon ES, a diferencia de UK/US que solo alimentan la web).
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
  `setupFilterGroup`): **Disponibilidad** (Cómpralo ya / Con invitación —
  se quitó la opción "No disponible" el 2026-08-24, ya no puede aparecer
  en los datos porque `exclude_out_of_stock` es universal ahora) y
  **Tienda** (Amazon ES 🇪🇸 / Amazon UK 🇬🇧 / Amazon USA 🇺🇸 / El Corte
  Inglés 🇪🇸).
- Cada tarjeta de producto: la esquina superior izquierda de la imagen
  lleva, uno junto a otro, el logo de la tienda (`STORE_ICONS` — solo
  Amazon por ahora, El Corte Inglés no tiene logo propio todavía) + la
  bandera PNG real del país (`FLAG_ICONS`, mismos ficheros que usa el bot
  para las marcas de agua de Telegram — antes era un emoji) + la etiqueta
  de disponibilidad.
- **2026-08-25: desplegado en Cloudflare Workers/Pages con dominio propio
  (`wheresthatstock.com`, comprado vía Cloudflare Registrar) en vez de
  GitHub Pages.** Como consecuencia, el repo `wheresthatstock` ya no
  necesita ser público (esa era la única razón — GitHub Pages exige repo
  público en el plan gratuito). Se puso en **privado** el mismo día; la
  web sigue siendo pública igualmente, Cloudflare despliega desde el
  repo sin que este necesite visibilidad pública. `wrangler.toml` en la
  raíz de `wheresthatstock` (`[assets] directory = "./"`) es lo que le
  dice a Cloudflare que sirva los ficheros estáticos tal cual, sin
  tratarlo como un Worker con código.

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
  scraper todavía), `wtsjp-22` (Japón, alta 2026-08-27, pendiente de
  aprobación, no implementado en el scraper todavía — Amazon.co.jp
  necesitaría su propio marketplace en `check_stock.py` si se activa).
  El Corte Inglés (issue #2, vía Awin) también pendiente
  de aprobación — el scraper de ECI sí está implementado, pero con link
  directo sin tracking hasta que se apruebe.
  - **Comprobado en Awin (2026-08-27):** sigue en estado "Pending Approval"
    (ID de programa 13075). Aviso a tener en cuenta para cuando se apruebe:
    "Payment Level: Exposure Level 2" — señal de que El Corte Inglés ha
    superado su límite de crédito con Awin o no paga por domiciliación.
    Tiempo medio de pago 88 días + validación de ventas a 60 días desde
    el registro = ~4-5 meses hasta cobrar una venta real. No es motivo
    para descartar el programa, pero no contar con él como ingreso rápido.
- **Amazon USA: lanzada y en producción desde el 2026-08-10.** Anuncio
  enviado al grupo de Telegram, y el `workflow_dispatch` manual del mismo
  día (10:50 UTC) completó con éxito y ya mandó los avisos reales de los
  productos disponibles en ese momento (confirmado con `gh run list`). El
  estado de USA en `state.json` es real, no de pruebas — no reiniciarlo.
- **Simulaciones con `DRY_RUN=1`** (corregido el 2026-09-08): los seis
  scrapers omiten Telegram y conservan el estado, los snapshots y el
  historial de eventos. No consumen las alertas de la siguiente ejecución
  real. Pueden generar archivos de diagnóstico en `debug/`.
  En ejecuciones reales, nunca pisar
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

## Mejoras pendientes acordadas

### Mejoras implementadas el 2026-09-08

- Fechas y margen de retraso por fuente en la web (dos intervalos + 30 min),
  incluidos los productores diario y horario de accesorios; textos de cadencia
  corregidos. La portada combina novedades, ofertas y actividad de cinco juegos.
- `build_catalog.py` genera fichas HTML persistentes, metadatos y sitemap en
  cada publicación web. Archivos `catalog-*.json` conservan los productos
  ausentes como «sin confirmar» y `activity-*.json` guarda hasta 200 eventos
  por fuente. No se inventan eventos históricos al inicializar.
- `monitor_health.yml` comprueba publicaciones y workflows cada hora y al
  terminar los scrapers; informa en GitHub Actions de fallos consecutivos y
  datos antiguos, sin enviar mensajes externos.
- `stock_logic.py` comparte reglas de avisos y serialización de snapshots.
  Pruebas de regresión del bot y de Chromium para la interfaz en ambos repos.

### Decisiones aplazadas

- **2026-09-08 — API y almacenamiento persistente (aplazado):** el usuario
  quiere dejar anotada esta mejora, sin implementarla todavía. Evaluar
  Cloudflare Workers + D1 para que el bot publique resultados mediante una
  ruta autenticada y la web consulte productos, precios, disponibilidad y
  última comprobación por tienda y juego sin depender de commits y despliegues
  para actualizar los datos. Verificar límites y costes antes de elegir.
  Migrar primero los datos de la web y después, con pruebas específicas,
  el estado de deduplicación de alertas. La API no aumenta por sí sola la
  frecuencia de consulta de las tiendas. Mantener el sistema actual hasta
  que el usuario decida retomar la migración.

- **2026-09-08 — Enlace web a la futura comunidad de Telegram:** el usuario
  prevé crear una comunidad general y dirigir allí los enlaces de Telegram
  de la web. Mantener por ahora los enlaces actuales a `t.me/PokeStockTCG`.
  Cuando la comunidad esté creada y el usuario facilite su URL, actualizar
  los enlaces de la web para apuntar a ella. No sustituirlos por canales
  individuales de cada juego. Esta decisión afecta a la navegación web;
  los bots conservan sus destinos de alertas por juego.

## Issues abiertas (revisar con `gh issue list`)

**pokestock-tcg-bot**
- #2 `[Partnership] El Corte Inglés` — afiliación vía Awin (bloqueada)
- #3 `[Feature] Web propia` — mayormente hecha, revisar si cerrar
- #4 `[Partnership] Carrefour` — afiliación vía Awin (bloqueada)
- #5 `[Partnership] Fnac.es` — afiliación vía Awin (bloqueada)
- #6 `[Feature] Añadir Toys R Us como tienda` — pendiente de decidir
  programa de afiliados y URLs a rastrear

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
