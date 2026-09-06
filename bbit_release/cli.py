from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import typer

from ._version import read_version
from .cache import get_cache
from .config import Config, win_to_posix
from .logger import info, success, warn, die

app = typer.Typer(
    name="bbit",
    help="BBit Release Manager - Bitbucket sync CLI y web",
    no_args_is_help=True,
)


@app.command()
def version():
    """Show the installed version."""
    try:
        from importlib.metadata import version as _v
        ver = _v("bbit-release-manager")
    except Exception:
        ver = read_version()
    info(f"bbit-release-manager v{ver}")


@app.command()
def config():
    """Show the current connection (config stored in data/cache.db)."""
    cfg = Config()
    if get_cache().get_connection() is None:
        warn("Sin conexión guardada — ejecutá 'bbit login' o conectate desde la web.")
        return
    info("[bold]=== CONEXIÓN (data/cache.db) ===[/bold]")
    info(f"  Bitbucket URL:  {cfg.bitbucket_url}")
    info(f"  Workspace:      {cfg.workspace or '(no set)'}")
    info(f"  Token BB:       {'set' if cfg.bitbucket_token else 'no'}")
    info(f"  Token Circle:   {'set' if cfg.circleci_token else 'no'}")
    info(f"  Default branch: {cfg.default_branch}")
    info(f"  Repos filter:   {', '.join(cfg.repos) or '(all)'}")
    info(f"  Proj prefixes:  {', '.join(cfg.project_prefixes) or '(all)'}")
    info(f"  Excluidos:      {', '.join(cfg.exclude_repos) or '(none)'}")
    info(f"  SSM prefixes:   {', '.join(cfg.ssm_prefixes)}")
    info(f"  Deploy envs:    {', '.join(cfg.deploy_prefixes)}")
    info(f"  Frontend:       {win_to_posix(str(cfg.frontend_root))}")


def _probe_bitbucket(workspace: str, token: str, url: str = "") -> tuple[bool, str, str, int]:
    """Abre sesión Bitbucket Cloud. Devuelve (ok, detalle, error, repo_count)."""
    from .bitbucket.client import BitbucketAuthError, BitbucketClient, BitbucketError

    with BitbucketClient(workspace, token, url=url) as client:
        try:
            info, identity = client.session()
        except (BitbucketAuthError, BitbucketError) as exc:
            return False, "", str(exc), 0
    detail = f"{identity} · {info.name} {'(privado)' if info.is_private else ''}"
    return True, detail, "", 0


@app.command()
def login(
    workspace: str = typer.Option("", "--workspace", help="Workspace de Bitbucket"),
    token: str = typer.Option("", "--token", help="App password o PAT de Bitbucket"),
    circleci_token: str = typer.Option(
        "", "--circleci-token", help="Token de CircleCI (opcional)"
    ),
):
    """Guardar credenciales en la fila de conexión (data/cache.db).

    Sin opciones, pregunta interactivamente por workspace, token de
    Bitbucket y token de CircleCI. Valida contra la API antes de guardar.
    """
    cfg = Config()
    ws = workspace or cfg.workspace
    tok = token
    cci = circleci_token
    if not tok:
        try:
            ws = input(f"Workspace [{ws or ''}]: ").strip() or ws
            tok = input("Bitbucket token (app password o PAT): ").strip()
            if not cci:
                cci = input("CircleCI token (opcional, Enter para omitir): ").strip()
        except (EOFError, KeyboardInterrupt):
            die("Login cancelado.")
    if not ws:
        die("Workspace obligatorio.")
    if not tok:
        die("Token de Bitbucket obligatorio.")

    if cci:
        from .circleci.client import CircleCiClient, CircleCiError
        ci = CircleCiClient(cci, vcs=cfg.circleci_vcs or "bb", org=cfg.circleci_org or ws)
        try:
            ci.me()
        except (CircleCiError, ValueError) as exc:
            die(f"Token de CircleCI inválido: {exc}")
        finally:
            ci.close()

    ok, detail, err, _ = _probe_bitbucket(ws, tok, cfg.bitbucket_url)
    if not ok:
        die(err)
    cfg.save_tokens(bitbucket_token=tok, circleci_token=cci, workspace=ws)
    success(f"Conexión guardada — {detail}")
    info("Siguiente paso: 'bbit session' para listar repos o 'bbit web' para la UI.")


@app.command()
def session():
    """Abrir sesión contra Bitbucket Cloud: valida token y lista los repos."""
    cfg = Config()
    if not cfg.is_configured:
        die(
            "Bitbucket no configurado. Ejecutá 'bbit login' (o conectate desde la web)."
        )

    info(
        f"Conectando a {cfg.bitbucket_url} "
        f"(workspace '{cfg.workspace}')..."
    )
    ok, detail, err, count = _probe_bitbucket(
        cfg.workspace, cfg.bitbucket_token, cfg.bitbucket_url
    )
    if not ok:
        die(err)

    success(f"Sesión OK — {detail}")
    info(f"Repos del workspace '{cfg.workspace}': {count}")

    with BitbucketClient(cfg.workspace, cfg.bitbucket_token, url=cfg.bitbucket_url) as client:
        repos = client.list_repos(filter_names=cfg.repos or None, prefixes=cfg.project_prefixes or None)
        if cfg.exclude_repos:
            blocked = {s.lower() for s in cfg.exclude_repos}
            repos = [r for r in repos if r.slug.lower() not in blocked]

    from rich.table import Table
    from rich import box

    table = Table(box=box.SIMPLE)
    table.add_column("Repo", style="cyan")
    table.add_column("Nombre")
    for repo in sorted(repos, key=lambda r: r.slug):
        table.add_row(repo.slug, repo.name)
    from .logger import console
    console.print(table)


@app.command()
def repos(
    origin: str = typer.Option(..., "--origin", help="Branch origen a buscar"),
):
    """Resolver repos que contienen la rama origen."""
    cfg = Config()
    if not cfg.is_configured:
        die("Bitbucket no configurado. Ejecutá 'bbit login'.")
    with BitbucketClient(cfg.workspace, cfg.bitbucket_token, url=cfg.bitbucket_url) as client:
        found = client.repos_with_branch(origin, prefixes=cfg.project_prefixes or None)
    if not found:
        warn(f"Ningún repo contiene la rama '{origin}'.")
        return
    if cfg.exclude_repos:
        found = [r for r in found if r.slug.lower() not in {s.lower() for s in cfg.exclude_repos}]
    success(f"Repos con '{origin}': {len(found)}")
    from rich.table import Table
    from rich import box
    table = Table(box=box.SIMPLE)
    table.add_column("Repo", style="cyan")
    table.add_column("Rama default")
    for repo in sorted(found, key=lambda r: r.slug):
        table.add_row(repo.slug, repo.default_branch)
    from .logger import console
    console.print(table)


@app.command()
def diff(
    origin: str = typer.Option(..., "--origin", help="Branch origen"),
    destination: str = typer.Option("master", "--destination", help="Branch destino"),
):
    """Mostrar diff de cada repo que tiene la rama origen contra destino."""
    cfg = Config()
    if not cfg.is_configured:
        die("Bitbucket no configurado. Ejecutá 'bbit login'.")
    with BitbucketClient(cfg.workspace, cfg.bitbucket_token, url=cfg.bitbucket_url) as client:
        found = client.repos_with_branch(origin, prefixes=cfg.project_prefixes or None)
        if not found:
            warn(f"Ningún repo contiene la rama '{origin}'.")
            return
        if cfg.exclude_repos:
            blocked = {s.lower() for s in cfg.exclude_repos}
            found = [r for r in found if r.slug.lower() not in blocked]
        from rich.table import Table
        from rich import box
        for repo in sorted(found, key=lambda r: r.slug):
            d = client.diff(repo.slug, destination, origin)
            info(f"[bold cyan]{repo.slug}[/bold cyan]  {destination} → {origin}")
            if not d.files:
                info("  (sin cambios)")
                continue
            table = Table(box=box.SIMPLE)
            table.add_column("Archivo", style="cyan")
            table.add_column("Estado")
            table.add_column("+", justify="right", style="green")
            table.add_column("-", justify="right", style="red")
            for f in d.files:
                table.add_row(f.path, f.status, str(f.lines_added), str(f.lines_removed))
            from .logger import console
            console.print(table)


@app.command()
def home():
    """Show the project root path."""
    print(win_to_posix(str(Path(__file__).resolve().parent.parent)))


def _frontend_needs_build() -> bool:
    """True si el frontend dist/browser falta o sus fuentes son más nuevas."""
    frontend = Config().frontend_root
    if not (frontend / "package.json").exists():
        return False
    index = frontend / "dist" / "browser" / "index.html"
    if not index.exists():
        return True
    if not _have_tool("npm"):
        return False
    source_files: list[Path] = [
        frontend / "package.json",
        frontend / "angular.json",
        frontend / "package-lock.json",
        frontend / "node_modules" / ".package-lock.json",
    ]
    for cfg in frontend.glob("tsconfig*.json"):
        source_files.append(cfg)
    src = frontend / "src"
    if src.exists():
        source_files.append(src)
    newest = 0.0
    for source in source_files:
        if not source.exists():
            continue
        try:
            if source.is_dir():
                newest = max(
                    newest,
                    max(p.stat().st_mtime for p in source.rglob("*") if p.is_file()),
                )
            else:
                newest = max(newest, source.stat().st_mtime)
        except OSError:
            continue
    return newest > index.stat().st_mtime


def _have_tool(name: str) -> bool:
    import shutil
    return shutil.which(name) is not None


def _build_web_frontend() -> None:
    frontend = Config().frontend_root
    npm = _have_tool("npm") and __import__("shutil").which("npm")
    if not npm:
        die("npm no está instalado — instalá Node.js (>= 24) para compilar el frontend")
    if not (frontend / "package.json").exists():
        die(f"No se encontró frontend/ en {frontend}")
    info("Compilando frontend Angular (frontend/ -> dist/browser)...")
    result = subprocess.run(
        [npm, "run", "build"],
        cwd=str(frontend),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if result.returncode != 0:
        die(f"Falló el build del frontend:\n{result.stdout}\n{result.stderr}")
    success("Frontend compilado.")


@app.command()
def web(
    port: int = typer.Option(8000, "--port", "-p", help="Puerto de la API"),
    dev: bool = typer.Option(
        False, "--dev", "-d",
        help="Modo interactivo: uvicorn --reload (backend) + ng serve (HMR frontend)",
    ),
    no_browser: bool = typer.Option(
        False, "--no-browser", help="No abrir el navegador automáticamente"
    ),
    build: bool | None = typer.Option(
        None,
        "--build/--no-build",
        help="Compilar el frontend Angular. Por defecto se compila "
        "solo si falta dist/browser o el código está desactualizado.",
    ),
):
    """Levantar la web de BBit (SPA + API)."""
    if dev:
        from .web.run import run_dev
        run_dev(port=port, open_browser=not no_browser)
        return
    if build or (build is None and _frontend_needs_build()):
        _build_web_frontend()
    from .web.run import run
    run(port=port, open_browser=not no_browser)


def _check_python_version() -> bool:
    import platform
    major, minor = platform.python_version_tuple()[:2]
    return int(major) > 3 or (int(major) == 3 and int(minor) >= 14)


def _check_node_version() -> tuple[bool, str]:
    node = _have_tool("node")
    if not node:
        return False, ""
    result = subprocess.run(["node", "--version"], capture_output=True, text=True)
    raw = (result.stdout or "").strip().lstrip("v")
    try:
        major = int(raw.split(".")[0])
    except (ValueError, IndexError):
        return False, raw
    return major >= 24, raw


@app.command()
def setup(
    yes: bool = typer.Option(
        False, "--yes", "-y", help="No preguntar, usar valores por defecto"
    ),
):
    """One-time setup: dependency check + config files."""
    info("=== BBit Setup ===")
    print()

    cfg = Config()

    # 1. Dependencias
    info("Dependencias:")
    if _check_python_version():
        success(f"  Python OK ({sys.version.split()[0]})")
    else:
        warn("  Python < 3.14 detectado — requerido >= 3.14")

    ok, node_ver = _check_node_version()
    if ok:
        success(f"  Node OK (v{node_ver})")
    else:
        warn("  Node >= 24 no detectado — necesario para el frontend")
    npm = _have_tool("npm")
    success("  npm encontrado") if npm else warn("  npm no encontrado")

    # 2. Conexión (credenciales por servicio, sin archivos env)
    print()
    info("Conexión:")
    if cfg.is_configured:
        success(f"  {cfg.workspace} ({cfg.bitbucket_url}) — credenciales en data/cache.db")
    else:
        yn = "y" if yes else input("  ¿Guardar credenciales AHORA con 'bbit login'? (Y/n): ")
        if yn.lower() != "n":
            success("  Ejecutá: bbit login")

    # 3. Bitbucket conectividad
    print()
    info("Bitbucket:")
    if cfg.bitbucket_url and cfg.workspace:
        info(f"  URL: {cfg.bitbucket_url}")
        info(f"  Workspace: {cfg.workspace}")
        if cfg.bitbucket_token:
            success("  Token: presente")
            info("  Probando sesión...")
            ok, detail, err, count = _probe_bitbucket(
                cfg.workspace, cfg.bitbucket_token, cfg.bitbucket_url
            )
            if ok:
                success(f"  Sesión OK — {detail} ({count} repos)")
            else:
                warn(f"  Sesión fallida: {err}")
        else:
            warn("  Token: no seteado — ejecutá 'bbit login' para guardarlo")
    else:
        warn(
            "  BITBUCKET_URL/BITBUCKET_WORKSPACE vacíos — ejecutá 'bbit login' "
            "y después 'bbit session'"
        )

    # 4. Frontend scaffold check
    print()
    info("Frontend:")
    frontend = cfg.frontend_root
    if (frontend / "package.json").exists():
        success(f"  Scaffolded en {win_to_posix(str(frontend))}")
    else:
        warn("  frontend/ sin scaffold — corré 'npm install' dentro de frontend/")

    print()
    success("Setup complete.")
    info("Siguiente paso: 'bbit web --dev' para levantar la web con live reload.")


if __name__ == "__main__":
    app()