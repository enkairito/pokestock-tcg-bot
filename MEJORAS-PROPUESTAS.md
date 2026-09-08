# Where’s That Stock: mejoras propuestas para revisar

Revisión: 2026-09-08. La investigación inicial dio paso a la implementación autorizada por el usuario. No se han enviado mensajes ni creado integraciones externas durante este trabajo.

## Estado tras la implementación

- Precios: corregida la lectura completa, descuentos entre monedas iguales y coherencia/validación del límite de precio.
- Filtros: contador de cero resultados, reinicio que conserva los valores iniciales de cada página y reintento de carga.
- Fichas: retorno validado al listado de origen, compartir URL canónica, metadatos sociales estáticos y consulta prioritaria a la fuente conocida sin bloquear por estadísticas.
- Favoritos: exportación e importación JSON con vista previa, validación, deduplicación y conservación del contenido anterior; búsqueda conservada en la URL.
- Mantenimiento: navegación común en `partials/navigation.html`, sincronización estática comprobada en CI, comprobación de recursos y Manrope WOFF2 de 24.836 bytes frente a 165.420 del TTF.
- **Avisos de pocas unidades: mantener el comportamiento actual**, por respuesta explícita del usuario. No aplicar el umbral propuesto.
- **Discord pendiente para trabajar con el usuario.** No se han creado webhooks, comandos ni destinos.
- **Avisos privados del monitor pendientes de elegir y configurar un destino.** El monitor interno actual sigue funcionando.

El resto del documento conserva el análisis y alcance que motivaron estos cambios.

## Orden recomendado

| Orden | Trabajo | Tipo / esfuerzo orientativo | Resultado |
| --- | --- | --- | --- |
| 1 | Corregir lectura y validación de precios | Fallo confirmado / pequeño | Orden y límites correctos, sin discrepancias entre controles y filtro |
| 2 | Discord: alertas por juego y entregas independientes | Ampliación / medio | Distribuir las alertas existentes también a Discord |
| 3 | Recuperar la selección desde la ficha | Mejora confirmada / pequeño | Volver al listado exacto desde el que se abrió el producto |
| 4 | Mejorar cero resultados y reinicio de filtros | Mejora confirmada / pequeño | Recuperar el listado sin deshacer filtros uno a uno |
| 5 | Reducir ruido de alertas | Decisión de producto / medio | Priorizar reposiciones y bajadas relevantes |
| 6 | Compartir fichas con una vista previa propia | Mejora / pequeño-medio | Enlaces que muestran el producto concreto en chats |
| 7 | Carga de fichas independiente de otras fuentes | Mejora / medio | Una tienda o juego lento no retrasa datos de otro |
| 8 | Avisos privados del monitor interno | Ampliación / pequeño-medio | Enterarse de incidencias sin consultar GitHub a mano |
| 9 | Exportar e importar favoritos | Ampliación / pequeño-medio | Llevarlos a otro dispositivo sin añadir cuentas |
| 10 | Ampliar cobertura de CI y reducir duplicación de HTML | Mantenimiento / pequeño y medio, respectivamente | Detectar roturas de assets y hacer cambios comunes en un solo sitio |

Esfuerzo relativo, no presupuesto ni compromiso de tiempo. Discord puede prepararse mientras se resuelven los pequeños arreglos; su activación depende de disponer de servidor, canales y configuración privada.

## 1. Precios: dos fallos reproducidos y una limitación que conviene prevenir

En `wheresthatstock/product-utils.js`, `parsePrice()` interpreta `1234,56 €` como **234.56**. Con `1.234,56 €` devuelve correctamente 1234.56. La expresión regular permite encontrar solo las últimas tres cifras de una parte entera sin separadores. Afecta potencialmente a ordenación, límite de precio y descuento. Reproducido con el JavaScript real en Chromium; no se afirma que haya ahora mismo una oferta real afectada.

En `app.js`, escribir `-1` deja el campo con ese valor mientras el filtro sigue usando su límite anterior (10 en la prueba). El dato inválido se ignora internamente pero no se corrige ni se explica en el control. También revisar los límites del deslizador frente al campo numérico y la URL.

Propuesta: un parseo completo, cantidades no negativas, actualización coherente de campo/deslizador/chip/URL y casos de regresión con y sin separador de miles. Comparar el resultado con `stock_logic.price_to_float`, que usa otra expresión regular.

La función web tampoco interpreta `$12.34` o `£12.34`. **Los seis snapshots inspeccionados tenían todos sus precios en euros**, incluso UK y US; no se ha confirmado una comparación actual entre monedas distintas. Añadir esta cobertura como prevención y conservar explícitamente moneda si llegan esos formatos. No convertir dólares/libras en euros simplemente cambiando el símbolo.

## 2. Discord por fases

### Fase inicial: alertas en canales por juego

Crear canales de Pokémon, One Piece, Magic, Lorcana y Yu-Gi-Oh!, con los avatares aprobados. Accesorios puede añadirse si se decide enviar también sus avisos. Publicar una tarjeta con nombre, tienda, precio, imagen y enlaces a la ficha y a la tienda. El servidor puede tener un canal general de conversación separado de las alertas.

Para este primer alcance usar **webhooks entrantes**, que pueden publicar en un canal sin mantener una conexión permanente ni un bot conectado. Encajan con los scrapers que ya se ejecutan en GitHub Actions. [Discord: webhooks](https://docs.discord.com/developers/platform/webhooks).

Reutilizar los resultados del rastreo y las reglas de `stock_logic.py`. No consultar de nuevo las tiendas desde Discord. La frecuencia seguirá siendo la de las comprobaciones actuales; añadir una plataforma no hace que el stock se detecte antes.

### Trabajo previo necesario para que los avisos sean fiables

Ahora `check_stock.py` y los scrapers de juegos envían directamente a Telegram y conservan el estado anterior si falla el envío. Con dos destinos no basta con añadir otra llamada en el mismo bloque: Discord podría fallar después de un envío correcto a Telegram y provocar que este se repita.

Separar observación de producto, evento y entrega. Guardar por evento y destino si está pendiente, enviado o fallido. El identificador debe distinguir dos reposiciones reales del mismo producto; no sirve deduplicar solo por ASIN. Persistirlo con el sistema actual, sin obligar a migrar a base de datos. Reintentar únicamente destinos pendientes y fijar caducidad para no publicar como nueva una reposición que ya perdió sentido.

Para Discord: pedir confirmación de creación con `wait=true`, respetar `retry_after` en 429, limitar reintentos de red/5xx y detener reintentos inútiles ante un webhook eliminado. Limitar longitud de contenido, impedir menciones involuntarias con `allowed_mentions`, guardar la URL del webhook como secreto y no escribirla en logs. [Ejecución de webhooks](https://docs.discord.com/developers/resources/webhook#execute-webhook), [límites](https://docs.discord.com/developers/topics/rate-limits).

No prometer entrega exactamente una vez: un timeout después de que el servidor acepte un mensaje puede dejar un resultado ambiguo. Registrar y tratar ese caso, en lugar de reintentar ciegamente todos los destinos.

Validación: datos simulados primero; casos Telegram correcto/Discord fallido y viceversa; repetición de un evento; segunda reposición legítima; webhook inexistente; respuesta 429; títulos largos; DRY_RUN sin envíos ni consumo de estado. La prueba en canales reales se hará cuando el usuario facilite y autorice los destinos.

### Segunda fase: comandos útiles

Si las alertas se usan, añadir `/stock`, `/ofertas` y `/buscar`, consultando los snapshots publicados. Los comandos necesitan una aplicación y un receptor de interacciones; puede ser HTTP, sin conexión permanente al Gateway. Validar firmas y atender los plazos de respuesta indicados por Discord. [Interacciones](https://docs.discord.com/developers/interactions/overview), [respuestas](https://docs.discord.com/developers/interactions/receiving-and-responding).

Roles por juego y menciones voluntarias serían otra opción. Alertas personales por producto/precio requieren preferencias persistentes y decisiones adicionales; no forman parte del primer envío por webhooks.

## 3. «Volver» debe recuperar el listado de origen

La URL del listado ya conserva filtros y el botón Atrás del navegador funciona. Sin embargo, `producto.js:setBackLink()` siempre usa el catálogo general del juego. Desde Ofertas, una ficha de Pokémon ofrece `/pokemontcg`, perdiendo la selección de Ofertas. Comprobado en la plantilla dinámica con producto simulado.

Conservar origen interno y filtros al abrir una ficha, con una alternativa razonable para enlaces compartidos o pestañas nuevas. Validar que el destino sea interno. Probar desde Ofertas, Favoritos y categorías; el enlace canónico del producto debe seguir siendo limpio.

## 4. Cero resultados y filtros

`app.js:render()` vacía el contador y los HTML muestran «No se encontraron productos.». No hay botón global «Limpiar filtros». Reproducido con `/ofertas?q=pokemon&price=5` y un producto simulado de 10 €.

Mostrar «0 productos», texto orientativo y un botón para restaurar **los valores iniciales de esa página**. No activar todas las categorías al reiniciar una página de Cajas de Colección: eso convertiría su listado en otro distinto. Respetar también el descuento mínimo inicial de Ofertas y limpiar los parámetros correspondientes de la URL.

Separar visualmente una búsqueda vacía de un fallo de carga; para este último ofrecer reintento. Comunicar cambios del contador a tecnologías de asistencia sin saturar cada pulsación.

## 5. Menos avisos repetitivos

Los scrapers incluyen `stock_decreased` en la condición de envío: bajar de 8 a 7 unidades puede producir otro aviso aun sin cambiar precio o disponibilidad. El historial público ya excluye ese evento aislado, pero Telegram lo sigue enviando.

Propuesta a decidir: avisar al cruzar una vez un umbral de pocas unidades, en lugar de cada bajada; reposiciones siempre; bajadas de precio con un mínimo configurable. No fijar porcentajes o unidades sin acordarlos. Compartir las reglas entre Telegram y Discord. Conservar las observaciones completas aunque no merezcan un mensaje.

## 6. Compartir productos

Añadir «Compartir» / «Copiar enlace» en fichas, usando la URL canónica. La ficha `producto.html` no incluye metadatos Open Graph propios y `build_catalog.render_page()` no los genera: preparar título, imagen del producto y descripción en el HTML estático para los lectores de enlaces de Telegram/Discord. Una vista previa con precio puede quedar cacheada; priorizar producto y tienda o indicar fecha si se incluye.

Probar compartir disponible/copia alternativa y evitar parámetros de retorno en el enlace compartido. Las tarjetas de redes necesitan metadatos estáticos; cambiar `document.title` con JavaScript no basta para todos los lectores.

## 7. Optimizar la carga de fichas

`producto.js:findProduct()` pide los seis snapshots mediante `Promise.allSettled`, incluso cuando el HTML contiene `_src` y ya sabemos de qué juego es el producto. El render inicial incrustado ayuda, pero el refresco posterior espera a que terminen todas las fuentes, cada una con timeout de 15 segundos en `fetchStock`.

Primero consultar la fuente conocida; ampliar la búsqueda solo cuando sea necesario y mantener los casos de productos compartidos/archivados. Cargar estadísticas sin bloquear la actualización principal. Los seis JSON sumaban aproximadamente 174 kB sin compresión en la revisión: no es una emergencia de volumen; el interés principal es aislar fuentes lentas. Medir solicitudes y tiempos con una fuente ajena retenida antes/después.

## 8. Incidencias privadas

`monitor_health.py` ya detecta fallos y escribe el resumen de GitHub Actions. Añadir, si se quiere, un destino privado con un aviso al empezar una incidencia y otro al recuperarse. Evitar un mensaje por hora sobre la misma incidencia. También valorar cambios bruscos de cantidad de productos como señal que requiera revisión, no como prueba automática de fallo.

Esto aprovecha el monitor existente y respeta la decisión de no mostrar avisos de antigüedad en la web. Destino privado y activación por decidir; no enviar nada durante esta revisión.

## 9. Favoritos sin cuenta

Exportar un archivo con los identificadores y nombres guardados; importar validando formato, límite de tamaño y duplicados. Previsualizar cuántos se añadirán y mezclar sin borrar los actuales. Útil para pasar del ordenador al móvil sin Google login ni servidor. No es sincronización automática y debe presentarse como copia/traslado.

## 10. Mantenimiento que acompaña a las funciones

- La CI web filtra cambios de JS/HTML/CSS/pruebas, pero un cambio solo en `assets/brand/` o `wrangler.toml` no la activa. Ampliar los paths para esos archivos y revisar existencia/carga de recursos importantes.
- Cabecera, navegación y metadatos comunes están repetidos en los HTML de la raíz. `producto.html` sí es plantilla para las fichas. Llevar los elementos comunes a una generación estática gradual, sin convertir todo el proyecto en una SPA. Hacerlo por separado de cambios de diseño para facilitar la revisión.
- La fuente Manrope local es TTF; preparar WOFF2/subconjunto para la web conservando caracteres necesarios y licencia. Medir primero y evitar precargar todas las variantes por defecto.

## Pendientes que siguen aplazados

API/base de datos; scraping al minuto e infraestructura permanente; Google login; avisos públicos de salud del stock; cambio del enlace de Telegram hasta conocer la URL de la comunidad. El símbolo general nuevo sigue en exploración: no se ha sustituido el publicado.

Las issues consultadas siguen incluyendo afiliaciones, Toys R Us, la creación de la web, ocultar `products.json` y eliminar productos antiguos. Revisarlas contra el estado actual: la web ya existe y las fichas archivadas tienen un propósito. No borrar el catálogo histórico ni tratar un JSON público que necesita el navegador como si fuese un secreto.

## Evidencias de esta revisión

Código leído en ambos repositorios y issues abiertas consultadas; reproducciones con Chromium y feeds simulados en `debug/review_backlog.py`, resultados en `debug/backlog-evidence.json`. No es una auditoría exhaustiva de seguridad o rendimiento ni una ejecución de los scrapers contra tiendas. Los hallazgos reproducidos se distinguen de ideas y riesgos preventivos en cada apartado.

Siguiente sesión sugerida: corregir precios en un commit; cerrar vuelta al listado y reinicio de filtros; preparar Discord con simulaciones y revisar una tarjeta de alerta antes de activarlo.
