// Dispara los workflows de check de stock por su hora exacta, vía la API de
// GitHub (workflow_dispatch), en vez de confiar en el "schedule:" de GitHub
// Actions — que se retrasa o se salta ejecuciones bajo carga. Ver README.md.

const REPO = "enkairito/pokestock-tcg-bot";

// El plan gratis de Cloudflare Workers permite 5 Cron Triggers por CUENTA
// (no por worker), así que hay que repartir 9 categorías en 5 disparos.
// Gaming (Nintendo/PlayStation/Xbox) tiene su propio disparo cada 2h en vez
// de compartir el de 6h con las TCG "tranquilas" (Magic/Lorcana/Yu-Gi-Oh!):
// un restock de PS5/Switch 2 vuela en minutos, así que le hace falta más
// frecuencia — parecida a Pokémon/One Piece, aunque sin su propio hueco
// horario dedicado por no gastar el último trigger libre en solo eso.
const CRON_TO_WORKFLOWS = {
  "17 * * * *": ["check_stock.yml"],
  "5 * * * *": ["check_onepiece.yml"],
  "15 */6 * * *": ["check_magic.yml", "check_lorcana.yml", "check_yugioh.yml"],
  "30 */2 * * *": ["check_nintendo.yml", "check_playstation.yml", "check_xbox.yml"],
  "0 6 * * *": ["check_accessories.yml"],
};

async function dispatchOne(workflow, token) {
  const response = await fetch(
    `https://api.github.com/repos/${REPO}/actions/workflows/${workflow}/dispatches`,
    {
      method: "POST",
      headers: {
        Authorization: `Bearer ${token}`,
        Accept: "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "pokestock-scheduler-worker",
      },
      body: JSON.stringify({ ref: "main" }),
    }
  );
  if (!response.ok) {
    const body = await response.text();
    throw new Error(`${response.status} ${body}`);
  }
}

// Común a scheduled() y a la ruta de prueba manual — misma lógica, dos
// disparadores distintos (uno el cron real, otro un curl a mano).
async function dispatchAll(cron, token) {
  const workflows = CRON_TO_WORKFLOWS[cron];
  if (!workflows) {
    return { ok: false, message: `Cron sin mapear a ningún workflow: "${cron}"` };
  }
  const results = await Promise.allSettled(workflows.map((w) => dispatchOne(w, token)));
  const lines = results.map((result, i) =>
    result.status === "fulfilled"
      ? `OK  ${workflows[i]}`
      : `ERR ${workflows[i]}: ${result.reason}`
  );
  const failedCount = results.filter((r) => r.status === "rejected").length;
  return { ok: failedCount === 0, message: lines.join("\n"), failedCount, total: results.length };
}

// GITHUB_TOKEN puede llegar como secreto de texto plano clásico (string) o
// como binding de Secrets Store (objeto con .get()) — depende de cómo se
// haya conectado en el panel. Aceptar los dos evita otra ronda de "Bad
// credentials" si algún día se reconecta de otra forma.
async function resolveToken(env) {
  const binding = env.GITHUB_TOKEN;
  if (typeof binding === "string") return binding;
  if (binding && typeof binding.get === "function") return await binding.get();
  throw new Error(`GITHUB_TOKEN no es ni string ni tiene .get() — typeof: ${typeof binding}, valor: ${JSON.stringify(binding)}`);
}

export default {
  // Sin ruta propia real: solo sirve para forzar una prueba manual sin
  // esperar a la hora del cron, ya que /__scheduled solo funciona con
  // `wrangler dev` en local, no contra el worker ya desplegado.
  // Uso: /?test=5+*+*+*+*  (con el cron exacto, entre los 5 de CRON_TO_WORKFLOWS)
  async fetch(request, env) {
    const cron = new URL(request.url).searchParams.get("test");
    if (!cron) {
      return new Response(
        "Este worker no atiende peticiones normales, solo dispara checks por cron.\n" +
        "Prueba manual: /?test=5+*+*+*+* (cron exacto, con espacios como '+')\n",
        { status: 200 }
      );
    }
    try {
      const token = await resolveToken(env);
      const result = await dispatchAll(cron, token);
      return new Response(result.message, { status: result.ok ? 200 : 500 });
    } catch (error) {
      return new Response(`EXCEPCIÓN: ${error.message}`, { status: 500 });
    }
  },

  async scheduled(event, env, ctx) {
    const token = await resolveToken(env);
    const result = await dispatchAll(event.cron, token);
    console.log(result.message);
    if (!result.ok) {
      throw new Error(`${result.failedCount}/${result.total} disparos fallaron para el cron "${event.cron}"`);
    }
  },
};
