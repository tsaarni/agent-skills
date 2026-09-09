#!/usr/bin/env python3
"""Pi Session Analytics & Audit Tool.

Extracts deterministic performance metrics, cost economics, cache health,
and user habits from Pi Coding Agent session logs (~/.pi/agent/sessions/**/*.jsonl).

Usage:
    python3 analyze.py [--latest] [path_to_session.jsonl]
    python3 analyze.py --habits [--days N]
    python3 analyze.py --latest --summary (or --llm-input)
    python3 analyze.py --latest --json
"""

import argparse
from collections import Counter, defaultdict
from datetime import date, datetime, time, timedelta, timezone
import glob
import json
import os
import sys
from typing import Any, Dict, List, Optional, Tuple


def load_models_store() -> Dict[str, float]:
    """Load default input rates ($/token) from Pi's local models-store.json if available."""
    path = os.path.expanduser("~/.pi/agent/models-store.json")
    rates: Dict[str, float] = {}
    if not os.path.isfile(path):
        return rates
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            for provider, info in data.items():
                if isinstance(info, dict):
                    for m in info.get("models", []):
                        mid = m.get("id")
                        cost = m.get("cost", {})
                        in_rate = cost.get("input")
                        if mid and in_rate is not None:
                            rates[mid] = float(in_rate) / 1_000_000.0
    except Exception:
        pass
    return rates


def parse_timestamp(ts: Any) -> Optional[datetime]:
    """Parse ISO-8601 string or millisecond integer timestamp into local datetime."""
    if not ts:
        return None
    try:
        if isinstance(ts, (int, float)):
            return datetime.fromtimestamp(ts / 1000.0, tz=timezone.utc).astimezone()
        if isinstance(ts, str):
            # Normalise UTC Z suffix
            clean_ts = ts.replace("Z", "+00:00")
            return datetime.fromisoformat(clean_ts).astimezone()
    except Exception:
        pass
    return None


def parse_user_datetime(dt_str: str, is_end: bool = False) -> datetime:
    """Parse user date/time string in system local timezone."""
    dt_str = dt_str.strip()
    formats = [
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%Y-%m-%d",
    ]
    local_tz = datetime.now().astimezone().tzinfo
    for fmt in formats:
        try:
            parsed = datetime.strptime(dt_str, fmt)
            if fmt == "%Y-%m-%d":
                default_time = time.max if is_end else time.min
                parsed = datetime.combine(parsed.date(), default_time)
            return parsed.replace(tzinfo=local_tz)
        except ValueError:
            continue
    raise ValueError(f"Invalid date/time format: '{dt_str}'. Expected 'YYYY-MM-DD' or 'YYYY-MM-DD HH:MM'")


def resolve_time_filters(args: argparse.Namespace) -> Tuple[Optional[datetime], Optional[datetime], str, bool]:
    """Resolve CLI time presets and arguments into (since_dt, until_dt, label, is_micro)."""
    now = datetime.now().astimezone()
    local_tz = now.tzinfo

    is_micro_forced = getattr(args, "timeline", False)

    if args.today:
        start = datetime.combine(now.date(), time.min, tzinfo=local_tz)
        return start, now, "Today", is_micro_forced

    if args.yesterday:
        y_date = now.date() - timedelta(days=1)
        start = datetime.combine(y_date, time.min, tzinfo=local_tz)
        end = datetime.combine(y_date, time.max, tzinfo=local_tz)
        return start, end, "Yesterday", is_micro_forced

    if args.this_week:
        monday = now.date() - timedelta(days=now.weekday())
        start = datetime.combine(monday, time.min, tzinfo=local_tz)
        return start, now, "This Week", is_micro_forced

    if args.last_week:
        monday = now.date() - timedelta(days=now.weekday() + 7)
        sunday = monday + timedelta(days=6)
        start = datetime.combine(monday, time.min, tzinfo=local_tz)
        end = datetime.combine(sunday, time.max, tzinfo=local_tz)
        return start, end, "Last Week", is_micro_forced

    if args.this_month:
        start = datetime(now.year, now.month, 1, 0, 0, 0, tzinfo=local_tz)
        return start, now, "This Month", is_micro_forced

    if args.last_month:
        first_this = date(now.year, now.month, 1)
        last_prev = first_this - timedelta(days=1)
        start = datetime(last_prev.year, last_prev.month, 1, 0, 0, 0, tzinfo=local_tz)
        end = datetime.combine(last_prev, time.max, tzinfo=local_tz)
        return start, end, "Last Month", is_micro_forced

    if args.days:
        start = now - timedelta(days=args.days)
        return start, now, f"Last {args.days} Days", is_micro_forced

    since_dt = parse_user_datetime(args.since, is_end=False) if args.since else None
    until_dt = parse_user_datetime(args.until, is_end=True) if args.until else None

    if since_dt and until_dt and until_dt < since_dt:
        raise ValueError(f"--until ({until_dt}) cannot be earlier than --since ({since_dt})")

    label = "Custom Time Window"
    if since_dt and until_dt:
        label = f"{since_dt.strftime('%Y-%m-%d %H:%M')} to {until_dt.strftime('%Y-%m-%d %H:%M')}"
    elif since_dt:
        label = f"Since {since_dt.strftime('%Y-%m-%d %H:%M')}"
    elif until_dt:
        label = f"Until {until_dt.strftime('%Y-%m-%d %H:%M')}"

    if not since_dt and not until_dt:
        raise ValueError("Time-window query requires a time boundary (e.g. --today, --yesterday, --since 'YYYY-MM-DD').")

    # Micro mode: explicit --timeline flag OR a bounded window <= 2 hours (7200s)
    is_micro = is_micro_forced or bool(since_dt and until_dt and (until_dt - since_dt).total_seconds() <= 7200)

    return since_dt, until_dt, label, is_micro


def find_latest_session(cwd: Optional[str] = None, global_search: bool = False) -> Optional[str]:
    """Locate the most recent session JSONL for a given workspace or globally across all workspaces."""
    sessions_dir = os.path.expanduser("~/.pi/agent/sessions")
    if not os.path.isdir(sessions_dir):
        return None

    all_files = glob.glob(os.path.join(sessions_dir, "*", "*.jsonl"))
    if not all_files:
        return None

    all_files.sort(key=os.path.getmtime, reverse=True)
    global_newest = all_files[0]

    if global_search:
        return global_newest

    if not cwd:
        cwd = os.getcwd()

    # Pi workspace folder pattern: --Users-tsaarni-work-foo--
    safe_name = "--" + cwd.strip("/").replace("/", "-") + "--"
    target_dir = os.path.join(sessions_dir, safe_name)
    local_latest: Optional[str] = None

    if os.path.isdir(target_dir):
        files = glob.glob(os.path.join(target_dir, "*.jsonl"))
        if files:
            local_latest = max(files, key=os.path.getmtime)

    # Fallback: scan all sessions and match cwd in header
    if not local_latest:
        for f in all_files:
            try:
                with open(f, "r", encoding="utf-8") as fp:
                    first_line = fp.readline()
                    if first_line:
                        header = json.loads(first_line)
                        if header.get("cwd") == cwd:
                            local_latest = f
                            break
            except Exception:
                continue

    if local_latest:
        # Check if local session is stale compared to global activity
        local_mtime = os.path.getmtime(local_latest)
        global_mtime = os.path.getmtime(global_newest)
        # If global session is newer by > 1 hour
        if global_newest != local_latest and (global_mtime - local_mtime) > 3600:
            local_dt = datetime.fromtimestamp(local_mtime, tz=timezone.utc).astimezone()
            global_dt = datetime.fromtimestamp(global_mtime, tz=timezone.utc).astimezone()
            global_ws = "another workspace"
            try:
                with open(global_newest, "r", encoding="utf-8") as gfp:
                    ghdr = json.loads(gfp.readline())
                    if ghdr.get("cwd"):
                        global_ws = ghdr.get("cwd")
            except Exception:
                pass
            sys.stderr.write(
                f"[INFO] Auditing local workspace session from {local_dt.strftime('%Y-%m-%d %H:%M')}.\n"
                f"       A newer session ({global_dt.strftime('%Y-%m-%d %H:%M')}) exists in '{global_ws}'.\n"
                f"       Use --global (-g) to audit the newest session across all workspaces.\n\n"
            )
        return local_latest

    # If no local session found at all, fall back to global
    sys.stderr.write(f"[INFO] No sessions found for current workspace '{cwd}'. Falling back to newest global session.\n\n")
    return global_newest


def resolve_session_target(
    target: Optional[str] = None,
    cwd: Optional[str] = None,
    global_search: bool = False,
    recent_rank: Optional[int] = None,
) -> Optional[str]:
    """Resolve a target argument (file path, UUID prefix, or recency rank) to a session file path."""
    sessions_dir = os.path.expanduser("~/.pi/agent/sessions")
    if not os.path.isdir(sessions_dir):
        return None

    all_files = glob.glob(os.path.join(sessions_dir, "*", "*.jsonl"))
    if not all_files:
        return None

    all_files.sort(key=os.path.getmtime, reverse=True)

    # 1. Explicit recent rank flag passed (e.g. -n 2)
    if recent_rank is not None and recent_rank > 0:
        if recent_rank <= len(all_files):
            return all_files[recent_rank - 1]
        return None

    # 2. Target is an existing file path
    if target and os.path.isfile(target):
        return target

    # 3. Target is an integer rank (e.g. "1", "2")
    if target and target.isdigit():
        rank = int(target)
        if 1 <= rank <= len(all_files):
            return all_files[rank - 1]

    # 4. Target matches a UUID or ID prefix
    if target:
        clean_target = target.strip()
        matched = []
        for f in all_files:
            bname = os.path.basename(f)
            if clean_target in bname:
                matched.append(f)
        if matched:
            return matched[0]  # Return newest match

        # Deep header check if not in basename
        for f in all_files:
            try:
                with open(f, "r", encoding="utf-8") as fp:
                    hdr = json.loads(fp.readline())
                    sid = hdr.get("id", "")
                    if sid.startswith(clean_target) or clean_target in sid:
                        return f
            except Exception:
                continue

    # 5. Default fallback to latest session
    return find_latest_session(cwd=cwd, global_search=global_search)


def list_sessions(
    limit: int = 15,
    workspace: Optional[str] = None,
    errors_only: bool = False,
    since_dt: Optional[datetime] = None,
    until_dt: Optional[datetime] = None,
    as_json: bool = False,
) -> None:
    """List recent sessions across workspaces in reverse chronological order."""
    sessions_dir = os.path.expanduser("~/.pi/agent/sessions")
    if not os.path.isdir(sessions_dir):
        print(f"No session directory found at {sessions_dir}")
        return

    all_files = glob.glob(os.path.join(sessions_dir, "*", "*.jsonl"))
    if not all_files:
        print(f"No sessions found in {sessions_dir}")
        return

    all_files.sort(key=os.path.getmtime, reverse=True)

    sessions_data = []
    user_home = os.path.expanduser("~")

    for f in all_files:
        mtime = os.path.getmtime(f)
        f_dt = datetime.fromtimestamp(mtime, tz=timezone.utc).astimezone()
        if since_dt and f_dt < since_dt:
            continue
        if until_dt and f_dt > until_dt:
            continue

        sid = None
        cwd = None
        first_ts = None
        asst_turns = 0
        cost = 0.0
        model = "unknown"
        err_count = 0
        err_types = []

        try:
            with open(f, "r", encoding="utf-8") as fp:
                for line in fp:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entry = json.loads(line)
                    except Exception:
                        continue

                    etype = entry.get("type")
                    if etype == "session" and not sid:
                        sid = entry.get("id")
                        cwd = entry.get("cwd")
                        first_ts = parse_timestamp(entry.get("timestamp"))
                    elif etype == "message" and entry.get("message", {}).get("role") == "assistant":
                        asst_turns += 1
                        msg = entry.get("message", {})
                        m = msg.get("model")
                        if m:
                            model = m
                        cost += msg.get("usage", {}).get("cost", {}).get("total", 0.0)
                        if msg.get("stopReason") == "error":
                            err_count += 1
                            eraw = str(msg.get("errorMessage", ""))
                            if "429" in eraw and "429" not in err_types:
                                err_types.append("429")
                            elif "503" in eraw and "503" not in err_types:
                                err_types.append("503")
                            elif "500" in eraw and "500" not in err_types:
                                err_types.append("500")
        except Exception:
            continue

        # Filter by workspace if specified
        if workspace:
            if not cwd or workspace not in cwd:
                continue

        # Filter by errors_only
        if errors_only and err_count == 0:
            continue

        dt_display = (first_ts or f_dt).strftime("%Y-%m-%d %H:%M")
        ws_display = (cwd or "unknown").replace(user_home, "~")
        if len(ws_display) > 28:
            ws_display = "..." + ws_display[-25:]

        status_display = f"{err_count} errs" if err_count else "OK"
        if err_types:
            status_display += f" ({','.join(err_types[:2])})"

        sessions_data.append({
            "index": len(sessions_data) + 1,
            "id": sid or os.path.basename(f),
            "id_short": (sid or os.path.basename(f))[:8],
            "datetime": dt_display,
            "timestamp": (first_ts or f_dt).isoformat(),
            "cwd": cwd or "unknown",
            "workspace_short": ws_display,
            "model": model,
            "turns": asst_turns,
            "cost": cost,
            "error_count": err_count,
            "status": status_display,
            "path": f,
        })

        if limit > 0 and len(sessions_data) >= limit:
            break

    if not sessions_data:
        print("No matching sessions found.")
        return

    if as_json:
        print(json.dumps(sessions_data, indent=2, default=str))
        return

    print("\n" + "=" * 98)
    print("                           PI SESSIONS NAVIGATOR")
    print("=" * 98)
    print(f"{'Idx':<6} {'Date & Time':<18} {'Workspace':<28} {'Model':<18} {'Turns':>5}   {'Cost':>7}  {'Status':<14}  {'Session ID'}")
    print("-" * 98)
    for s in sessions_data:
        print(f"[{s['index']:<2}]   {s['datetime']:<18} {s['workspace_short']:<28} {s['model'][:17]:<18} {s['turns']:>5}   ${s['cost']:>6.2f}  {s['status']:<14}  {s['id_short']}")
    print("=" * 98)
    print("Tip: Run 'analyze.py <Idx>' (e.g. 'analyze.py 2') or 'analyze.py <Session ID>' to audit.")
    print("")


def list_workspaces(as_json: bool = False) -> None:
    """List all workspaces recorded in Pi session logs with session counts and recency."""
    sessions_dir = os.path.expanduser("~/.pi/agent/sessions")
    if not os.path.isdir(sessions_dir):
        print(f"No session directory found at {sessions_dir}")
        return

    dirs = [d for d in glob.glob(os.path.join(sessions_dir, "*")) if os.path.isdir(d)]
    if not dirs:
        print("No workspaces found.")
        return

    user_home = os.path.expanduser("~")
    workspaces = []

    for d in dirs:
        files = glob.glob(os.path.join(d, "*.jsonl"))
        if not files:
            continue
        newest = max(files, key=os.path.getmtime)
        mtime = os.path.getmtime(newest)
        cwd = None
        sid = None
        try:
            with open(newest, "r", encoding="utf-8") as fp:
                hdr = json.loads(fp.readline())
                cwd = hdr.get("cwd")
                sid = hdr.get("id")
        except Exception:
            pass
        if not cwd:
            raw_name = os.path.basename(d).strip("-")
            cwd = "/" + raw_name.replace("-", "/")

        ws_display = cwd.replace(user_home, "~")
        dt_str = datetime.fromtimestamp(mtime, tz=timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M")

        workspaces.append({
            "cwd": cwd,
            "workspace_short": ws_display,
            "sessions_count": len(files),
            "latest_mtime": mtime,
            "latest_activity": dt_str,
            "latest_session_id": sid or "unknown",
            "latest_session_short": (sid or "")[:8],
        })

    workspaces.sort(key=lambda x: x["latest_mtime"], reverse=True)

    if as_json:
        print(json.dumps(workspaces, indent=2, default=str))
        return

    print("\n" + "=" * 82)
    print("                          PI WORKSPACES INDEX")
    print("=" * 82)
    print(f"{'Idx':<5} {'Workspace':<44} {'Sessions':>8}   {'Latest Activity':<17} {'Latest ID'}")
    print("-" * 82)
    for idx, w in enumerate(workspaces, start=1):
        ws_name = w["workspace_short"]
        if len(ws_name) > 44:
            ws_name = "..." + ws_name[-41:]
        print(f"[{idx:<2}] {ws_name:<44} {w['sessions_count']:>8}   {w['latest_activity']:<17} {w['latest_session_short']}")
    print("=" * 82)
    print("Tip: Run 'analyze.py --list --workspace <name>' to filter sessions by workspace.")
    print("")


def load_active_branch(jsonl_path: str) -> Tuple[List[Dict[str, Any]], int, Dict[str, Any]]:
    """Parse session JSONL and resolve the active conversation branch via parentId pointers.

    Returns:
        branch: Chronological list of nodes on the active path (root to leaf).
        total_raw_entries: Total valid JSON lines parsed.
        session_meta: Dict containing root session properties (cwd, id, start_time).
    """
    entries: Dict[str, Dict[str, Any]] = {}
    children: Dict[str, List[str]] = {}
    last_id: Optional[str] = None
    session_meta: Dict[str, Any] = {}
    total_raw_entries = 0

    with open(jsonl_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                # Live file may have a partially written trailing line
                continue

            total_raw_entries += 1
            entry_id = entry.get("id")
            parent_id = entry.get("parentId")
            entry_type = entry.get("type")

            if entry_type == "session" and not session_meta:
                session_meta = {
                    "id": entry.get("id"),
                    "cwd": entry.get("cwd"),
                    "timestamp": entry.get("timestamp"),
                    "version": entry.get("version"),
                }

            if entry_id:
                entries[entry_id] = entry
                last_id = entry_id
                if parent_id:
                    children.setdefault(parent_id, []).append(entry_id)

    if not entries:
        return [], total_raw_entries, session_meta

    # Active leaf determination: pick the leaf node with latest timestamp or last in file
    leaves = [eid for eid in entries if eid not in children]
    active_leaf_id = last_id
    if leaves:
        if last_id not in leaves:
            # Sort leaves by timestamp if available
            leaves.sort(
                key=lambda eid: parse_timestamp(entries[eid].get("timestamp"))
                or datetime.min.replace(tzinfo=timezone.utc),
                reverse=True,
            )
            active_leaf_id = leaves[0]

    # Walk backwards from active leaf to root
    branch: List[Dict[str, Any]] = []
    curr = active_leaf_id
    visited = set()
    while curr and curr in entries and curr not in visited:
        visited.add(curr)
        branch.append(entries[curr])
        curr = entries[curr].get("parentId")

    branch.reverse()
    return branch, total_raw_entries, session_meta


def analyze_session(branch: List[Dict[str, Any]], total_raw_entries: int, meta: Dict[str, Any]) -> Dict[str, Any]:
    """Calculate comprehensive metrics along the active branch."""
    fresh_input = 0
    cache_read = 0
    output_tokens = 0
    reasoning_tokens = 0
    total_cost = 0.0
    cost_fresh_input = 0.0
    cost_cache_read = 0.0
    cost_output = 0.0
    models = Counter()
    model_rates: Dict[str, float] = load_models_store()  # Seed from Pi's local models registry

    # Pre-pass: dynamically deduce exact unit input rate from turns in this session
    for entry in branch:
        if entry.get("type") == "message" and entry.get("message", {}).get("role") == "assistant":
            m = entry.get("message", {}).get("model")
            u = entry.get("message", {}).get("usage", {})
            inp_cnt = u.get("input", 0)
            c_inp = u.get("cost", {}).get("input", 0.0)
            if m and inp_cnt > 0 and c_inp > 0:
                model_rates[m] = c_inp / inp_cnt

    tool_counts = Counter()
    tool_errors: List[Dict[str, Any]] = []

    custom_nodes_active: List[Dict[str, Any]] = []
    user_prompts: List[Tuple[Optional[datetime], int]] = []
    think_times: List[float] = []

    last_assistant_end_ts: Optional[datetime] = None
    last_successful_assistant_ts: Optional[datetime] = None
    last_successful_total_ctx: int = 0
    last_successful_model: Optional[str] = None
    prev_assistant_ts: Optional[datetime] = None
    prev_assistant_model: Optional[str] = None
    prev_total_ctx = 0

    cache_busts: List[Dict[str, Any]] = []
    prefix_busts: List[Dict[str, Any]] = []
    server_evictions: List[Dict[str, Any]] = []
    idle_expirations: List[Dict[str, Any]] = []
    model_switches: List[Dict[str, Any]] = []
    assistant_turns: List[Dict[str, Any]] = []
    cost_by_model = Counter()

    first_ts: Optional[datetime] = parse_timestamp(meta.get("timestamp"))
    last_ts: Optional[datetime] = None

    for idx, entry in enumerate(branch):
        etype = entry.get("type")
        ts = parse_timestamp(entry.get("timestamp"))
        if ts:
            if not first_ts:
                first_ts = ts
            last_ts = ts

        if etype == "custom":
            custom_nodes_active.append({
                "id": entry.get("id"),
                "customType": entry.get("customType"),
                "timestamp": entry.get("timestamp"),
            })

        elif etype == "message":
            msg = entry.get("message", {})
            role = msg.get("role")

            if role == "user":
                content = msg.get("content", "")
                if isinstance(content, list):
                    text_len = sum(len(c.get("text", "")) for c in content if isinstance(c, dict))
                else:
                    text_len = len(str(content))
                user_prompts.append((ts, text_len))

                if last_assistant_end_ts and ts:
                    delta_sec = (ts - last_assistant_end_ts).total_seconds()
                    if delta_sec >= 0:
                        think_times.append(delta_sec)

            elif role == "assistant":
                model = msg.get("model") or "unknown"
                models[model] += 1

                usage = msg.get("usage", {})
                inp = usage.get("input", 0)
                cr = usage.get("cacheRead", 0)
                out = usage.get("output", 0)
                reas = usage.get("reasoning", 0)
                cost_dict = usage.get("cost", {})
                c_inp = cost_dict.get("input", 0.0)
                c_cr = cost_dict.get("cacheRead", 0.0)
                c_out = cost_dict.get("output", 0.0)
                cost = cost_dict.get("total", 0.0)
                is_error = (msg.get("stopReason") == "error") or bool(msg.get("errorMessage"))

                fresh_input += inp
                cache_read += cr
                output_tokens += out
                reasoning_tokens += reas
                total_cost += cost
                cost_fresh_input += c_inp
                cost_cache_read += c_cr
                cost_output += c_out
                cost_by_model[model] += cost

                tot_ctx = inp + cr
                turn_idx = len(assistant_turns) + 1

                # Evaluate cache transitions against the last primed warm context
                warm_ctx = last_successful_total_ctx if last_successful_total_ctx > 0 else prev_total_ctx
                reference_model = last_successful_model or prev_assistant_model
                reference_ts = last_successful_assistant_ts or prev_assistant_ts
                time_since_prime = (ts - reference_ts).total_seconds() if (ts and reference_ts) else 0.0

                if not is_error and tot_ctx > 0:
                    # Track in-flight model switches
                    if reference_model and reference_model != model:
                        model_switches.append({
                            "turn": turn_idx,
                            "from_model": reference_model,
                            "to_model": model,
                            "fresh_input": inp,
                            "cost": cost,
                            "total_ctx": tot_ctx,
                        })
                    # Check for cache drops on warm context (>= 32k tokens) within the same model
                    elif warm_ctx >= 32768 and (cr == 0 or cr < 0.1 * tot_ctx) and tot_ctx >= 32768:
                        event_info = {
                            "turn": turn_idx,
                            "total_ctx": tot_ctx,
                            "fresh_input": inp,
                            "cost": cost,
                            "time_gap_sec": time_since_prime,
                        }
                        if time_since_prime > 300.0:  # > 5 minutes indicates provider server TTL expiration
                            idle_expirations.append(event_info)
                        else:
                            cache_busts.append(event_info)
                            if len(custom_nodes_active) > 0:
                                event_info["reason"] = "graph_pollution"
                                prefix_busts.append(event_info)
                            else:
                                event_info["reason"] = "clean_prefix_eviction"
                                server_evictions.append(event_info)

                # Collect tool calls initiated by assistant
                content = msg.get("content", [])
                tool_calls_in_turn = []
                if isinstance(content, list):
                    for c in content:
                        if isinstance(c, dict) and c.get("type") == "toolCall":
                            tname = c.get("name", "unknown")
                            tool_counts[tname] += 1
                            tool_calls_in_turn.append(tname)

                # Prior context trigger: find previous conversational, model change, or custom entry
                trigger = "none"
                for back_idx in range(idx - 1, -1, -1):
                    pe = branch[back_idx]
                    ptype = pe.get("type")
                    if ptype == "message":
                        prole = pe.get("message", {}).get("role", "unknown")
                        if prole == "toolResult":
                            tname = pe.get("message", {}).get("toolName", "unknown")
                            trigger = f"toolResult:{tname}"
                        else:
                            trigger = prole
                        break
                    elif ptype == "model_change":
                        trigger = f"model_change:{pe.get('modelId')}"
                        break
                    elif ptype == "custom":
                        trigger = f"custom:{pe.get('customType')}"
                        break

                assistant_turns.append({
                    "turn": turn_idx,
                    "ts": ts,
                    "model": model,
                    "fresh_input": inp,
                    "cache_read": cr,
                    "total_ctx": tot_ctx,
                    "output": out,
                    "reasoning": reas,
                    "cost": cost,
                    "cost_output": c_out,
                    "trigger": trigger,
                    "tools": tool_calls_in_turn,
                    "is_error": is_error,
                    "error_msg": msg.get("errorMessage") if is_error else None,
                })

                if not is_error and tot_ctx > 0:
                    last_successful_total_ctx = tot_ctx
                    last_successful_assistant_ts = ts
                    last_successful_model = model

                prev_total_ctx = tot_ctx
                prev_assistant_ts = ts
                prev_assistant_model = model
                last_assistant_end_ts = ts

            elif role == "toolResult":
                tname = msg.get("toolName", "unknown")
                if msg.get("isError", False):
                    err_content = msg.get("content", "")
                    if isinstance(err_content, list) and err_content:
                        first_item = err_content[0]
                        if isinstance(first_item, dict) and "text" in first_item:
                            snippet = first_item["text"][:120]
                        else:
                            snippet = str(first_item)[:120]
                    else:
                        snippet = str(err_content)[:120]
                    tool_errors.append({
                        "tool": tname,
                        "snippet": snippet.replace("\n", " ").strip(),
                    })

    # Calculate rolling 60-second throughput velocity (TPM and RPM)
    peak_tpm_60s = 0
    peak_tpm_turn = 1
    peak_rpm_60s = 0
    api_errors: List[Dict[str, Any]] = []

    for i, t in enumerate(assistant_turns):
        t_ts = t.get("ts")
        if not t_ts:
            continue
        w_start = t_ts.timestamp() - 60.0
        w_tokens = 0
        w_count = 0
        for prev in assistant_turns[:i]:
            p_ts = prev.get("ts")
            if p_ts and p_ts.timestamp() >= w_start:
                w_tokens += prev["total_ctx"]
                w_count += 1
        t["rolling_tpm"] = w_tokens
        t["rolling_rpm"] = w_count
        if w_tokens > peak_tpm_60s:
            peak_tpm_60s = w_tokens
            peak_tpm_turn = t["turn"]
        if w_count > peak_rpm_60s:
            peak_rpm_60s = w_count
        if t.get("error_msg"):
            err_raw = str(t["error_msg"])
            # Extract clean error code/snippet
            err_snippet = err_raw[:120].replace("\n", " ")
            if "429" in err_raw:
                err_snippet = "429 Too Many Requests (Rate Limit)"
            elif "503" in err_raw:
                err_snippet = "503 Service Unavailable"
            elif "500" in err_raw:
                err_snippet = "500 Internal Server Error"
            elif "401" in err_raw:
                err_snippet = "401 Unauthorized"
            api_errors.append({
                "turn": t["turn"],
                "ts": t["ts"],
                "total_ctx": t["total_ctx"],
                "rolling_tpm": w_tokens,
                "rolling_rpm": w_count,
                "snippet": err_snippet,
                "raw_error": err_raw,
            })

    # Calculate uncached cost comparison (provider-neutral: base input rate + actual output cost)
    cost_without_cache = 0.0
    for t in assistant_turns:
        rate = model_rates.get(t["model"], 0.0)
        cost_without_cache += (t["total_ctx"] * rate) + t["cost_output"]
    if cost_without_cache < total_cost:
        # Fallback if rates couldn't be resolved cleanly
        cost_without_cache = total_cost

    savings = cost_without_cache - total_cost
    savings_pct = (savings / cost_without_cache * 100) if cost_without_cache > 0 else 0.0
    reasoning_pct = (reasoning_tokens / output_tokens * 100) if output_tokens > 0 else 0.0

    # Sort turns by largest fresh input to detect context spikes
    sorted_spikes = sorted(assistant_turns, key=lambda t: t["fresh_input"], reverse=True)
    top_spikes = sorted_spikes[:15]

    wall_duration = (last_ts - first_ts).total_seconds() if (first_ts and last_ts) else 0.0
    total_tokens = fresh_input + cache_read + output_tokens
    tot_in = fresh_input + cache_read
    hit_rate = (cache_read / tot_in * 100) if tot_in > 0 else 0.0

    return {
        "meta": meta,
        "first_ts": first_ts,
        "last_ts": last_ts,
        "wall_duration_sec": wall_duration,
        "total_raw_entries": total_raw_entries,
        "active_branch_entries": len(branch),
        "abandoned_entries": total_raw_entries - len(branch),
        "models": dict(models),
        "assistant_turns_count": len(assistant_turns),
        "user_prompts_count": len(user_prompts),
        "autonomy_ratio": (len(assistant_turns) / len(user_prompts)) if user_prompts else 0.0,
        "tokens": {
            "fresh_input": fresh_input,
            "cache_read": cache_read,
            "output": output_tokens,
            "reasoning": reasoning_tokens,
            "reasoning_pct": reasoning_pct,
            "total": total_tokens,
            "hit_rate_pct": hit_rate,
        },
        "cost": {
            "total": total_cost,
            "fresh_input": cost_fresh_input,
            "cache_read": cost_cache_read,
            "output": cost_output,
            "without_cache": cost_without_cache,
            "savings": savings,
            "savings_pct": savings_pct,
        },
        "cost_by_model": dict(cost_by_model),
        "velocity": {
            "peak_tpm_60s": peak_tpm_60s,
            "peak_tpm_turn": peak_tpm_turn,
            "peak_rpm_60s": peak_rpm_60s,
            "api_errors": api_errors,
        },
        "cache_health": {
            "durable_custom_nodes": len(custom_nodes_active),
            "custom_nodes_details": custom_nodes_active,
            "cache_busts": cache_busts,
            "prefix_busts": prefix_busts,
            "server_evictions": server_evictions,
            "idle_expirations": idle_expirations,
            "model_switches": model_switches,
        },
        "tools": {
            "total_calls": sum(tool_counts.values()),
            "by_name": dict(tool_counts),
            "error_count": len(tool_errors),
            "error_rate_pct": (len(tool_errors) / sum(tool_counts.values()) * 100) if tool_counts else 0.0,
            "errors": tool_errors,
        },
        "user_habits": {
            "avg_prompt_len": sum(l for _, l in user_prompts) / len(user_prompts) if user_prompts else 0,
            "think_times_count": len(think_times),
            "avg_think_sec": sum(think_times) / len(think_times) if think_times else 0,
            "median_think_sec": sorted(think_times)[len(think_times) // 2] if think_times else 0,
            "max_think_sec": max(think_times) if think_times else 0,
        },
        "top_spikes": top_spikes,
        "assistant_turns": assistant_turns,
        "recent_turns": assistant_turns[-5:],
    }


def format_duration(seconds: float) -> str:
    """Format seconds into human-readable duration."""
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    if h > 0:
        return f"{h}h {m:02d}m {s:02d}s"
    if m > 0:
        return f"{m}m {s:02d}s"
    return f"{s}s"


def print_session_report(data: Dict[str, Any], verbose: bool = False) -> None:
    """Render a clean, formatted terminal report."""
    meta = data["meta"]
    tokens = data["tokens"]
    cache = data["cache_health"]
    tools = data["tools"]
    habits = data["user_habits"]

    print("\n" + "=" * 68)
    print("                PI SESSION AUDIT REPORT")
    print("=" * 68)

    started_str = data["first_ts"].strftime("%Y-%m-%d %H:%M:%S %Z") if data["first_ts"] else "Unknown"
    dur_str = format_duration(data["wall_duration_sec"])
    print(f"Session ID  : {meta.get('id', 'Unknown')}")
    print(f"Workspace   : {meta.get('cwd', 'Unknown')}")
    print(f"Started     : {started_str} ({dur_str} elapsed)")
    models_str = ", ".join(f"{m} ({c} turns)" for m, c in data["models"].items())
    print(f"Model(s)    : {models_str or 'None'}")
    print(f"Graph Path  : {data['active_branch_entries']} active nodes "
          f"({data['abandoned_entries']} abandoned turns pruned)")

    print("\n" + "-" * 68)
    print("1. COST & TOKEN ECONOMICS")
    print("-" * 68)
    cost = data["cost"]
    print(f"  Actual Cost      : ${cost['total']:.4f}")
    if len(data["models"]) > 1:
        print("  Model Breakdown  :")
        max_m_turns = max(data["models"].values()) if data["models"] else 1
        tot_m_turns = sum(data["models"].values()) if data["models"] else 1
        for m, count in data["models"].items():
            pct = count / tot_m_turns * 100
            m_cost = data["cost_by_model"].get(m, 0.0)
            bar = "#" * int((count / max_m_turns) * 20)
            print(f"    * {m:<20}: {count:<3} turns ({pct:>4.1f}%) | ${m_cost:<6.4f} | {bar}")
    if cost["without_cache"] > cost["total"]:
        print(f"  Cost w/o Cache   : ${cost['without_cache']:.4f} (Saved ${cost['savings']:.4f} / {cost['savings_pct']:.1f}%)")
    if cost["total"] > 0:
        pct_cr = cost["cache_read"] / cost["total"] * 100
        pct_in = cost["fresh_input"] / cost["total"] * 100
        pct_out = cost["output"] / cost["total"] * 100
        print(f"  Cost Breakdown   : Context cache: ${cost['cache_read']:.4f} ({pct_cr:.1f}%) | "
              f"Fresh input: ${cost['fresh_input']:.4f} ({pct_in:.1f}%) | "
              f"Output: ${cost['output']:.4f} ({pct_out:.1f}%)")
    print(f"  Overall Cache Hit: {tokens['hit_rate_pct']:.1f}%")
    print(f"  Fresh Input      : {tokens['fresh_input']:,} tokens")
    print(f"  Cache Read       : {tokens['cache_read']:,} tokens")
    print(f"  Output Generated : {tokens['output']:,} tokens (incl. {tokens['reasoning']:,} reasoning / {tokens['reasoning_pct']:.1f}%)")
    print(f"  Total Tokens     : {tokens['total']:,}")

    print("\n" + "-" * 68)
    print("2. CACHE HEALTH & RATE-LIMIT VELOCITY")
    print("-" * 68)
    velocity = data.get("velocity", {})
    if velocity.get("peak_tpm_60s"):
        print(f"  Peak 60s Velocity: {velocity['peak_tpm_60s']:,} tokens/min (at Turn {velocity['peak_tpm_turn']}) | "
              f"Peak RPM: {velocity['peak_rpm_60s']} turns/min")

    if velocity.get("api_errors"):
        api_errs = velocity["api_errors"] if verbose else velocity["api_errors"][:8]
        print(f"  API Rate Errors  : [FAIL] {len(velocity['api_errors'])} provider error(s) detected:")
        for err in api_errs:
            print(f"                     - Turn {err['turn']}: {err['snippet']} "
                  f"(rolling 60s was {err['rolling_tpm']:,} tokens across {err['rolling_rpm']} turns)")
        if len(velocity["api_errors"]) > len(api_errs):
            print(f"                     ... and {len(velocity['api_errors']) - len(api_errs)} more provider errors (use --verbose to view all)")

    if cache["durable_custom_nodes"] == 0:
        print("  Graph Pollution  : [PASS] 0 durable custom nodes on active chain.")
    else:
        cns = cache["custom_nodes_details"] if verbose else cache["custom_nodes_details"][:5]
        print(f"  Graph Pollution  : [FAIL] {cache['durable_custom_nodes']} custom nodes found!")
        for cn in cns:
            print(f"                     - type='{cn['customType']}', id={cn['id']}")
        if len(cache["custom_nodes_details"]) > len(cns):
            print(f"                     ... and {len(cache['custom_nodes_details']) - len(cns)} more (use --verbose to view all)")

    if not cache.get("cache_busts") and not cache["idle_expirations"] and not cache.get("model_switches"):
        print("  Prefix Integrity : [PASS] No unexpected cache drops detected.")
    else:
        if cache.get("prefix_busts"):
            pb_list = cache["prefix_busts"] if verbose else cache["prefix_busts"][:5]
            print(f"  Prefix Busts     : [CRITICAL] {len(cache['prefix_busts'])} full invalidation events (<5m gap, graph polluted)!")
            for b in pb_list:
                print(f"                     - Turn {b['turn']}: {b['total_ctx']:,} ctx dropped to 0 cache read ({b['time_gap_sec']:.1f}s gap, cost ${b.get('cost', 0):.4f})")
            if len(cache["prefix_busts"]) > len(pb_list):
                print(f"                     ... and {len(cache['prefix_busts']) - len(pb_list)} more prefix busts (use --verbose to view all)")
        if cache.get("server_evictions"):
            se_list = cache["server_evictions"] if verbose else cache["server_evictions"][:5]
            print(f"  Server Evictions : [WARN] {len(cache['server_evictions'])} cache drops on clean prefix (<5m gap, provider eviction/routing miss)")
            for ev in se_list:
                print(f"                     - Turn {ev['turn']}: {ev['total_ctx']:,} ctx dropped to 0 cache read ({ev['time_gap_sec']:.1f}s gap, cost ${ev.get('cost', 0):.4f})")
            if len(cache["server_evictions"]) > len(se_list):
                print(f"                     ... and {len(cache['server_evictions']) - len(se_list)} more server evictions (use --verbose to view all)")
        elif cache.get("cache_busts") and not cache.get("prefix_busts") and not cache.get("server_evictions"):
            cb_list = cache["cache_busts"] if verbose else cache["cache_busts"][:5]
            print(f"  Prefix Busts     : [WARN] {len(cache['cache_busts'])} cache invalidation events (<5m gap)!")
            for b in cb_list:
                print(f"                     - Turn {b['turn']}: {b['total_ctx']:,} ctx dropped to 0 cache read ({b['time_gap_sec']:.1f}s gap)")
            if len(cache["cache_busts"]) > len(cb_list):
                print(f"                     ... and {len(cache['cache_busts']) - len(cb_list)} more invalidation events (use --verbose to view all)")
        if cache.get("model_switches"):
            ms_list = cache["model_switches"] if verbose else cache["model_switches"][:5]
            print(f"  Model Switches   : [INFO] {len(cache['model_switches'])} cache re-seeds due to in-flight model switches")
            for ms in ms_list:
                print(f"                     - Turn {ms['turn']}: switched '{ms.get('from_model')}' -> '{ms.get('to_model')}' "
                      f"(re-seeded {ms.get('fresh_input', 0):,} tokens, cost ${ms.get('cost', 0):.4f})")
            if len(cache["model_switches"]) > len(ms_list):
                print(f"                     ... and {len(cache['model_switches']) - len(ms_list)} more model switches (use --verbose to view all)")
        if cache["idle_expirations"]:
            ie_list = cache["idle_expirations"] if verbose else cache["idle_expirations"][:5]
            print(f"  Idle Expirations : [INFO] {len(cache['idle_expirations'])} cache drops due to user idle timeout (>5m)")
            for exp in ie_list:
                print(f"                     - Turn {exp['turn']}: idle for {format_duration(exp['time_gap_sec'])} "
                      f"(re-seeded {exp.get('fresh_input', 0):,} tokens, cost ${exp.get('cost', 0):.4f})")
            if len(cache["idle_expirations"]) > len(ie_list):
                print(f"                     ... and {len(cache['idle_expirations']) - len(ie_list)} more idle expirations (use --verbose to view all)")

    print("\n" + "-" * 68)
    print("3. TOOL EXECUTION & EFFICIENCY")
    print("-" * 68)
    tool_summary = ", ".join(f"{name}: {cnt}" for name, cnt in sorted(tools["by_name"].items(), key=lambda x: -x[1]))
    print(f"  Calls Breakdown  : {tool_summary or 'None'}")
    err_rate_display = f"{tools['error_count']} ({tools['error_rate_pct']:.1f}%)"
    print(f"  Tool Error Rate  : {err_rate_display}")
    if tools["errors"]:
        print("  Failed Calls     :")
        tool_errs = tools["errors"] if verbose else tools["errors"][:4]
        for err in tool_errs:
            print(f"    * [{err['tool']}] {err['snippet']}")
        if len(tools["errors"]) > len(tool_errs):
            print(f"    ... and {len(tools['errors']) - len(tool_errs)} more tool failures (use --verbose to view all)")

    print("\n" + "-" * 68)
    print("4. TOP CONTEXT SPIKES (Largest Fresh Inputs)")
    print("-" * 68)
    print(f"  {'Turn':<6} {'Fresh Input':<14} {'Total Ctx':<14} {'Cost':<10} {'Caused By'}")
    spikes_to_show = data["top_spikes"] if verbose else data["top_spikes"][:5]
    for spike in spikes_to_show:
        print(f"  {spike['turn']:<6} {spike['fresh_input']:<14,} {spike['total_ctx']:<14,} ${spike['cost']:<9.4f} {spike['trigger']}")
    if len(data["top_spikes"]) > len(spikes_to_show):
        print(f"  ... and {len(data['top_spikes']) - len(spikes_to_show)} more context spikes omitted (use --verbose to view all)")

    print("\n" + "-" * 68)
    print("5. USER HABITS & INTERACTION PACING")
    print("-" * 68)
    print(f"  User Prompts     : {data['user_prompts_count']}")
    print(f"  Autonomy Ratio   : {data['autonomy_ratio']:.1f} assistant turns per user prompt")
    print(f"  Avg Prompt Size  : {habits['avg_prompt_len']:.0f} characters")
    if habits["think_times_count"] > 0:
        print(f"  Review/Think Time: median {habits['median_think_sec']:.1f}s | avg {habits['avg_think_sec']:.1f}s | max {format_duration(habits['max_think_sec'])}")
    print("=" * 68 + "\n")


def print_session_timeline(data: Dict[str, Any], verbose: bool = False) -> None:
    """Render a chronological turn-by-turn stream for a single session."""
    meta = data["meta"]
    cost = data["cost"]
    velocity = data.get("velocity", {})
    turns = data.get("assistant_turns", [])
    idle_turns = {e["turn"]: e for e in data.get("cache_health", {}).get("idle_expirations", [])}
    evict_turns = {e["turn"]: e for e in data.get("cache_health", {}).get("server_evictions", [])}
    switch_turns = {e["turn"]: e for e in data.get("cache_health", {}).get("model_switches", [])}

    print("\n" + "=" * 80)
    print("                      PI SESSION CHRONOLOGICAL TIMELINE")
    print("=" * 80)
    started_str = data["first_ts"].strftime("%Y-%m-%d %H:%M:%S %Z") if data.get("first_ts") else "Unknown"
    dur_str = format_duration(data.get("wall_duration_sec", 0))
    print(f"Session ID  : {meta.get('id', 'Unknown')}")
    print(f"Workspace   : {meta.get('cwd', 'Unknown')}")
    print(f"Started     : {started_str} ({dur_str} elapsed)")
    models_str = ", ".join(f"{m} ({c} turns)" for m, c in data.get("models", {}).items())
    print(f"Model(s)    : {models_str or 'None'}")
    print(f"Actual Cost : ${cost['total']:.4f} (Saved ${cost.get('savings', 0):.4f} / {cost.get('savings_pct', 0):.1f}% vs ${cost.get('without_cache', 0):.4f} w/o cache)")
    if velocity.get("peak_tpm_60s"):
        print(f"Peak 60s TPM: {velocity['peak_tpm_60s']:,} tokens/min (at Turn {velocity['peak_tpm_turn']}) | Peak RPM: {velocity.get('peak_rpm_60s', 0)} turns/min")

    api_errors = velocity.get("api_errors", [])
    if api_errors:
        print(f"\nAPI RATE ERRORS DETECTED ({len(api_errors)}):")
        for err in (api_errors if verbose else api_errors[:6]):
            err_ts_str = err['ts'].strftime('%H:%M:%S') if err.get('ts') else '??'
            print(f"  * Turn {err['turn']} ({err_ts_str}): {err['snippet']} (rolling 60s: {err['rolling_tpm']:,} tokens across {err['rolling_rpm']} turns)")
        if len(api_errors) > 6 and not verbose:
            print(f"    ... and {len(api_errors) - 6} more rate errors omitted (use --verbose to view all)")

    print("\n" + "-" * 80)
    print(f"{'Time':<10} {'Turn':<6} {'Model':<18} {'Fresh Inp':<11} {'Total Ctx':<11} {'Rolling TPM':<13} {'Note / Tools'}")
    print("-" * 80)

    if verbose or len(turns) <= 40:
        sample_turns = turns
    else:
        # Intelligently sample: first 10, last 10, and all turns with errors, spikes, or cache drops
        key_turns = set()
        for i in range(min(10, len(turns))):
            key_turns.add(turns[i]["turn"])
        for i in range(max(0, len(turns) - 10), len(turns)):
            key_turns.add(turns[i]["turn"])
        for t in turns:
            if t.get("error_msg") or t["turn"] in idle_turns or t["turn"] in evict_turns or t["turn"] in switch_turns or t["fresh_input"] >= 25000:
                key_turns.add(t["turn"])
        sample_turns = [t for t in turns if t["turn"] in key_turns]

    for t in sample_turns:
        t_num = t["turn"]
        note = ""
        if t.get("error_msg"):
            note = "[FAIL] 429 RATE LIMIT" if "429" in str(t["error_msg"]) else "[FAIL] API ERROR"
        elif t_num in idle_turns:
            note = f"[IDLE TTL] +{t['fresh_input']//1000}k fresh"
        elif t_num in evict_turns:
            note = "[SERVER EVICT] dropped"
        elif t_num in switch_turns:
            note = f"MODEL SWITCH: {t['model']}"
        elif t["fresh_input"] >= 25000:
            note = f"SPIKE: +{t['fresh_input']//1000}k fresh"
        elif t.get("tools"):
            note = f"`{','.join(t['tools'][:2])}`"
        else:
            note = t.get("trigger", "")

        ts_str = t["ts"].strftime("%H:%M:%S") if t.get("ts") else "--:--:--"
        print(f"{ts_str:<10} #{t_num:<5} {t['model'][:17]:<18} {t['fresh_input']:<11,} {t['total_ctx']:<11,} {t['rolling_tpm']:<13,} {note}")

    if len(sample_turns) < len(turns):
        print(f"... and {len(turns) - len(sample_turns)} ordinary turns omitted (use --verbose to view all {len(turns)} turns)")
    print("=" * 80 + "\n")


def analyze_time_window_micro(
    since_dt: Optional[datetime],
    until_dt: Optional[datetime],
    label: str,
    as_json: bool = False,
    workspace: Optional[str] = None,
    verbose: bool = False,
) -> None:
    """Delve into a specific short time window or incident timeline across sessions."""
    if not since_dt and not until_dt:
        print("Error: Time-window delve requires a bounded time range (e.g. --today, --since 'YYYY-MM-DD').")
        print("To audit a specific session timeline, provide the session ID or rank: analyze.py <session> --timeline")
        sys.exit(1)

    sessions_dir = os.path.expanduser("~/.pi/agent/sessions")
    pattern = os.path.join(sessions_dir, "*", "*.jsonl")
    files = glob.glob(pattern)

    model_rates = load_models_store()
    turns: List[Dict[str, Any]] = []
    user_prompts: List[Dict[str, Any]] = []
    sessions_seen = set()

    for f in files:
        if since_dt:
            mtime = datetime.fromtimestamp(os.path.getmtime(f), tz=timezone.utc).astimezone()
            if mtime < since_dt:
                continue

        try:
            with open(f, "r", encoding="utf-8") as fp:
                current_session_cwd = None
                current_session_id = None
                prev_turn_model = None

                for line in fp:
                    try:
                        entry = json.loads(line)
                    except Exception:
                        continue

                    etype = entry.get("type")
                    if etype == "session":
                        current_session_cwd = entry.get("cwd")
                        current_session_id = entry.get("id")

                    if workspace and current_session_cwd and workspace not in current_session_cwd:
                        continue

                    ts = parse_timestamp(entry.get("timestamp"))
                    if not ts:
                        continue

                    if since_dt and ts < since_dt:
                        continue
                    if until_dt and ts > until_dt:
                        continue

                    if etype == "message":
                        msg = entry.get("message", {})
                        role = msg.get("role")

                        if role == "user":
                            sessions_seen.add(current_session_id or f)
                            user_prompts.append({
                                "ts": ts,
                                "session_id": current_session_id,
                                "cwd": current_session_cwd,
                                "len": len(str(msg.get("content", ""))),
                            })

                        elif role == "assistant":
                            sessions_seen.add(current_session_id or f)
                            m = msg.get("model") or "unknown"
                            u = msg.get("usage", {})
                            c = u.get("cost", {})
                            inp = u.get("input", 0)
                            cr = u.get("cacheRead", 0)
                            out = u.get("output", 0)
                            reas = u.get("reasoning", 0)
                            tot = inp + cr
                            cost = c.get("total", 0.0)

                            if inp > 0 and c.get("input", 0.0) > 0:
                                model_rates[m] = c["input"] / inp

                            # Identify tools
                            tool_calls = []
                            content = msg.get("content", [])
                            if isinstance(content, list):
                                for item in content:
                                    if isinstance(item, dict) and item.get("type") == "toolCall":
                                        tool_calls.append(item.get("name", "unknown"))

                            turns.append({
                                "ts": ts,
                                "session_id": current_session_id,
                                "cwd": current_session_cwd,
                                "model": m,
                                "prev_model": prev_turn_model,
                                "inp": inp,
                                "cr": cr,
                                "out": out,
                                "reas": reas,
                                "total_ctx": tot,
                                "cost": cost,
                                "cost_output": c.get("output", 0.0),
                                "tools": tool_calls,
                                "error": msg.get("errorMessage") if msg.get("stopReason") == "error" else None,
                            })
                            prev_turn_model = m
        except Exception:
            continue

    turns.sort(key=lambda x: x["ts"])
    user_prompts.sort(key=lambda x: x["ts"])

    if not turns and not user_prompts:
        print(f"\nNo activity found for window: {label}")
        return

    # Calculate rolling 60s throughput velocity
    peak_tpm = 0
    peak_rpm = 0
    peak_turn_idx = 1
    total_cost = 0.0
    cost_without_cache = 0.0
    fresh_input_total = 0
    cache_read_total = 0
    output_total = 0
    models_counter = Counter()
    api_errors = []

    for i, t in enumerate(turns):
        t["turn_idx"] = i + 1
        w_start = t["ts"].timestamp() - 60.0
        w_tokens = 0
        w_count = 0
        for prev in turns[:i]:
            if prev["ts"].timestamp() >= w_start:
                w_tokens += prev["total_ctx"]
                w_count += 1
        t["rolling_tpm"] = w_tokens
        t["rolling_rpm"] = w_count

        if w_tokens > peak_tpm:
            peak_tpm = w_tokens
            peak_turn_idx = t["turn_idx"]
        if w_count > peak_rpm:
            peak_rpm = w_count

        m = t["model"]
        models_counter[m] += 1
        total_cost += t["cost"]
        fresh_input_total += t["inp"]
        cache_read_total += t["cr"]
        output_total += t["out"]

        rate = model_rates.get(m, 0.0)
        cost_without_cache += (t["total_ctx"] * rate) + t["cost_output"]

        if t["error"]:
            err_raw = str(t["error"])
            if "429" in err_raw:
                err_snip = "429 Too Many Requests (Rate Limit)"
            elif "503" in err_raw:
                err_snip = "503 Service Unavailable"
            elif "500" in err_raw:
                err_snip = "500 Internal Server Error"
            elif "401" in err_raw:
                err_snip = "401 Unauthorized"
            else:
                err_snip = err_raw[:100]
            api_errors.append({
                "turn": t["turn_idx"],
                "ts": t["ts"],
                "rolling_tpm": w_tokens,
                "rolling_rpm": w_count,
                "snippet": err_snip,
            })

    if cost_without_cache < total_cost:
        cost_without_cache = total_cost
    savings = cost_without_cache - total_cost
    savings_pct = (savings / cost_without_cache * 100) if cost_without_cache > 0 else 0.0
    tot_input = fresh_input_total + cache_read_total
    hit_rate = (cache_read_total / tot_input * 100) if tot_input > 0 else 0.0

    first_ctx = turns[0]["total_ctx"] if turns else 0
    last_ctx = turns[-1]["total_ctx"] if turns else 0

    if as_json:
        out_dict = {
            "label": label,
            "since": since_dt.isoformat() if since_dt else None,
            "until": until_dt.isoformat() if until_dt else None,
            "turns_count": len(turns),
            "prompts_count": len(user_prompts),
            "sessions_count": len(sessions_seen),
            "total_cost": total_cost,
            "cost_without_cache": cost_without_cache,
            "savings": savings,
            "savings_pct": savings_pct,
            "peak_tpm_60s": peak_tpm,
            "peak_rpm_60s": peak_rpm,
            "api_errors": api_errors,
        }
        print(json.dumps(out_dict, indent=2, default=str))
        return

    # Terminal report
    print("\n" + "=" * 68)
    print(f"           PI TIME-WINDOW DELVE: {label.upper()}")
    print("=" * 68)
    start_label = since_dt.strftime("%Y-%m-%d %H:%M:%S %Z") if since_dt else "Earliest"
    end_label = until_dt.strftime("%Y-%m-%d %H:%M:%S %Z") if until_dt else "Latest"
    print(f"Window      : {start_label} -> {end_label}")
    print(f"Activity    : {len(turns)} assistant turns, {len(user_prompts)} user prompts across {len(sessions_seen)} session(s)")
    models_str = ", ".join(f"{m} ({c})" for m, c in models_counter.items())
    print(f"Models Used : {models_str}")
    print(f"Cost        : ${total_cost:.4f} (Saved ${savings:.4f} / {savings_pct:.1f}% vs ${cost_without_cache:.4f} w/o cache)")
    print(f"Peak 60s TPM: {peak_tpm:,} tokens/min (at Turn {peak_turn_idx}) | Peak RPM: {peak_rpm} turns/min")

    if api_errors:
        print("\nAPI RATE ERRORS DETECTED:")
        for err in api_errors:
            print(f"  * Turn {err['turn']} ({err['ts'].strftime('%H:%M:%S')}): {err['snippet']} (rolling 60s was {err['rolling_tpm']:,} tokens across {err['rolling_rpm']} turns)")

    print("\n" + "-" * 68)
    print(f"{'Time':<10} {'Turn':<5} {'Model':<18} {'Total Ctx':<12} {'Rolling TPM':<13} {'Note / Tools'}")
    print("-" * 68)

    # If verbose or small window, print all; if large, sample
    sample_turns = turns if (verbose or len(turns) <= 40) else (turns[:15] + turns[len(turns)//2 - 5:len(turns)//2 + 5] + turns[-15:])

    prev_turn_ts = None
    for t in sample_turns:
        note = ""
        if t["error"]:
            note = "[FAIL] " + ("429 RATE LIMIT" if "429" in str(t["error"]) else "API ERROR")
        elif t["prev_model"] and t["prev_model"] != t["model"]:
            note = f"MODEL SWITCH: {t['model']}"
        elif t["inp"] > 25000:
            note = f"SPIKE: +{t['inp']//1000}k fresh"
        elif t["tools"]:
            note = f"`{','.join(t['tools'][:2])}`"

        print(f"{t['ts'].strftime('%H:%M:%S'):<10} {t['turn_idx']:<5} {t['model'][:17]:<18} {t['total_ctx']:<12,} {t['rolling_tpm']:<13,} {note}")

    if len(sample_turns) < len(turns):
        print(f"... and {len(turns) - len(sample_turns)} more turns omitted (use --verbose to view all)")
    print("=" * 68 + "\n")


def analyze_time_window_macro(
    since_dt: Optional[datetime],
    until_dt: Optional[datetime],
    label: str,
    summary: bool = False,
    as_json: bool = False,
    workspace: Optional[str] = None,
) -> None:
    """Aggregate review (Macro mode) over a daily, weekly, monthly, or historical range."""
    sessions_dir = os.path.expanduser("~/.pi/agent/sessions")
    pattern = os.path.join(sessions_dir, "*", "*.jsonl")
    files = glob.glob(pattern)

    if not files:
        print(f"No session logs found in {sessions_dir}")
        return

    total_sessions = set()
    total_prompts = 0
    total_assistant_turns = 0
    total_cost = 0.0
    total_uncached_cost = 0.0
    hourly_histogram = [0] * 24
    hourly_models: Dict[int, Counter] = {h: Counter() for h in range(24)}
    weekday_histogram = [0] * 7  # 0 = Monday, 6 = Sunday
    weekday_models: Dict[int, Counter] = {d: Counter() for d in range(7)}
    model_turns = Counter()
    model_cost = Counter()
    think_times: List[float] = []
    workspaces = Counter()
    api_errors_count = 0
    assistant_window_events: List[Tuple[datetime, int]] = []

    for f in files:
        if since_dt:
            mtime = datetime.fromtimestamp(os.path.getmtime(f), tz=timezone.utc).astimezone()
            if mtime < since_dt:
                continue

        try:
            with open(f, "r", encoding="utf-8") as fp:
                last_assistant_ts = None
                current_cwd = None
                current_sid = None

                for line in fp:
                    try:
                        entry = json.loads(line)
                    except Exception:
                        continue

                    etype = entry.get("type")
                    if etype == "session":
                        current_cwd = entry.get("cwd")
                        current_sid = entry.get("id")

                    if workspace and current_cwd and workspace not in current_cwd:
                        continue

                    ts = parse_timestamp(entry.get("timestamp"))
                    if not ts:
                        continue
                    if since_dt and ts < since_dt:
                        continue
                    if until_dt and ts > until_dt:
                        continue

                    if etype == "message":
                        msg = entry.get("message", {})
                        role = msg.get("role")

                        if role == "user":
                            total_sessions.add(current_sid or f)
                            total_prompts += 1
                            hourly_histogram[ts.hour] += 1
                            weekday_histogram[ts.weekday()] += 1
                            if current_cwd:
                                workspaces[current_cwd] += 1

                            if last_assistant_ts and ts:
                                delta = (ts - last_assistant_ts).total_seconds()
                                if delta >= 0:
                                    think_times.append(delta)

                        elif role == "assistant":
                            total_sessions.add(current_sid or f)
                            total_assistant_turns += 1
                            last_assistant_ts = ts
                            m = msg.get("model") or "unknown"
                            u = msg.get("usage", {})
                            c = u.get("cost", {})
                            inp = u.get("input", 0)
                            cr = u.get("cacheRead", 0)
                            cin = c.get("input", 0.0)
                            ctot = c.get("total", 0.0)
                            total_cost += ctot
                            model_turns[m] += 1
                            model_cost[m] += ctot
                            hourly_models[ts.hour][m] += 1
                            weekday_models[ts.weekday()][m] += 1
                            tot_ctx = inp + cr
                            assistant_window_events.append((ts, tot_ctx))

                            if msg.get("stopReason") == "error":
                                api_errors_count += 1

                            if inp > 0 and cin > 0:
                                rate = cin / inp
                                total_uncached_cost += ((inp + cr) * rate) + c.get("output", 0.0)
                            else:
                                total_uncached_cost += ctot
        except Exception:
            continue

    if total_assistant_turns == 0 and total_prompts == 0:
        print(f"\nNo session activity found for period: {label}")
        return

    # Calculate peak 60s throughput in this macro period
    assistant_window_events.sort(key=lambda x: x[0])
    peak_tpm_macro = 0
    peak_rpm_macro = 0
    for i, (ts, tot_ctx) in enumerate(assistant_window_events):
        w_start = ts.timestamp() - 60.0
        w_tokens = 0
        w_count = 0
        for prev_ts, prev_ctx in assistant_window_events[:i]:
            if prev_ts.timestamp() >= w_start:
                w_tokens += prev_ctx
                w_count += 1
        if w_tokens > peak_tpm_macro:
            peak_tpm_macro = w_tokens
        if w_count > peak_rpm_macro:
            peak_rpm_macro = w_count

    savings_hist = total_uncached_cost - total_cost
    savings_hist_pct = (savings_hist / total_uncached_cost * 100) if total_uncached_cost > 0 else 0.0

    if as_json:
        out_dict = {
            "label": label,
            "sessions_count": len(total_sessions),
            "prompts_count": total_prompts,
            "assistant_turns": total_assistant_turns,
            "total_cost": total_cost,
            "total_uncached_cost": total_uncached_cost,
            "savings": savings_hist,
            "savings_pct": savings_hist_pct,
            "models": dict(model_turns),
            "model_costs": dict(model_cost),
            "peak_tpm_60s": peak_tpm_macro,
            "peak_rpm_60s": peak_rpm_macro,
            "api_errors_count": api_errors_count,
        }
        print(json.dumps(out_dict, indent=2, default=str))
        return

    print("\n" + "=" * 68)
    print(f"             PI MACRO REVIEW: {label.upper()}")
    print("=" * 68)
    print(f"Scope                : {label} ({len(total_sessions)} sessions analyzed)")
    print(f"Total User Prompts   : {total_prompts:,}")
    print(f"Total Assistant Turns: {total_assistant_turns:,}")
    print(f"Total Model Cost     : ${total_cost:,.2f} (Saved ${savings_hist:,.2f} / {savings_hist_pct:.1f}% vs ${total_uncached_cost:,.2f} w/o cache)")
    if total_prompts:
        print(f"Autonomy Ratio       : {total_assistant_turns / total_prompts:.1f} agent turns per user prompt")
    print(f"Peak 60s Throughput  : {peak_tpm_macro:,} tokens/min | Peak RPM: {peak_rpm_macro} turns/min")
    if api_errors_count > 0:
        print(f"API Rate Errors      : {api_errors_count} error event(s) recorded")

    top_models = [m for m, _ in model_turns.most_common(4)]
    symbols = ["#", "=", "*", "+"]
    model_sym = {m: s for m, s in zip(top_models, symbols)}

    print("\n" + "-" * 68)
    print("MODEL USAGE & SPEND BREAKDOWN")
    print("-" * 68)
    max_m_cnt = model_turns.most_common(1)[0][1] if model_turns else 1
    for m, cnt in model_turns.most_common(6):
        pct_t = cnt / total_assistant_turns * 100 if total_assistant_turns else 0.0
        pct_c = model_cost[m] / total_cost * 100 if total_cost else 0.0
        bar = "#" * int((cnt / max_m_cnt) * 20)
        sym = f"[{model_sym.get(m, '.')}] " if m in model_sym else "    "
        print(f"  {sym}{m:<24} | {cnt:<6,} turns ({pct_t:>4.1f}%) | ${model_cost[m]:<7.2f} ({pct_c:>4.1f}%) | {bar}")

    print("\n" + "-" * 68)
    print("HOURLY ACTIVITY DISTRIBUTION (Local Time)")
    print("-" * 68)
    legend_parts = [f"[{model_sym[m]}] {m}" for m in top_models] + ["[.] other"]
    print("Legend:", "  ".join(legend_parts))
    print("." * 68)
    max_h = max(hourly_histogram) if max(hourly_histogram) > 0 else 1
    for h in range(24):
        tot_p = hourly_histogram[h]
        bar_len = int((tot_p / max_h) * 35) if max_h > 0 else 0
        tot_h_models = sum(hourly_models[h].values())
        if bar_len == 0 or tot_h_models == 0:
            bar = "#" * bar_len
        else:
            bar = ""
            for m in top_models:
                cnt = hourly_models[h][m]
                seg_len = int(round((cnt / tot_h_models) * bar_len))
                bar += model_sym[m] * seg_len
            if len(bar) < bar_len:
                bar += "." * (bar_len - len(bar))
            elif len(bar) > bar_len:
                bar = bar[:bar_len]
        print(f"  {h:02d}:00 | {tot_p:<5} | {bar}")

    print("\n" + "-" * 68)
    print("WEEKDAY ACTIVITY DISTRIBUTION")
    print("-" * 68)
    days_names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    max_d = max(weekday_histogram) if max(weekday_histogram) > 0 else 1
    for d_idx, d_name in enumerate(days_names):
        tot_p = weekday_histogram[d_idx]
        bar_len = int((tot_p / max_d) * 35) if max_d > 0 else 0
        tot_d_models = sum(weekday_models[d_idx].values())
        if bar_len == 0 or tot_d_models == 0:
            bar = "#" * bar_len
        else:
            bar = ""
            for m in top_models:
                cnt = weekday_models[d_idx][m]
                seg_len = int(round((cnt / tot_d_models) * bar_len))
                bar += model_sym[m] * seg_len
            if len(bar) < bar_len:
                bar += "." * (bar_len - len(bar))
            elif len(bar) > bar_len:
                bar = bar[:bar_len]
        print(f"  {d_name}   | {tot_p:<5} | {bar}")

    if think_times:
        print("\n" + "-" * 68)
        print("USER REVIEW & THINK-TIME PACING")
        print("-" * 68)
        fast = sum(1 for t in think_times if t < 30)
        norm = sum(1 for t in think_times if 30 <= t <= 180)
        deep = sum(1 for t in think_times if t > 180)
        total_tt = len(think_times)
        sorted_tt = sorted(think_times)
        median_tt = sorted_tt[total_tt // 2]
        avg_tt = sum(think_times) / total_tt

        print(f"  Median Think Time : {median_tt:.1f}s  (Average: {avg_tt:.1f}s)")
        print(f"  Fast Steering (<30s)   : {fast:<5} ({fast / total_tt * 100:.1f}%)")
        print(f"  Normal Review (30s-3m) : {norm:<5} ({norm / total_tt * 100:.1f}%)")
        print(f"  Deep Work/Pause (>3m)  : {deep:<5} ({deep / total_tt * 100:.1f}%)")

    print("\n" + "-" * 68)
    print("TOP WORKSPACES")
    print("-" * 68)
    for ws, count in workspaces.most_common(5):
        print(f"  - {ws}: {count} prompts")

    print("=" * 68 + "\n")


def main() -> None:
    parser = argparse.ArgumentParser(description="Pi Session Analytics & Audit Tool")
    parser.add_argument("path", nargs="?", default=None, help="Path to a session .jsonl file, UUID prefix, or recency rank (e.g. 1, 2)")
    parser.add_argument("-l", "--list", dest="list_sessions", action="store_true", help="List recent sessions across workspaces in date order")
    parser.add_argument("--workspaces", dest="list_workspaces", action="store_true", help="List all workspaces with session counts and latest activity")
    parser.add_argument("--limit", type=int, default=15, help="Max sessions to display with --list (default: 15, 0 for all)")
    parser.add_argument("--errors-only", action="store_true", help="Filter --list to sessions that encountered errors")
    parser.add_argument("-n", "--recent", dest="recent_rank", type=int, default=None, help="Audit the N-th most recent session (1 = latest, 2 = 2nd latest)")
    parser.add_argument("--latest", action="store_true", help="Audit the latest session in the current workspace")
    parser.add_argument("-g", "--global", dest="global_search", action="store_true", help="Audit the latest session across all workspaces globally")
    parser.add_argument("--timeline", "--delve", dest="timeline", action="store_true", help="Force chronological turn timeline (Micro mode)")
    parser.add_argument("--today", action="store_true", help="Audit activity from today (midnight to now)")
    parser.add_argument("--yesterday", action="store_true", help="Audit activity from yesterday (full 24h)")
    parser.add_argument("--this-week", dest="this_week", action="store_true", help="Audit activity from this week (Monday to now)")
    parser.add_argument("--last-week", dest="last_week", action="store_true", help="Audit activity from last week (Monday to Sunday)")
    parser.add_argument("--this-month", dest="this_month", action="store_true", help="Audit activity from this calendar month")
    parser.add_argument("--last-month", dest="last_month", action="store_true", help="Audit activity from last calendar month")
    parser.add_argument("--days", type=int, default=None, help="Audit activity from the last N days")
    parser.add_argument("--since", type=str, default=None, help="Start datetime: 'YYYY-MM-DD' or 'YYYY-MM-DD HH:MM'")
    parser.add_argument("--until", type=str, default=None, help="End datetime: 'YYYY-MM-DD' or 'YYYY-MM-DD HH:MM'")
    parser.add_argument("--workspace", type=str, default=None, help="Filter to specific workspace directory path")
    parser.add_argument("--habits", action="store_true", help="Audit all historical habits across all sessions")
    parser.add_argument("--json", dest="as_json", action="store_true", help="Output raw JSON metrics")
    parser.add_argument("--verbose", action="store_true", help="Include verbose turn-by-turn breakdown")

    args = parser.parse_args()

    # Workspace index overview
    if args.list_workspaces:
        list_workspaces(as_json=args.as_json)
        return

    # Chronological session listing
    if args.list_sessions:
        since_dt = parse_user_datetime(args.since, is_end=False) if args.since else None
        until_dt = parse_user_datetime(args.until, is_end=True) if args.until else None
        now = datetime.now().astimezone()
        if args.today:
            since_dt = datetime.combine(now.date(), time.min, tzinfo=now.tzinfo)
            until_dt = now
        elif args.yesterday:
            y_date = now.date() - timedelta(days=1)
            since_dt = datetime.combine(y_date, time.min, tzinfo=now.tzinfo)
            until_dt = datetime.combine(y_date, time.max, tzinfo=now.tzinfo)
        elif args.days:
            since_dt = now - timedelta(days=args.days)
            until_dt = now

        list_sessions(
            limit=args.limit,
            workspace=args.workspace,
            errors_only=args.errors_only,
            since_dt=since_dt,
            until_dt=until_dt,
            as_json=args.as_json,
        )
        return

    # Historical habits
    if args.habits:
        analyze_time_window_macro(
            since_dt=None,
            until_dt=None,
            label="All Time (Habits)",
            as_json=args.as_json,
            workspace=args.workspace,
        )
        return

    # Time-window analytics
    has_time_filter = any([
        args.today, args.yesterday, args.this_week, args.last_week,
        args.this_month, args.last_month, args.days, args.since, args.until,
    ])

    if has_time_filter:
        since_dt, until_dt, label, is_micro = resolve_time_filters(args)
        if is_micro:
            analyze_time_window_micro(
                since_dt=since_dt,
                until_dt=until_dt,
                label=label,
                as_json=args.as_json,
                workspace=args.workspace,
                verbose=args.verbose,
            )
        else:
            analyze_time_window_macro(
                since_dt=since_dt,
                until_dt=until_dt,
                label=label,
                as_json=args.as_json,
                workspace=args.workspace,
            )
        return

    # Single session target resolution (path, rank, UUID prefix, or latest)
    target_path = resolve_session_target(
        target=args.path,
        cwd=os.getcwd(),
        global_search=args.global_search,
        recent_rank=args.recent_rank,
    )

    if not target_path:
        print("Error: No session file specified and could not resolve target session.")
        print("Provide a path, session ID prefix, rank number, or run with --list / --latest.")
        sys.exit(1)

    if not os.path.isfile(target_path):
        print(f"Error: File not found: {target_path}")
        sys.exit(1)

    branch, total_raw, meta = load_active_branch(target_path)
    if not branch:
        print(f"Error: No valid entries found in {target_path}")
        sys.exit(1)

    metrics = analyze_session(branch, total_raw, meta)

    if args.as_json:
        # Convert non-serializable objects
        metrics["first_ts"] = metrics["first_ts"].isoformat() if metrics.get("first_ts") else None
        metrics["last_ts"] = metrics["last_ts"].isoformat() if metrics.get("last_ts") else None
        if args.verbose or args.timeline:
            full_turns = []
            for t in metrics.get("assistant_turns", []):
                t_copy = dict(t)
                if t_copy.get("ts") and hasattr(t_copy["ts"], "isoformat"):
                    t_copy["ts"] = t_copy["ts"].isoformat()
                full_turns.append(t_copy)
            metrics["turns"] = full_turns
        metrics.pop("assistant_turns", None)
        print(json.dumps(metrics, indent=2, default=str))
    elif args.timeline:
        print_session_timeline(metrics, verbose=args.verbose)
    else:
        print_session_report(metrics, verbose=args.verbose)


if __name__ == "__main__":
    main()
