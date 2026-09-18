# Agrupación de productos y comparación de precios

El agrupador distingue entre el **producto real** y las **ofertas de tienda**.
Su alcance actual es exclusivamente **Pokémon TCG**: el resto de juegos,
Gaming y accesorios ajenos no entran en los grupos ni en la cola manual.

- Una oferta mantiene su URL actual: `/producto/{tienda-id}`.
- Un producto confirmado tendrá una URL comparadora estable:
  `/comparar/{group-id}`.
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
  "ignore": []
}
```

Opciones:

- `assign`: asigna una oferta a un grupo. Tiene prioridad absoluta.
- `groups`: permite fijar nombre, juego, categoría o idioma del grupo.
- `separate`: producto revisado que debe permanecer independiente.
- `ignore`: falso positivo o entrada que no debe participar.

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
