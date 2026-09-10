# Scheduler Worker

Cloudflare Worker con Cron Triggers que dispara los workflows de GitHub Actions
(`workflow_dispatch`) a su hora, en vez de depender del `schedule:` de GitHub —
que se retrasa o se salta ejecuciones bajo carga (ver `project_monitor_disabled`
en memoria: los checks reales llegaban 4-5h tarde).

No sustituye el trabajo (scraping, Telegram, publicación) — eso lo sigue
haciendo GitHub Actions exactamente igual que ahora. Este worker solo llama al
botón de "Run workflow" a la hora correcta, desde un reloj que sí es fiable.

## Por qué así y no un VPS entero

Cero migración: mismo código, mismos secretos (siguen en GitHub Secrets), mismo
`publish_updates.py`. Si algún día se quiere independencia total de GitHub
Actions, un VPS sigue siendo la opción — pero primero merece la pena confirmar
que el problema era solo el disparador, no la ejecución en sí.

## Instalación (vía panel de Cloudflare, sin CLI)

1. dash.cloudflare.com → **Workers y Pages** → **Crear** → **Crear Worker**.
   Nombre sugerido: `pokestock-scheduler`.
2. Pega el contenido de `worker.js` en el editor (Quick Edit) y despliega.
3. **Settings → Variables and Secrets** → añade `GITHUB_TOKEN` (tipo *Secret*,
   no *Text*) con el token que se genera en el paso siguiente.
4. **Settings → Trigger Events → Cron Triggers** → añade estos 6, exactamente
   como están (mismos horarios que ya usan los workflows):
   - `17 * * * *` (Pokémon)
   - `5 * * * *` (One Piece)
   - `15 */6 * * *` (Magic)
   - `25 */6 * * *` (Lorcana)
   - `35 */6 * * *` (Yu-Gi-Oh!)
   - `0 6 * * *` (Accesorios)

## El token de GitHub

github.com → foto de perfil → **Settings → Developer settings → Personal
access tokens → Fine-grained tokens → Generate new token**.

- **Repository access**: Only select repositories → `pokestock-tcg-bot`.
- **Permissions → Actions**: Read and write. (Nada más hace falta.)
- Expiración: la que prefieras (recuerda renovarlo antes de que caduque, o el
  worker empezará a fallar en silencio — mira los logs del Worker si un día
  algo no se dispara).

Copia el token y pégalo en el secreto `GITHUB_TOKEN` del paso 3 — no lo
guardes en ningún fichero del repo.

## Verificar que funciona

En el panel del Worker, pestaña **Cron Triggers**, hay un botón para forzar
una ejecución ("Trigger event"). O simplemente espera a la siguiente hora en
punto y comprueba en GitHub → Actions que el workflow correspondiente aparece
como disparado por "workflow_dispatch" en vez de "schedule".
