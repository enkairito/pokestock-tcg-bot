// Dispara los workflows de check de stock por su hora exacta, vía la API de
// GitHub (workflow_dispatch), en vez de confiar en el "schedule:" de GitHub
// Actions — que se retrasa o se salta ejecuciones bajo carga. Ver README.md.

const REPO = "enkairito/pokestock-tcg-bot";

const CRON_TO_WORKFLOW = {
  "17 * * * *": "check_stock.yml",
  "5 * * * *": "check_onepiece.yml",
  "15 */6 * * *": "check_magic.yml",
  "25 */6 * * *": "check_lorcana.yml",
  "35 */6 * * *": "check_yugioh.yml",
  "0 6 * * *": "check_accessories.yml",
};

export default {
  async scheduled(event, env, ctx) {
    const workflow = CRON_TO_WORKFLOW[event.cron];
    if (!workflow) {
      console.error(`Cron sin mapear a ningún workflow: "${event.cron}"`);
      return;
    }

    const response = await fetch(
      `https://api.github.com/repos/${REPO}/actions/workflows/${workflow}/dispatches`,
      {
        method: "POST",
        headers: {
          Authorization: `Bearer ${env.GITHUB_TOKEN}`,
          Accept: "application/vnd.github+json",
          "X-GitHub-Api-Version": "2022-11-28",
          "User-Agent": "pokestock-scheduler-worker",
        },
        body: JSON.stringify({ ref: "main" }),
      }
    );

    if (!response.ok) {
      const body = await response.text();
      // Lanzar el error hace que Cloudflare lo marque como fallo y lo
      // muestre en los logs del Worker — no se reintenta el scraping en
      // sí, solo esta llamada de disparo.
      throw new Error(`No se pudo disparar ${workflow}: ${response.status} ${body}`);
    }

    console.log(`Disparado ${workflow} correctamente (cron "${event.cron}")`);
  },
};
