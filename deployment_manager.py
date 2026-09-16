"""
deployment_manager.py (Bot 7) — Deploy a QA-approved site.

Input:
    generated_sites/<lead_id>/ + its qa_report.json

Process:
    1. Copy the site to the GitHub folder (send_to_github).
    2. git init + push to a public GitHub repo (create_github_repo).
    3. Link the folder to its OWN Vercel project (vercel link --project
       <lead_id>) so sibling sites never share a project, then deploy.
       Production deploys (prod=True, default) get a public live URL
       https://<lead_id>.vercel.app; preview deploys may be login-gated.
    4. Append the deployment record (github + live URLs) to
       deployments/deployments.json.

Output (deployment record JSON):
    {"site": "lead_00421", "status": "deployed",
     "github_url": "https://github.com/<owner>/lead_00421",
     "preview_url": "https://lead000421.vercel.app",
     "provider": "vercel", "deployed_at": "..."}

Usage:
    python -c "from deployment_manager import main; main('generated_sites/lead_00001')"
    python tests.py
"""

import datetime
import json
import os
from pathlib import Path
import shutil
import subprocess

def _resolve_cli(cmd: str) -> str:
    """Find a CLI on PATH plus known Windows install locations."""
    found = shutil.which(cmd)
    if found:
        return found
    candidates: list[str] = []
    if cmd == "git":
        candidates = [r"C:\Program Files\Git\cmd\git.exe",
                      r"C:\Program Files\Git\bin\git.exe"]
    elif cmd == "gh":
        candidates = [r"C:\Program Files\GitHub CLI\gh.exe",
                      r"C:\Program Files (x86)\GitHub CLI\gh.exe"]
    elif cmd == "vercel":
        candidates = [str(Path.home() / "AppData" / "Roaming" / "npm" / "vercel.cmd"),
                      str(Path.home() / "AppData" / "Roaming" / "npm" / "vercel")]
    for cand in candidates:
        if Path(cand).exists():
            return cand
    return cmd  # let subprocess raise FileNotFoundError -> clear message below

def append_deployment_record(deployment_record: dict, record_file: str = "deployments/deployments.json"):
    """Append a deployment record to the deployments JSON file."""
    record_path = Path(record_file)
    record_path.parent.mkdir(parents=True, exist_ok=True)
    records: list = []
    if record_path.exists():
        try:
            raw = json.loads(record_path.read_text(encoding="utf-8"))
            records = raw if isinstance(raw, list) else [raw]
        except (json.JSONDecodeError, OSError):
            records = []
    records.append(deployment_record)
    record_path.write_text(json.dumps(records, ensure_ascii=False, indent=2) + "\n",
                           encoding="utf-8")
    # Per-lead record(s) for Bot 8 (current_lead_generator defaults to
    # deployments/<lead_id>.json). The folder/repo name is now a
    # customer-facing slug, so index under both slug and lead_id.
    seen: set[str] = set()
    for key in (deployment_record.get("site"), deployment_record.get("lead_id")):
        if key and key not in seen:
            seen.add(key)
            (record_path.parent / f"{key}.json").write_text(
                json.dumps(deployment_record, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8")
        
def _github_base() -> Path:
    """Portable base dir for GitHub push folders.

    Old code hardcoded one machine's OneDrive path. Prefer explicit env
    ``AGENCY_GITHUB_DIR``, else the repo root (parent of this file), so a
    fresh clone works anywhere.
    """
    env = os.environ.get("AGENCY_GITHUB_DIR", "").strip()
    if env:
        return Path(env)
    return Path(__file__).resolve().parent


def send_to_github(path_to_file: str, dest_base: str | Path | None = None):
    """Send the website to GitHub (folder).

    Skips local/deploy-only files — project links, secrets, and logs must
    never land in the public repo.
    """
    source = Path(path_to_file)
    name = source.name
    base = Path(dest_base) if dest_base else _github_base()
    destination = base / name
    shutil.copytree(source, destination, dirs_exist_ok=True,
                    ignore=shutil.ignore_patterns(
                        ".vercel", ".git", "*.log",
                        ".env", ".env.*", "qa_report.json"))
    return destination

def create_github_repo(path_to_file: str) -> str:
    """Init a git repo in folder and push it to GitHub via `gh`.

    Idempotent: safe to re-run. Skips commit when clean, and pushes
    when the GitHub repo already exists. Returns the repo URL (or name
    when the URL can't be parsed). Raises RuntimeError with a clear
    message when git/gh is missing or git identity is unconfigured.
    """
    folder = Path(path_to_file)
    if not folder.exists():
        raise RuntimeError(f"create_github_repo: path not found: {folder}")
    if not folder.is_dir():
        raise RuntimeError(f"create_github_repo: not a folder: {folder}")
    # Agency guard: never publish client sites from the wrong account.
    expected = os.environ.get("AGENCY_GH_USER", "").strip()
    if expected:
        try:
            out_login = subprocess.run(
                [_resolve_cli("gh"), "api", "user", "-q", ".login"],
                capture_output=True, text=True, timeout=30)
            active = (out_login.stdout or "").strip()
        except (OSError, subprocess.TimeoutExpired):
            active = ""
        if active.lower() != expected.lower():
            raise RuntimeError(
                f"wrong GitHub account active ({active or 'unknown'}); "
                f"agency deploys require '{expected}'. "
                f"Run: gh auth switch --user {expected}")
    # Refuse empty folders early — git cannot create a commit from nothing.
    if not any(p for p in folder.iterdir() if p.name != ".git"):
        raise RuntimeError(f"create_github_repo: folder is empty: {folder} "
                           f"(add at least one file first)")
    repo_name = folder.name

    def _run(args: list[str]) -> subprocess.CompletedProcess:
        exe = _resolve_cli(args[0])
        try:
            return subprocess.run(
                [exe, *args[1:]], cwd=str(folder), check=True,
                capture_output=True, text=True)
        except FileNotFoundError:
            raise RuntimeError(
                f"'{args[0]}' CLI not found. "
                f"Install it to use create_github_repo (git / gh).")

    # 1. git init (only when needed; pin default branch to main for new repos).
    if not (folder / ".git").exists():
        try:
            _run(["git", "init", "-b", "main"])
        except subprocess.CalledProcessError:
            _run(["git", "init"])  # older git without -b
    # Agency commit identity (local to the deploy repo only — never touches
    # the user's global git config). Set AGENCY_GIT_NAME/AGENCY_GIT_EMAIL
    # to author client-repo commits as the agency.
    for _key, _val in (("user.name", os.environ.get("AGENCY_GIT_NAME", "")),
                       ("user.email", os.environ.get("AGENCY_GIT_EMAIL", ""))):
        if _val.strip():
            try:
                _run(["git", "config", _key, _val.strip()])
            except subprocess.CalledProcessError as e:
                raise RuntimeError(f"git config {_key} failed: "
                                   f"{((e.stdout or '') + (e.stderr or ''))[-300:]}")
    _run(["git", "add", "."])

    # 2. Commit only when there is something to commit.
    status = _run(["git", "status", "--porcelain"]).stdout.strip()
    try:
        _run(["git", "rev-parse", "--verify", "HEAD"])
        head_exists = True
    except subprocess.CalledProcessError:
        head_exists = False
    if status or not head_exists:
        try:
            _run(["git", "commit", "-m", "Initial commit"])
        except subprocess.CalledProcessError as e:
            out = (e.stdout or "") + (e.stderr or "")
            if "nothing to commit" in out.lower():
                pass  # clean tree — nothing to do
            elif "user.name" in out or "user.email" in out or "identity" in out.lower():
                raise RuntimeError(
                    "git identity missing. Run once: "
                    'git config --global user.name \"You\"; '
                    'git config --global user.email \"you@example.com\"')
            else:
                raise RuntimeError(f"git commit failed: {out[-800:]}")
    try:
        _run(["git", "branch", "-M", "main"])
    except subprocess.CalledProcessError:
        pass

    # 3. Create on GitHub (non-interactive) or push if it already exists.
    try:
        proc = _run(["gh", "repo", "create", repo_name,
                     "--public", "--source=.", "--push"])
        out = (proc.stdout or "") + (proc.stderr or "")
    except subprocess.CalledProcessError as e:
        out = (e.stdout or "") + (e.stderr or "")
        if "already exists" in out.lower():
            _ensure_origin(_run, repo_name)
            try:
                _run(["git", "push", "-u", "origin", "HEAD"])
            except subprocess.CalledProcessError as e2:
                err = (e2.stdout or "") + (e2.stderr or "")
                if "fetch first" in err or "non-fast-forward" in err \
                        or "[rejected]" in err:
                    # Deploy repos mirror generated output — local wins.
                    try:
                        _run(["git", "push", "-f", "-u", "origin", "HEAD:main"])
                    except subprocess.CalledProcessError as e3:
                        err3 = (e3.stdout or "") + (e3.stderr or "")
                        raise RuntimeError(f"git push failed: {err3[-800:]}")
                else:
                    raise RuntimeError(f"git push failed: {err[-800:]}")
        else:
            raise RuntimeError(f"gh repo create failed: {out[-800:]}")

    import re
    m = re.search(r"https://github\.com/\S+", out)
    if m:
        url = m.group(0).rstrip(".,)")
    else:
        # Deterministic fallback: read the remote we just pushed to.
        url = repo_name
        try:
            remote = _run(["git", "remote", "get-url", "origin"]).stdout.strip()
            url = _normalize_github_url(remote) or url
        except subprocess.CalledProcessError:
            pass
    print(f"GitHub repository created: {url}")
    return url


def _ensure_origin(_run, repo_name: str) -> None:
    """Reattach the origin remote when a stale local folder lost it.

    Happens when the GitHub repo already exists (prior deploy) but the
    local folder's .git has no origin — e.g. the folder was recopied
    without its remote. Owner comes from `gh api user`.
    """
    try:
        _run(["git", "remote", "get-url", "origin"])
        return
    except subprocess.CalledProcessError:
        pass
    owner = ""
    try:
        owner = _run(["gh", "api", "user", "-q", ".login"]).stdout.strip()
    except subprocess.CalledProcessError:
        pass
    if not owner:
        raise RuntimeError(
            "repo already exists on GitHub but local has no origin remote "
            "and the owner could not be determined via `gh api user`")
    _run(["git", "remote", "add", "origin",
          f"https://github.com/{owner}/{repo_name}.git"])


def _normalize_github_url(remote: str) -> str | None:
    """git@github.com:o/r(.git) or https://github.com/o/r(.git) -> https URL."""
    import re
    remote = (remote or "").strip()
    m = re.match(r"(?:git@github\.com:|https://github\.com/)([^/\s]+/[^/\s]+?)(?:\.git)?/?$", remote)
    return f"https://github.com/{m.group(1)}" if m else None


def deploy_to_vercel(path_to_file: str, prod: bool = True) -> str:
    """Deploy a site folder to its own Vercel project.

    Links the folder to a project named after the folder (vercel link
    --project) so sibling sites can never share a project, then deploys.
    prod=True (default) promotes to production for a public live URL
    https://<lead_id>.vercel.app; prod=False leaves a preview deployment
    (may be login-gated by Vercel protection).
    Requires `vercel` installed and logged in (`vercel login`).
    Returns the live URL. Raises RuntimeError on failure.
    """
    import re
    folder = Path(path_to_file)
    if not folder.exists():
        raise RuntimeError(f"deploy_to_vercel: path not found: {folder}")
    if not folder.is_dir():
        raise RuntimeError(f"deploy_to_vercel: not a folder: {folder}")
    if not (folder / "index.html").exists():
        raise RuntimeError(f"deploy_to_vercel: no index.html in {folder}")
    vercel = _resolve_cli("vercel")
    if vercel.lower().endswith((".cmd", ".bat")):
        # Batch shims can't exec directly — run through cmd.exe.
        vercel_cmd: list[str] = [os.environ.get("COMSPEC", "cmd.exe"), "/c", vercel]
    else:
        vercel_cmd = [vercel]

    def _vrun(args: list[str], step: str) -> str:
        try:
            proc = subprocess.run(
                [*vercel_cmd, *args], cwd=str(folder), check=True,
                capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=300)
        except FileNotFoundError:
            raise RuntimeError("'vercel' CLI not found. Install it: npm i -g vercel")
        except subprocess.TimeoutExpired:
            raise RuntimeError(f"vercel {step} timed out after 300s")
        except subprocess.CalledProcessError as e:
            out = (e.stdout or "") + "\n" + (e.stderr or "")
            raise RuntimeError(f"vercel {step} failed: {out[-800:]}")
        return (proc.stdout or "") + "\n" + (proc.stderr or "")

    # Pin this folder to its own project (immune to stray parent .vercel links).
    _vrun(["link", "--yes", "--project", folder.name], "link")
    args = ["deploy", "--yes"]
    if prod:
        args.append("--prod")
    out = _vrun(args, "deploy")
    urls = re.findall(r"https://[A-Za-z0-9\-.]+\.vercel\.app[^\s\"']*", out)
    if not urls:
        raise RuntimeError(f"vercel deploy gave no URL: {out[-800:]}")
    # Prefer the production alias (<project>.vercel.app) over hashed preview URLs.
    bare = "https://" + folder.name.lower().replace("_", "") + ".vercel.app"
    url = next((u for u in urls if u.lower() == bare), urls[-1])
    print(f"Vercel deployment live: {url}")
    return url


def main(argv=None, path_to_file: str | None = None, prod: bool = True,
         dest_base: str | Path | None = None) -> int:
    """Deploy a QA-approved site: GitHub push, then Vercel (production by default).

    Orchestrator use: ``main(path_to_file="generated_sites/lead_00001")``.
    CLI use: ``python deployment_manager.py generated_sites/lead_00001 [--no-prod]``.
    Returns 0 on success, non-zero exit code on error.
    """
    import argparse as _ap
    if isinstance(argv, (str, Path)):
        argv, path_to_file = None, str(argv)
    if argv is not None:
        _p = _ap.ArgumentParser(description="Bot 7: deploy a QA-approved site")
        _p.add_argument("path", nargs="?", default=path_to_file)
        _p.add_argument("--prod", dest="prod", action="store_true", default=True)
        _p.add_argument("--no-prod", dest="prod", action="store_false")
        _a = _p.parse_args(argv)
        path_to_file, prod = _a.path, _a.prod
    if not path_to_file:
        print("[deployment_manager] ERROR: pass the site folder, e.g. "
              'main("generated_sites/lead_00001")', flush=True)
        return 2
    try:
        github_path = send_to_github(path_to_file, dest_base=dest_base)
        github_url = create_github_repo(str(github_path))
        preview_url = deploy_to_vercel(path_to_file, prod=prod)
        lead_id = Path(path_to_file).name
        try:
            _meta = json.loads((Path(path_to_file) / "meta.json").read_text(encoding="utf-8"))
            if isinstance(_meta, dict) and _meta.get("lead_id"):
                lead_id = str(_meta["lead_id"])
        except (OSError, json.JSONDecodeError):
            pass
        append_deployment_record({
            "site": Path(path_to_file).name,
            "lead_id": lead_id,
            "status": "deployed",
            "github_url": github_url,
            "preview_url": preview_url,
            "provider": "vercel",
            "deployed_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        })
    except RuntimeError as e:
        print(f"[deployment_manager] ERROR: {e}", flush=True)
        return 1
    print(f"[deployment_manager] Deployment completed for {path_to_file}.", flush=True)
    print(f"[deployment_manager] preview_url: {preview_url}", flush=True)
    return 0


if __name__ == "__main__":
    import sys as _sys
    raise SystemExit(main(_sys.argv[1:]))