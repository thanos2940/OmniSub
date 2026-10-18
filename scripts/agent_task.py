"""
OmbiSub Agent Task CLI
======================
For AI agents (Claude Code, Antigravity, ...) that translate in place of the model.
Omnisub does all preparation and post-processing; the agent only edits text files.

Run from the repository root:
  .venv/Scripts/python.exe scripts/agent_task.py next            claim the next episode, print its workspace
  .venv/Scripts/python.exe scripts/agent_task.py check [TASK]    validate the work file (changes nothing)
  .venv/Scripts/python.exe scripts/agent_task.py submit [TASK]   validate, ingest, export
  .venv/Scripts/python.exe scripts/agent_task.py list | active   pending queue / episodes being translated
  .venv/Scripts/python.exe scripts/agent_task.py target -p "Show" [-e S01E02] [--full]
  .venv/Scripts/python.exe scripts/agent_task.py enqueue -p "Show" [-e S01E01,S01E02] [--all] [--full]
  .venv/Scripts/python.exe scripts/agent_task.py audit -p "Show" [--requeue] [--repair] [--no-misplaced]
  .venv/Scripts/python.exe scripts/agent_task.py audit --all [--repair] [--requeue] [--no-misplaced]
  .venv/Scripts/python.exe scripts/agent_task.py release TASK | cancel TASK | clear
  .venv/Scripts/python.exe scripts/agent_task.py run [--engine antigravity] [--lanes 3] [--episodes N] [--follow]
  .venv/Scripts/python.exe scripts/agent_task.py run --status | --stop [--now]

TASK may be omitted for check/submit when exactly one episode is claimed.
When the Omnisub server is running every command goes through its API (so the UI
updates live); otherwise the same logic runs in-process.
"""

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

try:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

ROOT_DIR = Path(__file__).resolve().parent.parent
BACKEND_DIR = ROOT_DIR / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))
os.environ.setdefault("OMNISUB_DATA_DIR", str(BACKEND_DIR))
try:  # in-process work gets the server's environment (the Gemini API key in the repo-root .env)
    from dotenv import load_dotenv
    load_dotenv(ROOT_DIR / ".env")
except Exception:
    pass
PY = ".venv/Scripts/python.exe" if sys.platform == "win32" else ".venv/bin/python"
CMD = f"{PY} scripts/agent_task.py"
DEFAULT_URL = "http://127.0.0.1:8000"


def normalize_url(url: str) -> str:
    """Normalize localhost or [::1] to 127.0.0.1 to avoid Windows IPv6 2-second timeout."""
    if not url:
        return DEFAULT_URL
    u = url.rstrip("/")
    if "://localhost" in u:
        u = u.replace("://localhost:", "://127.0.0.1:").replace("://localhost/", "://127.0.0.1/")
        if u.endswith("://localhost"):
            u = u[:-len("localhost")] + "127.0.0.1"
    if "://[::1]" in u:
        u = u.replace("://[::1]:", "://127.0.0.1:").replace("://[::1]/", "://127.0.0.1/")
        if u.endswith("://[::1]"):
            u = u[:-len("[::1]")] + "127.0.0.1"
    return u


class ServerUnreachable(Exception):
    pass


class Client:
    """Calls the running server when there is one, else the service in-process."""

    def __init__(self):
        self.base = normalize_url(os.environ.get("OMNISUB_API_URL") or DEFAULT_URL)
        self.headers = {"Content-Type": "application/json"}
        key = os.environ.get("OMNISUB_API_KEY") or self._config_api_key()
        if key:
            self.headers["X-Api-Key"] = key
        self.online = self._probe()
        self._svc = None

    @staticmethod
    def _config_api_key():
        try:
            cfg = json.loads((Path(os.environ["OMNISUB_DATA_DIR"]) / "config.json").read_text(encoding="utf-8"))
            return cfg.get("api_key")
        except Exception:
            return None

    def _probe(self) -> bool:
        if os.environ.get("OMNISUB_AGENT_OFFLINE"):
            return False
        try:
            with urllib.request.urlopen(f"{self.base}/api/health", timeout=3) as r:
                return r.status == 200
        except Exception:
            return False

    @property
    def svc(self):
        if self._svc is None:
            from services.agent_task_service import agent_task_service
            self._svc = agent_task_service
        return self._svc

    def call(self, method: str, path: str, payload=None, local=None, timeout: float = 900):
        """API call when online (HTTP errors return their JSON body), else ``local()``."""
        if not self.online:
            return local()
        data = json.dumps(payload).encode("utf-8") if payload is not None else (b"" if method == "POST" else None)
        req = urllib.request.Request(f"{self.base}{path}", data=data, headers=self.headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode("utf-8") or "{}")
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="replace")
            try:
                parsed = json.loads(body)
            except Exception:
                parsed = {"message": body}
            if isinstance(parsed, dict) and "detail" in parsed and "success" not in parsed:
                parsed = {"success": False, "message": parsed["detail"] if isinstance(parsed["detail"], str) else json.dumps(parsed["detail"])}
            parsed.setdefault("success", False)
            parsed.setdefault("http_status", e.code)
            return parsed
        except urllib.error.URLError as e:
            # The server answered the probe, so this is not "offline": don't redo the work locally.
            return {"success": False, "status": "server_error", "message": f"Omnisub server error: {e.reason}"}


def out(res, args, render):
    if args.json:
        print(json.dumps(res, indent=2, ensure_ascii=False))
    else:
        render(res)
    if isinstance(res, dict) and res.get("success") is False and res.get("status") not in ("empty",):
        sys.exit(1)


def resolve_task(client: Client, task_id):
    if task_id:
        return task_id
    active = client.call("GET", "/api/agent/tasks/active", local=lambda: {"tasks": client.svc.list_active_tasks()})
    tasks = active.get("tasks", [])
    if len(tasks) == 1:
        return tasks[0]["task_id"]
    if not tasks:
        sys.exit(f"[-] No claimed task. Run: {CMD} next")
    listing = "\n".join(f"    {t['task_id']}  ({t['project_name']} {t['episode_name']})" for t in tasks)
    sys.exit(f"[-] Several tasks are claimed; name one:\n{listing}")


# --------------------------------------------------------------------------- renderers

def show_claim(res):
    if not res.get("success"):
        print(f"[-] {res.get('message', 'No task available.')}")
        return
    for f in res.get("finalized_without_work") or []:
        print(f"[=] {f.get('project_name')} {f.get('episode_name')}: {f.get('message')}")
    status = res.get("status")
    if status == "nothing_to_translate":
        print(f"[=] {res.get('message')}")
        return
    ws = Path(res["workspace_dir"])
    head = "Already claimed" if status == "already_claimed" else "Claimed"
    print(f"\n[+] {head}: {res['project_name']} {res['episode_name']} -> {res.get('target_language')}   ({res['task_id']})")
    if res.get("units") is not None and status != "already_claimed":
        scope = "retranslate everything" if res.get("mode") == "full" else "only lines missing a translation"
        print(f"    {res['units']} lines: {res.get('dialogue_units', 0)} dialogue, {res.get('sign_units', 0)} on-screen text ({scope})")
    print(f"    Workspace: {ws}")
    print(f"    1. Read   {ws / 'BRIEF.md'}")
    print(f"    2. Edit   {ws / 'translation.txt'}   (replace the English after each \"N|\")")
    print(f"    3. Write  new_terms.json (new recurring names/terms) and episode_summary.txt (2-3 sentences)")
    print(f"    4. Run    {CMD} submit {res['task_id']}\n")


def show_check(res):
    if not res.get("success"):
        print(f"[-] {res.get('message')}")
        return
    r = res["report"]
    mark = "[+]" if r["ok"] else "[!]"
    print(f"{mark} {res['project_name']} {res['episode_name']}: {r['translated']}/{r['total']} lines translated")
    if res.get("message"):
        print(res["message"])
    for w in r.get("glossary_warnings", [])[:10]:
        print(f"    glossary: line {w['id']} — '{w['term']}' should be '{w['expected']}' (declined as needed)")
    terms = res.get("new_terms_ready")
    print(f"    new_terms.json: {'invalid JSON' if terms == -1 else f'{terms} term(s)'} | "
          f"episode_summary.txt: {'written' if res.get('summary_ready') else 'empty'}")


def show_submit(res):
    if not res.get("success"):
        print(f"[-] Not submitted ({res.get('status')}):\n{res.get('message')}")
        return
    if res.get("status") == "already_completed":
        print(f"[=] {res.get('message')}")
        return
    print(f"\n[+] Ingested {res['project_name']} {res['episode_name']}: {res['units_translated']}/{res['units']} lines "
          f"-> {res['cues_translated']} cues")
    print(f"    Exported:  {res.get('exported_path') or 'not exported (media folder unreachable; queued for retry if known)'}")
    print(f"    Glossary:  +{res.get('harvested_terms_count', 0)} new, {res.get('glossary_updated_terms', 0)} updated"
          f" | Summary: {'saved' if res.get('episode_summary_saved') else 'none'}"
          f" | Context guide: {'updated' if res.get('context_guide_updated') else 'unchanged'}"
          f" | TM: {res.get('tm_pairs_saved', 0)} pairs")
    for c in res.get("glossary_conflicts") or []:
        print(f"    kept glossary '{c['term']}' = '{c['kept']}' (you proposed '{c['proposed']}'; add \"update\": true to replace)")
    for w in (res.get("glossary_warnings") or [])[:8]:
        print(f"    note: line {w['id']} does not use glossary '{w['term']}' -> '{w['expected']}'")
    print(f"    Next: {CMD} next\n")


def show_list(res):
    tasks = res.get("tasks", [])
    if not tasks:
        print("No pending translation items in the queue.")
        return
    prio = {0: "Webhook", 1: "Manual", 2: "Sync", 3: "Backlog"}
    print(f"\n--- Pending ({len(tasks)}) ---")
    for t in tasks:
        print(f"{prio.get(t.get('priority'), t.get('priority')):<8} {t['project_name'][:30]:<31} {t['episode_name']:<12} "
              f"{t.get('engine', 'model'):<6} {t.get('mode', 'gaps'):<5} {t['id']}")
    print(f"\nClaim the next one: {CMD} next\n")


def show_active(res):
    tasks = res.get("tasks", [])
    if not tasks:
        print("No episodes are claimed by agents.")
        return
    for t in tasks:
        prog = f"{t.get('units_done', '?')}/{t.get('units', '?')} lines" if t.get("units") else "no workspace"
        print(f"{t['task_id']}  {t['project_name']} {t['episode_name']}  {prog}  claimed {t.get('claimed_at')}")
        if t.get("workspace_dir"):
            print(f"    {t['workspace_dir']}")


def show_simple(res):
    print(("[+] " if res.get("success", True) else "[-] ") + str(res.get("message") or json.dumps(res, ensure_ascii=False)))


def show_audit(res):
    if not res.get("success"):
        print(f"[-] {res.get('message')}")
        return
    print(f"\n{res['project_name']}: {res['incomplete_count']} episode(s) with untranslated lines")
    for e in res["episodes"]:
        if e["episode_name"] in res["incomplete"]:
            print(f"  {e['episode_name']:<10} {e['dialogue']:>4} dialogue, {e['signs']:>3} on-screen  "
                  f"(of {e['total_lines']}; {e['translated_lines']} done)")
    by_name = {e["episode_name"]: e for e in res["episodes"]}
    if res.get("misplaced"):
        print(f"\n{len(res['misplaced'])} episode(s) with lines that carry another line's translation:")
        for ep in res["misplaced"]:
            e = by_name.get(ep, {})
            sample = ", ".join(f"{m['line']}->{m['fits_line']}" for m in (e.get("misplaced_lines") or [])[:6])
            print(f"  {ep:<10} {e.get('misplaced', 0):>4} line(s)   (line -> the line it fits: {sample})")
    if res.get("misplaced_imported"):
        print(f"\n{len(res['misplaced_imported'])} imported subtitle(s) that do not line up with the source (left as they are):")
        for ep in res["misplaced_imported"]:
            print(f"  {ep:<10} {by_name.get(ep, {}).get('misplaced', 0):>4} line(s)")
    if res.get("broken"):
        print(f"\n{len(res['broken'])} episode(s) with broken text:")
        for ep in res["broken"]:
            e = by_name.get(ep, {})
            n = sum((e.get("broken") or {}).values())
            sample = ", ".join(str(m["line"]) for m in (e.get("broken_lines") or [])[:6])
            print(f"  {ep:<10} {n:>4} line(s)   ({kind_counts(e.get('broken'))}; lines {sample}{'...' if n > 6 else ''})")
    if res.get("repaired_lines") or res.get("broken_cleared"):
        print(f"[+] Repaired {res.get('repaired_lines', 0)} broken line(s), cleared {res.get('broken_cleared', 0)} "
              f"for retranslation (the old text is in text_repair_backup.json next to each episode).")
    for d in res.get("disk_repaired") or []:
        print(f"[+] {d['episode']}: {d['lines']} line(s) fixed in {d['path']} (original kept in disk_backup/ next to the episode)")
    if res.get("reexported"):
        print(f"[+] Exported again: {', '.join(res['reexported'])}")
    for d in res.get("disk_failed") or []:
        print(f"[-] {d['episode']}: could not repair {d['path']}: {d['error']}")
    if res.get("reexport_failed"):
        print(f"[-] Not exported (media folder unreachable): {', '.join(res['reexport_failed'])}")
    if res.get("cleared_lines"):
        print(f"[+] Cleared {res['cleared_lines']} misplaced line(s) for retranslation "
              f"(the old text is in misplaced_backup.json next to each episode).")
    if res.get("enqueue"):
        print(f"[+] {res['enqueue'].get('message')}")
    elif res["incomplete"] or res.get("misplaced"):
        print(f"\nQueue them for gap-filling: {CMD} audit -p \"{res['project_name']}\" --requeue")
    if res.get("broken") and "repaired_lines" not in res:
        print(f"\nFix the broken text: {CMD} audit -p \"{res['project_name']}\" --repair"
              f"   (wrong-alphabet lines are only cleared with --requeue)")


def kind_counts(counts) -> str:
    """{"mojibake": 560, "mixed_script": 2} -> "mojibake 560, mixed_script 2"."""
    return ", ".join(f"{k} {n}" for k, n in (counts or {}).items())


def show_library_audit(res):
    if not res.get("success", True):
        print(f"[-] {res.get('message') or res.get('error') or 'Library audit failed.'}")
        return
    t = res.get("totals") or {}
    print(f"\nLibrary audit: {t.get('projects', 0)} project(s), {t.get('episodes_checked', 0)} episode(s) checked")
    for p in res.get("projects") or []:
        if p.get("error"):
            print(f"  [-] {p['project_name']}: {p['error']}")
            continue
        kinds: dict = {}
        for e in p.get("episodes") or []:
            for k, n in (e.get("broken") or {}).items():
                kinds[k] = kinds.get(k, 0) + n
        bits = [f"{len(p[key])} {label}" for key, label in (("incomplete", "incomplete"), ("misplaced", "misplaced"),
                                                            ("misplaced_imported", "imported misaligned"))
                if p.get(key)]
        if p.get("broken"):
            bits.append(f"{len(p['broken'])} broken ({kind_counts(kinds)})")
        print(f"  {p['project_name'][:42]:<43} {', '.join(bits)}")
        for d in p.get("disk_failed") or []:
            print(f"      [-] {d['episode']}: could not repair {d['path']}: {d['error']}")
        if p.get("reexport_failed"):
            print(f"      [-] not exported (media folder unreachable): {', '.join(p['reexport_failed'])}")
    if not res.get("projects"):
        print("  Nothing to report.")
    print(f"\n  Episodes with untranslated lines: {t.get('incomplete', 0)} | misplaced lines: {t.get('misplaced', 0)} "
          f"(+{t.get('misplaced_imported', 0)} imported) | broken text: {t.get('broken', 0)} "
          f"({t.get('broken_lines', 0)} line(s))")
    if t.get("repaired_lines") or t.get("cleared_lines"):
        print(f"[+] Repaired {t.get('repaired_lines', 0)} line(s), cleared {t.get('cleared_lines', 0)} for retranslation "
              f"(old text: text_repair_backup.json / misplaced_backup.json in each episode folder).")
    files = sum(len(p.get("disk_repaired") or []) for p in res.get("projects") or [])
    exported = sum(len(p.get("reexported") or []) for p in res.get("projects") or [])
    if files or exported:
        print(f"[+] {files} subtitle file(s) repaired in place (originals in disk_backup/), {exported} episode(s) exported again.")
    if t.get("enqueued"):
        print(f"[+] Queued {t['enqueued']} episode(s) for agent gap-filling: {CMD} next")
    elif t.get("broken") and not t.get("repaired_lines") and not t.get("cleared_lines"):
        print(f"\nFix the broken text: {CMD} audit --all --repair   (wrong-alphabet lines are only cleared with --requeue)")


def audit_library(c: "Client", args):
    """Audit every project: a background job on the server (started, then polled), else in this process."""
    options = {"repair": args.repair, "requeue": args.requeue, "misplaced": not args.no_misplaced}
    quiet = args.json
    if not c.online:
        def progress(done, total, name):
            if not quiet:
                print(f"[{done}/{total}] {name}", flush=True)
        out(c.svc.audit_library(requeue=args.requeue, repair=args.repair, misplaced=not args.no_misplaced,
                                progress=progress), args, show_library_audit)
        return
    state = c.call("POST", "/api/agent/library-audit", options)
    if state.get("http_status") == 409:
        if not quiet:
            print("[=] A library audit is already running; following it.")
    elif state.get("success") is False or "status" not in state:
        sys.exit(f"[-] {state.get('message') or state.get('detail') or 'Could not start the library audit.'}")
    seen, failures = 0, 0
    try:
        while state.get("status") not in ("done", "error"):
            if state.get("status") == "running":
                failures = 0
                prog = state.get("progress") or {}
                if prog.get("done") and prog["done"] != seen and not quiet:
                    seen = prog["done"]
                    print(f"[{seen}/{prog.get('total', '?')}] {prog.get('project')}", flush=True)
            else:  # no proper answer: the server is restarting, or lost the run
                failures += 1
                if failures >= 3:
                    sys.exit(f"[-] {state.get('message') or 'The server does not report the audit (status: ' + str(state.get('status')) + ')'}"
                             f" - a finished audit's result is kept in library_audit.json in the data folder.")
            time.sleep(2)
            state = c.call("GET", "/api/agent/library-audit", timeout=60)
    except KeyboardInterrupt:
        sys.exit("[=] Stopped following; the audit keeps running on the server (GET /api/agent/library-audit).")
    if state.get("status") == "error":
        sys.exit(f"[-] Library audit failed: {state.get('error')}")
    out(state.get("result") or {}, args, show_library_audit)


def show_runner(res):
    if res.get("success") is False:
        print(f"[-] {res.get('message') or res.get('detail')}")
        return
    cfg = res.get("config") or {}
    models = ", ".join(m or "default model" for m in res.get("models") or [])
    print(f"\nRunner: {res.get('status')}" + (f" — {res['message']}" if res.get("message") else ""))
    print(f"    {cfg.get('engine')} ({models}), {cfg.get('lanes')} lane(s), up to {cfg.get('per_show')} episode(s) "
          f"of one show at a time{', keeps watching the queue' if cfg.get('watch') else ''}")
    for lane in res.get("lanes") or []:
        if lane.get("task"):
            progress = f" ({lane['progress']})" if lane.get("progress") else ""
            print(f"    lane {lane['lane']}: {lane.get('project')} {lane.get('episode')} — {lane.get('state')}{progress}")
    for model, until in (res.get("pauses") or {}).items():
        print(f"    paused: {model} until {until}")
    s = res.get("session") or {}
    print(f"    this session: {s.get('episodes', 0)} episode(s), {s.get('lines', 0)} lines, {s.get('calls', 0)} requests")
    for e in (res.get("log") or [])[-12:]:
        print(f"    {e['t']}  {e['text']}")
    print()


def follow_runner(get, since: int = 0):
    """Print the runner's log as it grows, until it stops (Ctrl+C stops following)."""
    try:
        while True:
            res = get(since)
            for e in res.get("log") or []:
                if e["seq"] > since:
                    print(f"{e['t']}  {e['text']}", flush=True)
            since = max(since, res.get("log_seq") or since)
            if not res.get("running"):
                print(f"[=] Runner {res.get('status')}" + (f": {res['message']}" if res.get("message") else ""))
                return res
            time.sleep(2)
    except KeyboardInterrupt:
        return None


def run_command(c: "Client", args):
    """The direct runner: translate the queue unattended, one prompt per episode (or part)."""
    if args.status or args.stop:
        if c.online:
            res = (c.call("POST", f"/api/agent/runner/stop?now={'true' if args.now else 'false'}") if args.stop
                   else c.call("GET", "/api/agent/runner"))
        else:
            from services.agent_runner import get_runner
            res = get_runner().stop(now=args.now) if args.stop else get_runner().info()
        out(res, args, show_runner)
        return
    config = {k: v for k, v in {
        "engine": args.engine, "model": args.model, "fallback_models": args.fallback, "lanes": args.lanes,
        "per_show": args.per_show, "max_episodes": args.episodes, "watch": True if args.watch else None,
    }.items() if v is not None}
    if c.online:
        res = c.call("POST", "/api/agent/runner/start", config)
        if res.get("success") is False or not args.follow:
            out(res, args, show_runner)
            return
        show_runner(res)
        follow_runner(lambda since: c.call("GET", f"/api/agent/runner?since={since}"), res.get("log_seq") or 0)
        return
    # No server: run in this process until the queue is done; Ctrl+C stops (half-done episodes resume later).
    from services.agent_runner import get_runner
    r = get_runner()
    try:
        res = r.start(config)
    except (ValueError, FileNotFoundError, RuntimeError) as e:
        sys.exit(f"[-] {e}")
    show_runner(res)
    print("[+] The Omnisub server is not running, so the runner works in this window. Ctrl+C stops it.")
    if follow_runner(lambda since: r.info(since), res.get("log_seq") or 0) is None:
        print("[=] Stopping (requests in flight are cut off; translated lines stay in the workspaces)...")
        r.stop(now=True)
        for t in list(r.threads):
            t.join(timeout=60)


# --------------------------------------------------------------------------- commands

def main():
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--json", action="store_true", help="machine-readable output")
    parser = argparse.ArgumentParser(description="OmbiSub agent translation CLI", parents=[common])
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("list", parents=[common], help="pending translation items")
    sub.add_parser("active", parents=[common], help="episodes claimed by agents")
    sub.add_parser("next", parents=[common], help="claim the next episode and build its workspace")

    p = sub.add_parser("target", parents=[common], help="claim a specific episode/movie")
    p.add_argument("--project", "-p", required=True)
    p.add_argument("--episode", "-e")
    p.add_argument("--lang", "-l")
    p.add_argument("--full", action="store_true", help="retranslate every line, not just missing ones")

    p = sub.add_parser("enqueue", parents=[common], help="queue a show (or some episodes) for agents")
    p.add_argument("--project", "-p", required=True)
    p.add_argument("--episodes", "-e", help="comma-separated, e.g. S01E01,S01E02")
    p.add_argument("--all", action="store_true", help="include already-translated episodes (fills their gaps)")
    p.add_argument("--full", action="store_true", help="retranslate every line")
    p.add_argument("--priority", type=int, default=1)

    p = sub.add_parser("audit", parents=[common],
                       help="find episodes with untranslated lines, misplaced lines and broken text")
    p.add_argument("--project", "-p", help="the show to audit (not with --all)")
    p.add_argument("--all", action="store_true",
                   help="audit every project of the library (a background job on the server, followed here)")
    p.add_argument("--requeue", action="store_true",
                   help="queue incomplete episodes for agent gap-filling (implies --repair; misplaced and "
                        "wrong-alphabet lines are cleared first)")
    p.add_argument("--repair", action="store_true",
                   help="fix broken text: decode Greek mojibake, clear leaked prompt text (the old text is backed up); "
                        "subtitle files are repaired / exported again to match")
    p.add_argument("--full", action="store_true")
    p.add_argument("--no-misplaced", action="store_true", help="skip the check for lines on the wrong line")

    for name, help_text in (("check", "validate a work file without ingesting"),
                            ("submit", "validate, ingest and export a translation")):
        p = sub.add_parser(name, parents=[common], help=help_text)
        p.add_argument("task_id", nargs="?")
        p.add_argument("--file", "-f", help="read the translation from this file instead of translation.txt")
        if name == "submit":
            p.add_argument("--force", action="store_true", help="accept lines flagged as untranslated")
            p.add_argument("--context-guide")
            p.add_argument("--terms")
            p.add_argument("--summary")

    for name, help_text in (("release", "give a claimed episode back to the queue"),
                            ("cancel", "cancel a task and its queue item")):
        p = sub.add_parser(name, parents=[common], help=help_text)
        p.add_argument("task_id", nargs="?")
        if name == "cancel":
            p.add_argument("--all", action="store_true")

    p = sub.add_parser("clear", parents=[common], help="delete workspace folders")
    p.add_argument("--status", default="finished",
                   choices=["finished", "all", "completed", "cancelled", "expired", "released", "prepared"])

    p = sub.add_parser("sync", parents=[common], help="release stale claims; optionally sync a project with disk")
    p.add_argument("--project", "-p")
    p.add_argument("--disk", "-d", action="store_true", help="disk sync every project")

    p = sub.add_parser("run", parents=[common],
                       help="translate the queue unattended through an agent CLI (one prompt per episode)")
    p.add_argument("--engine", choices=["antigravity", "claude", "gemini"])
    p.add_argument("--model", help="main model (default: the runner's saved setting)")
    p.add_argument("--fallback", help="comma-separated models to use while the main one is out of quota")
    p.add_argument("--lanes", type=int, help="episodes translated at the same time")
    p.add_argument("--per-show", type=int, help="at most this many of them from one show")
    p.add_argument("--episodes", type=int, help="stop after this many episodes")
    p.add_argument("--watch", action="store_true", help="keep running when the queue is empty")
    p.add_argument("--follow", action="store_true", help="print the runner's log until it stops")
    p.add_argument("--status", action="store_true", help="show what the runner is doing")
    p.add_argument("--stop", action="store_true", help="stop after the episodes in hand")
    p.add_argument("--now", action="store_true", help="with --stop: cut off the requests in flight")

    args = parser.parse_args()
    if args.command == "audit" and bool(args.all) == bool(args.project):
        parser.error("audit needs either --project NAME or --all (not both)")
    c = Client()
    s = lambda: c.svc  # noqa: E731

    if args.command == "list":
        out(c.call("GET", "/api/agent/tasks/pending", local=lambda: {"tasks": s().list_pending_tasks()}), args, show_list)
    elif args.command == "active":
        out(c.call("GET", "/api/agent/tasks/active", local=lambda: {"tasks": s().list_active_tasks()}), args, show_active)
    elif args.command == "next":
        out(c.call("POST", "/api/agent/tasks/next", local=lambda: s().prepare_task_workspace(None)), args, show_claim)
    elif args.command == "target":
        payload = {"project_name": args.project, "episode_name": args.episode, "lang": args.lang, "full": args.full}
        out(c.call("POST", "/api/agent/tasks/target", payload,
                   local=lambda: s().prepare_target_workspace(args.project, args.episode, args.lang, args.full)), args, show_claim)
    elif args.command == "enqueue":
        eps = [e.strip() for e in args.episodes.split(",") if e.strip()] if args.episodes else None
        payload = {"project_name": args.project, "episode_names": eps, "priority": args.priority,
                   "full": args.full, "include_translated": args.all}
        out(c.call("POST", "/api/agent/enqueue", payload,
                   local=lambda: s().enqueue_target_project(args.project, eps, args.priority, args.full, args.all)),
            args, lambda r: (show_simple(r), print(f"Claim the first one: {CMD} next") if r.get("enqueued_count") else None))
    elif args.command == "audit" and args.all:
        audit_library(c, args)
    elif args.command == "audit":
        payload = {"project_name": args.project, "requeue": args.requeue, "full": args.full,
                   "misplaced": not args.no_misplaced, "repair": args.repair}
        out(c.call("POST", "/api/agent/audit", payload,
                   local=lambda: s().audit_project(args.project, args.requeue, args.full, not args.no_misplaced,
                                                   args.repair)),
            args, show_audit)
    elif args.command == "check":
        tid = resolve_task(c, args.task_id)
        content = Path(args.file).read_text(encoding="utf-8-sig") if args.file else None
        out(c.call("POST", f"/api/agent/tasks/{tid}/check", {"content": content},
                   local=lambda: s().check_task(tid, content)), args, show_check)
    elif args.command == "submit":
        tid = resolve_task(c, args.task_id)
        content = Path(args.file).read_text(encoding="utf-8-sig") if args.file else None
        read = lambda v: Path(v).read_text(encoding="utf-8") if v and Path(v).is_file() else v  # noqa: E731
        terms = read(args.terms)
        try:
            terms = json.loads(terms) if isinstance(terms, str) else terms
        except Exception:
            pass
        payload = {"content": content, "force": args.force, "context_guide": read(args.context_guide),
                   "new_terms": terms, "episode_summary": read(args.summary)}
        out(c.call("POST", f"/api/agent/tasks/{tid}/submit", payload,
                   local=lambda: s().submit_task_result(tid, content, payload["context_guide"], terms,
                                                        payload["episode_summary"], args.force)), args, show_submit)
    elif args.command == "release":
        tid = resolve_task(c, args.task_id)
        res = c.call("POST", f"/api/agent/tasks/{tid}/release",
                     local=lambda: {"success": s().release_agent_task(tid), "task_id": tid})
        res.setdefault("message", f"Released {tid}; the episode is back in the queue." if res.get("success") else f"Task {tid} not found.")
        out(res, args, show_simple)
    elif args.command == "cancel":
        if args.all:
            res = c.call("POST", "/api/agent/tasks/cancel-all", local=lambda: {"success": True, "cancelled": s().cancel_all_agent_tasks()})
            res.setdefault("message", f"Cancelled {res.get('cancelled', 0)} open task(s).")
        else:
            tid = resolve_task(c, args.task_id)
            res = c.call("POST", f"/api/agent/tasks/{tid}/cancel", local=lambda: {"success": s().cancel_agent_task(tid), "task_id": tid})
            res.setdefault("message", f"Cancelled {tid}." if res.get("success") else f"Task {tid} not found.")
        out(res, args, show_simple)
    elif args.command == "clear":
        res = c.call("POST", f"/api/agent/tasks/clear?status={args.status}",
                     local=lambda: {"success": True, "cleared": s().clear_agent_tasks(args.status)})
        res.setdefault("message", f"Removed {res.get('cleared', 0)} workspace folder(s) ({args.status}).")
        out(res, args, show_simple)
    elif args.command == "sync":
        res = c.call("POST", "/api/agent/tasks/sync",
                     local=lambda: {"success": True, "released_claims": s().housekeeping(),
                                    "reconciled_queue_items": s().reconcile_queue_with_storage()})
        if args.project:
            from urllib.parse import quote
            res["disk_sync"] = c.call("POST", f"/projects/{quote(args.project)}/sync-disk", {},
                                      local=lambda: __import__("services.disk_sync_service", fromlist=["x"])
                                      .disk_sync_service.sync_project_disk(args.project))
        elif args.disk:
            res["disk_sync"] = c.call("POST", "/api/sync/disk",
                                      local=lambda: __import__("services.disk_sync_service", fromlist=["x"])
                                      .disk_sync_service.sync_all_projects_disk())
        res.setdefault("message", f"Released {len(res.get('released_claims') or [])} stale claim(s); "
                                  f"completed {res.get('reconciled_queue_items', 0)} already-translated item(s).")
        out(res, args, show_simple)
    elif args.command == "run":
        run_command(c, args)


if __name__ == "__main__":
    main()
