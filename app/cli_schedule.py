"""
``ziya schedule`` -- the schedule registry from the command line.

    ziya schedule list                     registrations, unregistered requests, lock holder
    ziya schedule register <card> [--daily HH:MM | --every N UNIT | --cron EXPR | --at ISO]
    ziya schedule unregister <card>
    ziya schedule enable|disable <card>
    ziya schedule fire <card>              fire now through the scheduler path (counts)
    ziya schedule status                   who holds the scheduler lock

<card> is a card id or (case-insensitive) name, as for ``ziya task --card``.
Everything goes through app/agents/schedule_registry.py, the same code the
GUI and the API use, so the CLI cannot disagree with them.  Nothing here
needs a model or credentials except ``fire``.
"""
from __future__ import annotations

import asyncio
import sys
import time
from typing import Any, Dict, Optional


def _c(code: str, text: str) -> str:
    return f"\033[{code}m{text}\033[0m"


def _rel(at: Optional[int], now: int) -> str:
    if at is None:
        return "-"
    m = int(abs(at - now) / 60_000)
    span = f"{m}m" if m < 60 else (f"{m // 60}h {m % 60}m" if m < 1440 else f"{m // 1440}d {(m % 1440) // 60}h")
    return f"in {span}" if at >= now else f"{span} ago"


def _project_and_card(root: str, card_ref: Optional[str]):
    from app.cli_card_runner import resolve_card
    from app.storage.projects import ProjectStorage
    from app.utils.paths import get_ziya_home
    project = ProjectStorage(get_ziya_home()).get_by_path(root)
    if project is None:
        print("No project registered for this directory - open it in the Ziya GUI once.",
              file=sys.stderr)
        return None, None
    if card_ref is None:
        return project, None
    card, _ = resolve_card(root, card_ref)
    if card is None:
        print(f"Task card not found: {card_ref}", file=sys.stderr)
        return project, None
    return project, card


def _cadence_from_args(args) -> Optional[Dict[str, Any]]:
    if getattr(args, "daily", None):
        return {"schedule_mode": "daily_at", "schedule_daily_at": args.daily}
    if getattr(args, "every", None):
        n, unit = args.every
        return {"schedule_mode": "interval", "schedule_interval_value": int(n),
                "schedule_interval_unit": unit}
    if getattr(args, "cron", None):
        return {"schedule_mode": "cron", "schedule_cron": args.cron}
    if getattr(args, "at", None):
        return {"schedule_mode": "at", "schedule_at_iso": args.at}
    return None


def cmd_list(root: str) -> int:
    from app.agents import schedule_registry as registry
    project, _ = _project_and_card(root, None)
    if project is None:
        return 1
    listing = registry.list_schedules(project.id)
    now = int(time.time() * 1000)
    lock = listing["scheduler"]
    if lock.get("this_process"):
        who = "this process"
    elif lock.get("held"):
        who = f"pid {lock.get('pid')} on {lock.get('host')}"
    else:
        who = _c("33", "nobody - nothing is firing (is a Ziya server running?)")
    print(_c("1", "Scheduler lock:") + f" {who}\n")
    rows = listing["registrations"]
    if rows:
        print(_c("1", "Registered:"))
        w = max(len(r["card_name"] or r["card_id"]) for r in rows)
        for r in rows:
            name = (r["card_name"] or "(deleted card)").ljust(w)
            state = _c("32", "on ") if r["enabled"] else _c("90", "off")
            if r.get("problem"):
                nxt = _c("31", f"cannot fire: {r['problem']}")
            elif r["card_missing"]:
                nxt = _c("31", "card deleted")
            elif not r["enabled"]:
                nxt = "disabled"
            elif r["exhausted"]:
                nxt = f"finished ({r['fires_so_far']} runs)"
            else:
                nxt = "next " + _rel(r["next_fire_at"], now)
            last = r.get("last_run") or {}
            last_s = (f"last {_rel(r['last_fire_at'], now)}: {last.get('status', '?')}"
                      if r["last_fire_at"] else "never fired")
            print(f"  {state} {_c('36', name)}  {r['summary']:<22} {nxt:<28} {last_s}"
                  f"  {_c('90', 'id: ' + r['card_id'])}")
    else:
        print("No registered schedules.")
    if listing["unregistered"]:
        print("\n" + _c("33", "Requested, not registered (these do not fire):"))
        for u in listing["unregistered"]:
            print(f"  {_c('36', u['card_name'])}  {_c('90', 'id: ' + u['card_id'])}")
        print("\nRegister: " + _c("36", "ziya schedule register <card>"))
    return 0


def cmd_mutate(root: str, action: str, card_ref: str, args) -> int:
    from app.agents import schedule_registry as registry
    project, card = _project_and_card(root, card_ref)
    if card is None:
        return 1
    try:
        if action == "register":
            r = registry.register(project.id, card, overrides=_cadence_from_args(args))
            print(f"Registered {_c('36', card.name)}: {registry.describe_cadence(r)}")
        elif action == "unregister":
            if not registry.unregister(project.id, card.id):
                print("Card is not registered.", file=sys.stderr)
                return 1
            print(f"Unregistered {_c('36', card.name)}")
        else:  # enable / disable
            r = registry.update_registration(project.id, card.id, {"enabled": action == "enable"})
            print(f"{action.capitalize()}d {_c('36', card.name)}: {registry.describe_cadence(r)}")
    except registry.AlreadyRegistered:
        print("Card is already registered (use enable/disable, or unregister first).",
              file=sys.stderr)
        return 1
    except registry.NotRegistered:
        print("Card is not registered.", file=sys.stderr)
        return 1
    except ValueError as e:
        print(_c("31", str(e)), file=sys.stderr)
        return 2
    return 0


def report_fire(project_id: str, card_name: str, run_id: str) -> int:
    """Print the outcome of a fire_now run.  app/cli.py::_fire_card resolves
    the card, awaits the run inside the MCP-wrapped loop, then calls this.
    Exit 0 for done / partial, 1 otherwise."""
    from app.storage.task_runs import TaskRunStorage
    from app.utils.paths import get_project_dir
    run = TaskRunStorage(get_project_dir(project_id)).get(run_id)
    status = run.status if run else "?"
    colour = "32" if status == "done" else ("33" if status in ("held", "partial") else "31")
    print(f"Fired {_c('36', card_name)} -> run {run_id[:8]}: {_c(colour, status)}"
          + (f" - {run.error}" if run and run.error else ""))
    return 0 if status in ("done", "partial") else 1


def cmd_status() -> int:
    from app.agents.schedule_registry import lock_status
    s = lock_status()
    if s["this_process"]:
        print("held by this process")
    elif s["held"]:
        print(f"held by pid {s['pid']} on {s['host']} (heartbeat {s['heartbeat_age_s']}s ago)")
    else:
        print(_c("33", "not held - nothing is firing") + f" ({s['lock_path']})")
    return 0
