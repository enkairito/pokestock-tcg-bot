"""Publica datos sobre la última versión remota, conservando cambios ajenos.

Cada reintento usa un worktree temporal nuevo. Los snapshots observados se
mantienen en memoria, pero accesorios y sitemap se vuelven a combinar con
la versión remota actual. El checkout que ejecutó el scraper no se modifica.
"""
import argparse
import json
import subprocess
import tempfile
from pathlib import Path

from merge_accesorios import SOURCES, merge_snapshots
from update_sitemap_lastmod import update_sitemap
from build_catalog import SOURCES as CATALOG_SOURCES, build_site


class PublicationError(RuntimeError):
    pass


def git(repo, *args, check=True):
    return subprocess.run(
        ["git", *args], cwd=repo, check=check, capture_output=True,
        text=True, encoding="utf-8",
    )


def target_path(worktree, name):
    target = (worktree / name).resolve()
    if target == worktree or not target.is_relative_to(worktree) or ".git" in Path(name).parts:
        raise ValueError(f"Ruta de publicación inválida: {name}")
    return target


def prepare_updates(copies, accessories=None, source=None, sitemap=False):
    """Carga los resultados antes de hacer fetch; un fichero ausente es un error."""
    contents = {destination: Path(origin).read_bytes() for origin, destination in copies}
    accessory_snapshot = json.loads(Path(accessories).read_text(encoding="utf-8")) if accessories else None
    if accessories and source not in SOURCES:
        raise ValueError("El snapshot de accesorios requiere un origen válido")

    def apply(worktree):
        changed = []
        for name, content in contents.items():
            path = target_path(worktree, name)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
            changed.append(name)
        if accessory_snapshot is not None:
            path = target_path(worktree, "accesorios.json")
            base = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"products": []}
            merged = merge_snapshots(base, accessory_snapshot, source)
            path.write_text(json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8")
            changed.append("accesorios.json")
        if sitemap:
            update_sitemap(target_path(worktree, "sitemap.xml"))
            changed.append("sitemap.xml")
            catalog_sources = [name for name in contents if name in CATALOG_SOURCES]
            if accessory_snapshot is not None:
                catalog_sources.append("accesorios.json")
            if catalog_sources:
                changed.extend(build_site(worktree, catalog_sources))
        return changed

    return apply


def publish(repo, apply, message, branch="main", attempts=3):
    repo = Path(repo).resolve()
    if attempts < 1:
        raise ValueError("Se requiere al menos un intento de publicación")
    git(repo, "check-ref-format", f"refs/heads/{branch}")
    for attempt in range(1, attempts + 1):
        git(repo, "fetch", "origin", branch)
        with tempfile.TemporaryDirectory(prefix="wts-publish-") as directory:
            worktree = (Path(directory) / "checkout").resolve()
            git(repo, "worktree", "add", "--detach", str(worktree), "FETCH_HEAD")
            try:
                paths = apply(worktree)
                if not paths:
                    raise ValueError("No hay archivos para publicar")
                git(worktree, "add", "--", *paths)
                diff = git(worktree, "diff", "--cached", "--quiet", check=False)
                if diff.returncode == 0:
                    print("Sin cambios: los datos ya están publicados")
                    return False
                if diff.returncode != 1:
                    raise PublicationError("No se pudo comprobar el contenido a publicar")
                git(worktree, "-c", "user.name=github-actions[bot]", "-c",
                    "user.email=github-actions[bot]@users.noreply.github.com",
                    "commit", "-m", message)
                result = git(worktree, "push", "origin", f"HEAD:refs/heads/{branch}", check=False)
                if result.returncode == 0:
                    print(f"Publicación completada (intento {attempt}/{attempts})")
                    return True
                print(f"Push fallido ({attempt}/{attempts}); se volverán a aplicar los datos sobre el remoto actual")
            finally:
                # Solo se elimina el worktree creado dentro de nuestro directorio
                # temporal; nunca el checkout del scraper ni una carpeta del usuario.
                git(repo, "worktree", "remove", "--force", str(worktree))
    raise PublicationError(f"No se pudo publicar después de {attempts} intentos")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True)
    parser.add_argument("--message", required=True)
    parser.add_argument("--copy", nargs=2, action="append", default=[], metavar=("SOURCE", "DESTINATION"))
    parser.add_argument("--accessories")
    parser.add_argument("--source", choices=SOURCES)
    parser.add_argument("--sitemap", action="store_true")
    args = parser.parse_args()
    apply = prepare_updates(args.copy, args.accessories, args.source, args.sitemap)
    publish(args.repo, apply, args.message)


if __name__ == "__main__":
    main()
