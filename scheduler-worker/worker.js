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

async function dispatch(workflow, token) {
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

export default {
  // No sirve para nada por sí solo (este worker no tiene rutas propias) —
  // pero sin un fetch() exportado, Cloudflare no puede interceptar
  // /__scheduled?cron=... para forzar una prueba manual de scheduled().
  async fetch(request) {
    return new Response(
      "Este worker no atiende peticiones normales, solo dispara checks por cron.\n" +
      "Prueba manual: /__scheduled?cron=5+*+*+*+*\n",
      { status: 200 }
    );
  },

  async scheduled(event, env, ctx) {
    const workflows = CRON_TO_WORKFLOWS[event.cron];
    if (!workflows) {
      console.error(`Cron sin mapear a ningún workflow: "${event.cron}"`);
      return;
    }

    const results = await Promise.allSettled(
      workflows.map((workflow) => dispatch(workflow, env.GITHUB_TOKEN))
    );

    const failed = results
      .map((result, i) => ({ result, workflow: workflows[i] }))
      .filter(({ result }) => result.status === "rejected");

    for (const { result, workflow } of failed) {
      console.error(`No se pudo disparar ${workflow}: ${result.reason}`);
    }
    console.log(`Disparados ${results.length - failed.length}/${results.length} workflows (cron "${event.cron}")`);

    if (failed.length) {
      throw new Error(`${failed.length}/${results.length} disparos fallaron para el cron "${event.cron}"`);
    }
  },
};
