from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import typer

from ._version import read_version
from .cache import DEFAULT_AWS_REGION, get_cache
from .config import Config, win_to_posix
from .logger import console, info, success, warn, die

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
    if not cfg.stored_services() and get_cache().get_connection() is None:
        warn("Sin conexión guardada — ejecutá 'bbit login' o conectate desde la web.")
        return
    states = cfg.service_states()
    info("[bold]=== CONEXIÓN (data/cache.db) ===[/bold]")
    info(f"  Cliente:        {cfg.client_alias or '(sin alias)'} — {cfg.client_id[:8]}…")
    info(f"  Bitbucket URL:  {cfg.bitbucket_url}")
    info(f"  Workspace:      {cfg.workspace or '(no set)'}")
    info(f"  Token BB:       {'set' if states['bitbucket']['stored'] else 'no'}{_expiry_suffix(states['bitbucket']['expires_at'])}")
    info(f"  Token Circle:   {'set' if states['circleci']['stored'] else 'no'}{_expiry_suffix(states['circleci']['expires_at'])}")
    info(f"  AWS:            {'profile %s · %s' % (cfg.aws_profile, cfg.aws_region) if states['aws']['stored'] else 'no'}{_expiry_suffix(states['aws']['expires_at'])}")
    info(f"  Default branch: {cfg.default_branch}")
    info(f"  Repos filter:   {', '.join(cfg.repos) or '(all)'}")
    info(f"  Proj prefixes:  {', '.join(cfg.project_prefixes) or '(all)'}")
    info(f"  Excluidos:      {', '.join(cfg.exclude_repos) or '(none)'}")
    info(f"  SSM prefixes:   {', '.join(cfg.ssm_prefixes)}")
    info(f"  Deploy envs:    {', '.join(cfg.deploy_prefixes)}")
    info(f"  Frontend:       {win_to_posix(str(cfg.frontend_root))}")


def _expiry_suffix(expires_at: float | None) -> str:
    """Sufijo legible del vencimiento opcional de credenciales TLS."""
    from datetime import datetime

    if not expires_at:
        return ""
    dt = datetime.fromtimestamp(expires_at)
    return f" (vence {dt.strftime('%Y-%m-%d %H:%M')})"


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


def _probe_circleci(token: str, vcs: str, org: str) -> tuple[bool, str]:
    """Valida un token de CircleCI (GET /me). Devuelve (ok, error)."""
    from .circleci.client import CircleCiClient, CircleCiError

    ci = CircleCiClient(token, vcs=vcs, org=org)
    try:
        ci.me()
    except (CircleCiError, ValueError) as exc:
        return False, str(exc)
    finally:
        ci.close()
    return True, ""


def _probe_aws(profile: str, region: str) -> tuple[bool, str]:
    """Valida credenciales AWS por STS GetCallerIdentity. Devuelve (ok, detalle)."""
    from .aws.client import validate_credentials

    return validate_credentials(profile, region)


@app.command()
def login(
    alias: str = typer.Option("", "--alias", help="Alias del cliente (default: 'local')"),
    workspace: str = typer.Option("", "--workspace", help="Workspace de Bitbucket"),
    token: str = typer.Option("", "--token", help="App password o PAT de Bitbucket"),
    circleci_token: str = typer.Option(
        "", "--circleci-token", help="Token de CircleCI (opcional)"
    ),
    aws_profile: str = typer.Option(
        "", "--aws-profile", help="Profile AWS a validar por STS (opcional)"
    ),
    aws_region: str = typer.Option(
        "", "--aws-region", help="Región AWS (opcional, default alineado con yappy-cli-manager)"
    ),
    aws_localstack: bool = typer.Option(
        False, "--aws-localstack", help="Usar endpoint de LocalStack/Docker"
    ),
):
    """Guardar credenciales por servicio (data/cache.db), validando cada uno.

    Carrusel: Bitbucket (requerido) → CircleCI (opcional) → AWS (opcional,
    credenciales en ~/.aws o env; se valida por STS GetCallerIdentity).

    Con claves opcionales (--token/--circleci-token/--aws-profile) corre en
    modo no interactivo. Sin opciones, pregunta paso a paso.
    """
    cfg = Config()
    non_interactive = bool(token or circleci_token or aws_profile)

    if alias:
        seed = f"{alias}|{time.time()}|{token}" if token else ""
        cfg = cfg.set_client_alias(alias, seed=seed)

    # -- paso Bitbucket (requerido) -----------------------------------------
    ws = workspace or cfg.workspace
    bb_url = cfg.bitbucket_url
    bb_username = cfg.bitbucket_username
    tok = token
    if not tok and not non_interactive:
        try:
            prompt_alias = cfg.client_alias or "local"
            user_alias = input(f"Alias del cliente [{prompt_alias}]: ").strip()
            if user_alias:
                cfg = cfg.set_client_alias(user_alias)
            ws = input(f"Workspace [{ws or ''}]: ").strip() or ws
            bb_url = input(f"Bitbucket URL [{bb_url}]: ").strip() or bb_url
            bb_username = input(
                f"Bitbucket username (opcional, actual: {bb_username or 'ninguno'}): "
            ).strip() or bb_username
            tok = input("Bitbucket token (app password o PAT): ").strip()
        except (EOFError, KeyboardInterrupt):
            die("Login cancelado.")
    if not ws:
        die("Workspace obligatorio.")
    if not tok:
        die("Token de Bitbucket obligatorio.")

    with console.status("Ahora validando credenciales de Bitbucket..."):
        ok, detail, err, _ = _probe_bitbucket(ws, tok, bb_url)
    if not ok:
        die(f"Token de Bitbucket inválido: {err}")
    info(f"[green]Bitbucket OK[/green] — {detail}")

    # -- paso CircleCI (opcional) --------------------------------------------
    cci = circleci_token
    vcs = cfg.circleci_vcs or "bb"
    org = cfg.circleci_org or ws
    if not cci and not non_interactive:
        try:
            raw = input("CircleCI token (opcional, Enter para omitir): ").strip()
            if raw:
                cci = raw
                vcs = input(f"CircleCI VCS [{vcs}]: ").strip() or vcs
                org = input(f"CircleCI org [{org}]: ").strip() or org
        except (EOFError, KeyboardInterrupt):
            die("Login cancelado.")
    if cci:
        with console.status("Ahora validando credenciales de CircleCI..."):
            ok_c, err_c = _probe_circleci(cci, vcs, org)
        if not ok_c:
            die(f"Token de CircleCI inválido: {err_c}")
        info("[green]CircleCI OK[/green] — sesión válida")

    # -- paso AWS (opcional, STS) --------------------------------------------
    aws_prof = aws_profile
    aws_reg = aws_region
    if not aws_prof and not non_interactive:
        try:
            current_prof = cfg.aws_profile or "ninguno"
            raw = input(f"AWS profile (opcional, actual: {current_prof}; Enter para omitir): ").strip()
            if raw:
                aws_prof = raw
                aws_reg = input(
                    f"AWS region [{aws_reg or DEFAULT_AWS_REGION}]: "
                ).strip() or aws_reg or DEFAULT_AWS_REGION
        except (EOFError, KeyboardInterrupt):
            die("Login cancelado.")
    if aws_prof:
        if not aws_reg:
            aws_reg = DEFAULT_AWS_REGION
        with console.status("Ahora validando credenciales de AWS (STS)..."):
            ok_a, detail_a = _probe_aws(aws_prof, aws_reg)
        if not ok_a:
            die(f"Credenciales AWS inválidas: {detail_a}")
        info(f"[green]AWS OK[/green] — {detail_a}")

    cfg.save_tokens(
        bitbucket_token=tok,
        workspace=ws,
        bitbucket_username=bb_username or "",
        bitbucket_url=bb_url,
        circleci_token=cci,
        circleci_vcs=vcs,
        circleci_org=org or ws,
        aws_profile=aws_prof,
        aws_region=aws_reg,
        aws_localstack="1" if aws_localstack else "",
    )
    alias_name = cfg.client_alias or "local"
    success(f"Conexión guardada para '{alias_name}' ({cfg.client_id[:8]}…)")
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


def _git_pull() -> None:
    """Ejecutar git pull desde el remote (en el directorio del proyecto)."""
    project_dir = Path(__file__).resolve().parent.parent  # bbit_release -> bbit-release-manager
    info("Haciendo git pull desde el remote...")
    result = subprocess.run(
        ["git", "pull"],
        cwd=str(project_dir),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if result.returncode != 0:
        die(f"Falló git pull:\n{result.stdout}\n{result.stderr}")
    success("Git pull completado.")


def _build_web_frontend() -> None:
    import shutil
    frontend = Config().frontend_root
    npm = _have_tool("npm") and __import__("shutil").which("npm")
    if not npm:
        die("npm no está instalado — instalá Node.js (>= 24) para compilar el frontend")
    if not (frontend / "package.json").exists():
        die(f"No se encontró frontend/ en {frontend}")
    
    # BBIT-33: Limpiar dist/ antes de compilar para evitar builds stale
    dist = frontend / "dist"
    if dist.exists():
        info(f"Limpiando {dist}...")
        shutil.rmtree(str(dist))
    
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
    """Levantar la web de BBit (SPA + API).
    
    Siempre ejecuta:
      1. git pull (desde el remote)
      2. Build del frontend (si es necesario)
      3. Levanta la app
    """
    # Paso 1: Git pull
    _git_pull()
    
    # Paso 2: Build del frontend
    if build or (build is None and _frontend_needs_build()):
        _build_web_frontend()
    
    # Paso 3: Levantar la app
    if dev:
        from .web.run import run_dev
        run_dev(port=port, open_browser=not no_browser)
        return
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


# -- AWS ------------------------------------------------------------------------

aws_app = typer.Typer(
    name="aws",
    help="Sesión AWS (SSO) y valores SSM — identidad por cliente de Config()",
    no_args_is_help=True,
)


def _home_aws() -> Path:
    """Directorio `~/.aws` del usuario actual (config + credentials + sso cache)."""
    return Path(os.path.expanduser("~")) / ".aws"


def _aws_session_from(cfg: Config | None = None):
    from .aws.session import AwsSession

    return AwsSession.from_config(cfg)


@aws_app.command("status")
def aws_status():
    """Estado de la sesión AWS del cliente actual (STS, sin secretos)."""
    cfg = Config()
    if not (cfg.aws_profile or cfg.aws_access_key_id):
        warn("Sin credenciales AWS para este cliente (AWS_PROFILE o access key) — ejecutá 'bbit login'.")
        raise typer.Exit(1)
    session = _aws_session_from(cfg)
    from .aws.session import AwsSessionError

    try:
        st = session.status()
    except AwsSessionError as exc:
        die(str(exc))
    info(f"  Profile:     {st['profile']}")
    info(f"  Region:      {st['region']}")
    info(f"  Endpoint:    {cfg.aws_endpoint_url or '(default AWS)'}")
    info(f"  Account:     {st['account']}")
    info(f"  Role/ARN:    {st['arn']}")
    success("Sesión AWS válida.")


@aws_app.command("export-session")
def aws_export_session(
    output: Path = typer.Option(Path("aws-session.tar.gz"), "--output", "-o", help="Archivo a generar"),
    no_refresh: bool = typer.Option(False, "--no-refresh", help="No refrescar el accessToken antes de exportar"),
):
    """Empaqueta `~/.aws` (config + credentials + sso cache) en un tar.gz 0600."""
    import tarfile
    from datetime import datetime

    home = _home_aws()
    if not home.exists():
        die(f"No hay {home} — no hay sesión AWS que exportar.")
    if output.exists():
        output.unlink()

    def _add(root: Path, arc: str) -> None:
        with tarfile.open(str(output), "w:gz") as tar:
            for f in sorted(root.rglob("*")):
                if f.is_file():
                    tar.add(f, arcname=f"{arc}/{f.relative_to(root).as_posix()}")
                    os.chmod(f, 0o600)

    # Si no usamos refresh no tocamos el cache; exportamos tal cual.
    tmp = home
    _add(tmp, ".aws")

    os.chmod(output, 0o600)
    size = output.stat().st_size
    dt = datetime.now().strftime("%Y-%m-%d %H:%M")
    warn("El archivo contiene credenciales (0600). No subir a git ni compartir por canales inseguros.")
    success(f"Sesión exportada a {win_to_posix(str(output))} ({size} bytes, {dt}) — importala en el VPS con 'bbit aws import-session'.")


@aws_app.command("import-session")
def aws_import_session(
    bundle: Path = typer.Option(Path("aws-session.tar.gz"), "--bundle", "-b", help="tar.gz generado con export-session"),
    yes: bool = typer.Option(False, "--yes", "-y", help="No preguntar (instala y valida)"),
):
    """Instala `~/.aws` desde el bundle y valida con STS."""
    import tarfile

    if not bundle.exists():
        die(f"No existe el bundle {win_to_posix(str(bundle))} — ejecutá 'bbit aws export-session' en la máquina local primero.")
    home = _home_aws()
    home.mkdir(parents=True, exist_ok=True)
    first = not any(home.iterdir()) or not (home / "config").exists()
    if not yes and not first:
        keep = input(f"  ~/.aws ya tiene contenido. ¿Reemplazar? (y/N): ").strip().lower()
        if keep != "y":
            info("Importación cancelada.")
            return
    with tarfile.open(str(bundle), "r:gz") as tar:
        for member in tar.getmembers():
            if not member.isfile():
                continue
            dest = home / member.name.removeprefix(".aws/")
            dest.parent.mkdir(parents=True, exist_ok=True)
            with tar.extractfile(member) as src, open(dest, "wb") as out:
                out.write(src.read())
            os.chmod(dest, 0o600)
    success(f"Config instalada en {win_to_posix(str(home))}.")

    cfg = Config()
    profile = cfg.aws_profile
    if not profile:
        warn("No hay AWS_PROFILE en la config de este cliente — 'bbit aws import-session' terminó sin validar (setealo con 'bbit login').")
        return
    from .aws.session import AwsSessionError

    try:
        st = _aws_session_from(cfg).status()
    except AwsSessionError as exc:
        die(str(exc))
    success(f"Sesión importada y validada: {st['arn']} (account {st['account']})")


@aws_app.command("params")
def aws_params(
    paths: list[str] = typer.Argument(None, help="Paths SSM a leer (ej: /config/app/url). Sin args: lee de un diff simulado."),
    decrypt: bool = typer.Option(False, "--decrypt", help="Descifrar SecureString"),
    force: bool = typer.Option(False, "--force", help="Ignorar cache"),
):
    """Valores reales de paths SSM (cache por cliente, batches de 10)."""
    from .aws.ssm import fetch_values

    cfg = Config()
    session = _aws_session_from(cfg)
    if not session.available:
        warn("Sin AWS_PROFILE configurado — ejecutá 'bbit login' o importá la sesión.")
        raise typer.Exit(1)
    if not paths:
        die("Indicá al menos un path SSM (ej: bbit aws params /config/app/url).")
    values = fetch_values(session, paths, decrypt=decrypt, cache=get_cache(), force=force)
    if not values:
        warn("Ningún parámetro resuelto (¿no existen? revisá con --decrypt o el path exacto).")
        return
    from rich.table import Table
    from rich import box

    table = Table(box=box.SIMPLE)
    table.add_column("Path", style="cyan")
    table.add_column("Valor")
    table.add_column("Tipo")
    missing = []
    for p in paths:
        info = values.get(p)
        if info is None:
            missing.append(p)
            continue
        table.add_row(p, info.get("value", ""), "SecureString" if info.get("encrypted") else "String")
    if table.row_count:
        from .logger import console as _console
        _console.print(table)
    if missing:
        warn(f"Missing: {', '.join(missing)}")


app.add_typer(aws_app, name="aws")


if __name__ == "__main__":
    app()