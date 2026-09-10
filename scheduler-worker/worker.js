// Dispara los workflows de check de stock por su hora exacta, vía la API de
// GitHub (workflow_dispatch), en vez de confiar en el "schedule:" de GitHub
// Actions — que se retrasa o se salta ejecuciones bajo carga. Ver README.md.

const REPO = "enkairito/pokestock-tcg-bot";

// El plan gratis de Cloudflare Workers permite 5 Cron Triggers por CUENTA
// (no por worker) — con los 6 checks originales no cabía. Magic, Lorcana y
// Yu-Gi-Oh (los tres "cada 6 horas") comparten ahora un único disparo: antes
// iban 10 minutos separados solo para no golpear los runners de GitHub en
// el mismo minuto exacto, algo que deja de importar una vez el disparo no
// depende del "schedule:" de GitHub sino de este worker.
const CRON_TO_WORKFLOWS = {
  "17 * * * *": ["check_stock.yml"],
  "5 * * * *": ["check_onepiece.yml"],
  "15 */6 * * *": ["check_magic.yml", "check_lorcana.yml", "check_yugioh.yml"],
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

export default {
  // Sin ruta propia real: solo sirve para forzar una prueba manual sin
  // esperar a la hora del cron, ya que /__scheduled solo funciona con
  // `wrangler dev` en local, no contra el worker ya desplegado.
  // Uso: /?test=5+*+*+*+*  (con el cron exacto, entre los 4 de CRON_TO_WORKFLOWS)
  async fetch(request, env) {
    const cron = new URL(request.url).searchParams.get("test");
    if (!cron) {
      return new Response(
        "Este worker no atiende peticiones normales, solo dispara checks por cron.\n" +
        "Prueba manual: /?test=5+*+*+*+* (cron exacto, con espacios como '+')\n",
        { status: 200 }
      );
    }
    // GITHUB_TOKEN está enlazado vía Secrets Store (no un secreto de texto
    // plano clásico) — ese tipo de binding entrega un objeto con .get(),
    // no el string directamente. Usar el binding tal cual como token manda
    // "Authorization: Bearer [object Object]", que GitHub rechaza igual
    // que un token inválido (401 Bad credentials, indistinguible del caso
    // real hasta que se revisa esto).
    const token = await env.GITHUB_TOKEN.get();
    const result = await dispatchAll(cron, token);
    return new Response(result.message, { status: result.ok ? 200 : 500 });
  },

  async scheduled(event, env, ctx) {
    const token = await env.GITHUB_TOKEN.get();
    const result = await dispatchAll(event.cron, token);
    console.log(result.message);
    if (!result.ok) {
      throw new Error(`${result.failedCount}/${result.total} disparos fallaron para el cron "${event.cron}"`);
    }
  },
};
