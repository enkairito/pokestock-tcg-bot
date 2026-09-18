# Agrupación de productos y comparación de precios

El agrupador distingue entre el **producto real** y las **ofertas de tienda**.
Su alcance actual es exclusivamente **Pokémon TCG**: el resto de juegos,
Gaming y accesorios ajenos no entran en los grupos ni en la cola manual.

- Una oferta mantiene su URL actual: `/producto/{tienda-id}`.
- Un producto confirmado tendrá una URL comparadora estable:
  `/producto/{group-id}`.
- Las URLs de oferta no se eliminan ni redirigen automáticamente.

## Archivos generados

- `product-groups.json`: grupos confirmados automáticamente o manualmente.
- `grouping-review.json`: ofertas pendientes y hasta tres candidatos sugeridos.
- `product-group-overrides.json`: decisiones humanas; nunca se sobrescribe.

El agrupador solo fusiona automáticamente:

1. EAN, GTIN, UPC o código de barras idénticos; o
2. una firma normalizada idéntica entre marketplaces distintos.

Una similitud de título solo genera una sugerencia. No fusiona productos.
Idioma, tipo de producto, cantidad de sobres/unidades y códigos de expansión
incompatibles bloquean incluso la sugerencia.

## Decisiones manuales

Ejemplo de `product-group-overrides.json`:

```json
{
  "version": 1,
  "groups": {
    "pokemon-mega-zygarde-premium-es": {
      "name": "Colección Premium Mega-Zygarde ex",
      "game": "Pokémon",
      "language": "espanol"
    }
  },
  "assign": {
    "ECI-A200805922": "pokemon-mega-zygarde-premium-es",
    "TRU-K1104392": "pokemon-mega-zygarde-premium-es"
  },
  "separate": [],
  "ignore": [],
  "reject_pairs": []
}
```

Opciones:

- `assign`: asigna una oferta a un grupo. Tiene prioridad absoluta.
- `groups`: permite fijar nombre, juego, categoría o idioma del grupo.
- `separate`: producto revisado que debe permanecer independiente.
- `ignore`: falso positivo o entrada que no debe participar.
- `reject_pairs`: parejas revisadas que no representan el mismo producto.

## Revisor visual

El panel local muestra las ofertas y sus fotos una al lado de la otra. Guarda
cada decisión de forma atómica en `product-group-overrides.json` y permite
deshacer las últimas decisiones de la sesión.

Desde este repositorio:

```powershell
python grouping_reviewer.py --web ..\wheresthatstock
```

En Windows también se puede abrir `review_groups.bat` con doble clic.

Se abrirá `http://127.0.0.1:8765/`. El servidor solo escucha en el equipo
local y utiliza un token aleatorio para proteger las escrituras. Atajos:
`M` mismo producto, `N` no coincide, `U` producto único e `I` ignorar.

Una misma oferta no puede aparecer a la vez en `assign`, `separate` o
`ignore`. El generador falla de forma visible si detecta decisiones
contradictorias, evitando publicar comparadores incorrectos.

## Flujo de revisión

1. Abrir `grouping-review.json`.
2. Revisar primero los elementos con `reason: "possible_matches"`.
3. Copiar el `offer_id` y el `group_id` sugerido a `assign`, o crear un ID
   descriptivo nuevo en `groups`.
4. Si no corresponde con nada, añadirlo a `separate`.
5. En la siguiente ejecución desaparece de la cola y la decisión queda
   persistida.

Ejecutar una auditoría local sin modificar el frontend:

```powershell
python product_grouping.py --web C:\ruta\wheresthatstock --output C:\ruta\revision
```
