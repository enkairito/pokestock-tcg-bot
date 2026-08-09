# Contexto del proyecto (para retomar en otro ordenador)

Este fichero resume el estado y las decisiones del proyecto para poder
retomar la conversación con Claude desde cualquier máquina. Se actualiza
según avanza el trabajo, no es documentación de usuario final (para eso
está el `README.md`).

## Qué es esto

Dos repos conectados:

- **`pokestock-tcg-bot`** (privado, este repo): scraper Python/Playwright
  (`check_stock.py`) que vigila tiendas de Amazon (España y Reino Unido) y
  avisa por Telegram al grupo [PokéStockTCG](https://t.me/PokeStockTCG)
  cuando un producto pasa a disponible o baja el stock.
- **[`wheresthatstock`](https://github.com/enkairito/wheresthatstock)**
  (público): web estática en GitHub Pages
  (https://enkairito.github.io/wheresthatstock/) que muestra todos los
  productos rastreados. Se alimenta de `products.json`, que este repo
  publica automáticamente en cada ejecución del workflow (vía un PAT de
  ámbito reducido, secret `WHERESTHATSTOCK_TOKEN`).

Marca: ambos forman parte del ecosistema **"Where's That Shiny"**
(Instagram/TikTok @wheresthatshiny).

## Arquitectura del scraper

- `MARKETPLACES` en `check_stock.py`: lista de config por tienda (ES, UK)
  — dominio, tag de afiliado, cookies, patrones de idioma para detectar
  "invitación"/"no disponible"/stock bajo, páginas de tienda a visitar.
- Lee todo directamente de las tarjetas `[data-asin]` de la página de
  tienda (no visita fichas individuales de producto salvo fallback), lo
  que evita bloqueos de Amazon.
- **Amazon UK tiene restricciones especiales**, pedidas explícitamente:
  - `allow_individual_fallback: False` — nunca visita fichas de producto
    individuales en UK (causaron redirecciones raras a páginas de
    Barclays durante pruebas).
  - `exclude_out_of_stock: True` — los productos agotados de UK ni
    siquiera se guardan en `state.json` ni se publican en la web (a
    diferencia de ES, donde sí se mantienen y son filtrables).
- Estado en `state.json` (claves `MARKETPLACE:ASIN`), snapshot completo
  para la web en `products_snapshot.json`.
- `patchright` (fork de Playwright anti-detección) + modo no-headless vía
  `xvfb-run` en GitHub Actions — así es como se consiguió que funcionara
  de forma fiable desde las IPs de datacenter de GitHub (antes fallaba).
- Cron: cada hora, sin pausa nocturna (decisión explícita del usuario).

### Fotos de Telegram con marca de agua

`check_stock.py` compone una bandera del país (dibujada con Pillow, no
emoji — para que se vea igual en cualquier sistema) en la esquina
**superior derecha** de cada foto de producto antes de enviarla. Si falla
la composición, se envía la foto original sin marca de agua (nunca rompe
el envío). **No lleva el logo** — se probó y se quitó a petición del
usuario, se dejó solo la bandera.

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
  No disponible) y **Tienda** (Amazon ES 🇪🇸 / Amazon UK 🇬🇧).
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
- Tags de afiliado: `enkairito-21` (ES), `wtsuk-21` (UK), `wheresthatsto-21`
  (DE, pendiente de aprobación, no implementado en el scraper todavía).
- Acciones que tocan sistemas compartidos (push, enviar mensajes de
  Telegram al grupo real) siempre se confirman con el usuario antes de
  ejecutarlas de verdad — se prueba primero con `DRY_RUN=1` o generando
  una imagen de ejemplo cuando aplica.

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

- Las cuentas de Amazon Associates de DE y UK podrían perder el estatus
  por la regla de 3 ventas/180 días si no hay tráfico establecido en esos
  mercados todavía — sin plan concreto de tráfico para esos países aparte
  de lo que ya genera el canal de Telegram/la web.
