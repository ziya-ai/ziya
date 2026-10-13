"""On-demand listing of ``[external]`` directories for the file browser.

External paths can be very large (a home directory, a vendor-docs dump with
a generated HTML manual, thousands of PDFs whose token estimate opens each
file), so the ``[external]`` tree is never walked in full on its own.  A
directory is listed one level at a time, when the user expands it.  A
directory node whose listing is missing or incomplete carries
``_lazy: True``; the frontend shows it with an expand arrow and asks the
server for it (``GET /api/external-dir``).

A full walk, bounded by entry count, depth and time, happens only when the
user asks for a whole subtree: checking a folder whose contents are not all
loaded, or adding a folder straight into context.  A directory that walk
could not finish is left ``_lazy`` rather than shown as empty, so it can
still be expanded.

These are pure functions over the folder-cache node shape
``{'children': {...}, 'token_count': int, '_lazy'?: True}`` (files are
``{'token_count': int}``).  The cache and its lock live in
``app.services.folder_service``.
"""
import os
import time
from typing import Callable, List, Optional, Tuple

LAZY = '_lazy'

DEFAULT_MAX_ENTRIES = 10_000
DEFAULT_DEADLINE_S = 15.0
DEFAULT_MAX_DEPTH = 10

Estimator = Callable[[str], int]
Listable = Callable[[str], bool]


def lazy_dir_node() -> dict:
    """A directory whose contents have not been listed."""
    return {'children': {}, 'token_count': 0, LAZY: True}


def is_dir_node(node) -> bool:
    return isinstance(node, dict) and 'children' in node


def sum_tokens(node: dict) -> int:
    """Token total of a directory node's children (tool-backed -1 counts as 0)."""
    total = 0
    for child in (node.get('children') or {}).values():
        tc = child.get('token_count', 0) if isinstance(child, dict) else 0
        if isinstance(tc, (int, float)) and tc > 0:
            total += int(tc)
    return total


def _inside(real_path: str, real_codebase: str) -> bool:
    return real_path == real_codebase or real_path.startswith(real_codebase + os.sep)


def _entries(dir_path: str, real_codebase: str) -> List[Tuple[str, str, bool]]:
    """Visible entries of ``dir_path`` as ``(name, path, is_dir)``, sorted by name.

    Hidden entries are skipped.  Directories are followed through symlinks;
    files are not (unchanged from the former recursive scan).  A directory
    resolving into the project root is skipped so the project does not
    appear twice.  Raises ``OSError`` if ``dir_path`` cannot be read.
    """
    out: List[Tuple[str, str, bool]] = []
    with os.scandir(dir_path) as it:
        for de in it:
            if de.name.startswith('.'):
                continue
            try:
                is_dir = de.is_dir()
                is_file = (not is_dir) and de.is_file(follow_symlinks=False)
            except OSError:
                continue
            if is_dir:
                try:
                    if _inside(os.path.realpath(de.path), real_codebase):
                        continue
                except (OSError, ValueError):
                    continue
            elif not is_file:
                continue
            out.append((de.name, de.path, is_dir))
    out.sort(key=lambda e: e[0])
    return out


def _file_node(path: str, estimate: Estimator) -> Optional[dict]:
    try:
        return {'token_count': estimate(path)}
    except Exception:
        return None


def list_dir(dir_path: str, real_codebase: str, estimate: Estimator) -> dict:
    """One level of ``dir_path``.  Subdirectories come back lazy.

    Raises ``OSError`` if the directory cannot be read.
    """
    node = {'children': {}, 'token_count': 0}
    for name, path, is_dir in _entries(dir_path, real_codebase):
        if is_dir:
            node['children'][name] = lazy_dir_node()
        else:
            f = _file_node(path, estimate)
            if f is not None:
                node['children'][name] = f
    node['token_count'] = sum_tokens(node)
    return node


def scan_tree(root_path: str, real_codebase: str, estimate: Estimator,
              max_entries: int = DEFAULT_MAX_ENTRIES,
              deadline_s: float = DEFAULT_DEADLINE_S,
              max_depth: int = DEFAULT_MAX_DEPTH) -> Tuple[dict, bool]:
    """Walk ``root_path`` within the limits.  Returns ``(node, complete)``.

    Every directory the walk could not finish (entry budget, deadline, depth
    limit, or a second path to an already-visited directory) is marked lazy,
    and so is each of its ancestors, so the frontend can list it on demand.
    """
    state = {'count': 0, 'complete': True}
    deadline = time.monotonic() + deadline_s
    visited: set = set()

    def walk(path: str, depth: int) -> dict:
        node = {'children': {}, 'token_count': 0}
        try:
            real = os.path.realpath(path)
        except (OSError, ValueError):
            return node
        if real in visited or depth >= max_depth:
            state['complete'] = False
            node[LAZY] = True
            return node
        visited.add(real)
        try:
            entries = _entries(path, real_codebase)
        except OSError:
            return node
        for name, entry_path, is_dir in entries:
            if state['count'] >= max_entries or time.monotonic() > deadline:
                state['complete'] = False
                node[LAZY] = True
                break
            state['count'] += 1
            if is_dir:
                child = walk(entry_path, depth + 1)
                node['children'][name] = child
                if child.get(LAZY):
                    node[LAZY] = True
            else:
                f = _file_node(entry_path, estimate)
                if f is not None:
                    node['children'][name] = f
        node['token_count'] = sum_tokens(node)
        return node

    root = walk(root_path, 0)
    return root, state['complete']


def mark_unverified(node: dict) -> None:
    """Mark ``node`` and every directory below it lazy (to be re-listed when expanded)."""
    stack = [node]
    while stack:
        n = stack.pop()
        if is_dir_node(n):
            n[LAZY] = True
            stack.extend(c for c in n['children'].values() if is_dir_node(c))


def merge_listing(old: Optional[dict], new: dict, distrust_existing: bool = False) -> dict:
    """Combine a fresh one-level listing ``new`` with the directory's cached node ``old``.

    The set of entries comes from ``new``, so deleted files disappear and new
    ones appear.  A subdirectory already present in ``old`` keeps its cached
    contents, so re-listing a folder does not collapse what was expanded
    below it.  With ``distrust_existing`` those kept subdirectories are
    marked lazy: used when a folder is newly added and its cached subtree
    may be placeholders created for some other added path.
    """
    if not is_dir_node(old):
        return new
    merged = {'children': {}, 'token_count': 0}
    for name, child in new['children'].items():
        prev = old['children'].get(name)
        if is_dir_node(child) and is_dir_node(prev):
            if distrust_existing:
                mark_unverified(prev)
            merged['children'][name] = prev
        else:
            merged['children'][name] = child
    merged['token_count'] = sum_tokens(merged)
    return merged


def _parts(abs_path: str) -> List[str]:
    return [p for p in abs_path.split('/') if p]


def get_node(ext_root: dict, abs_path: str) -> Optional[dict]:
    """The node for ``abs_path`` under the ``[external]`` root node, or None."""
    level = ext_root
    for part in _parts(abs_path):
        level = (level.get('children') or {}).get(part) if isinstance(level, dict) else None
        if level is None:
            return None
    return level


def set_node(ext_root: dict, abs_path: str, node: dict,
             listable: Optional[Listable] = None) -> None:
    """Place ``node`` at ``abs_path`` and refresh every ancestor's token total.

    Missing parents are created as placeholders.  A placeholder that is itself
    inside an added external path (``listable``) is marked lazy, since its
    other entries have not been listed.  One outside every added path stays
    non-lazy: it only exists to lead to the added path, and the server will
    not list it.
    """
    parts = _parts(abs_path)
    if not parts:
        return
    chain = [ext_root]
    level = ext_root
    for i, part in enumerate(parts[:-1]):
        children = level.setdefault('children', {})
        nxt = children.get(part)
        if not is_dir_node(nxt):
            nxt = {'children': {}, 'token_count': 0}
            if listable is not None and listable('/' + '/'.join(parts[:i + 1])):
                nxt[LAZY] = True
            children[part] = nxt
        chain.append(nxt)
        level = nxt
    level.setdefault('children', {})[parts[-1]] = node
    for anc in reversed(chain):
        anc['token_count'] = sum_tokens(anc)


def loaded_dir_paths(ext_root: dict) -> List[str]:
    """Absolute paths of directories whose listing is cached (not lazy), parents first."""
    out: List[str] = []

    def walk(node: dict, prefix: str) -> None:
        for name, child in (node.get('children') or {}).items():
            if not is_dir_node(child):
                continue
            path = prefix + '/' + name
            if not child.get(LAZY):
                out.append(path)
            walk(child, path)

    walk(ext_root, '')
    out.sort(key=lambda p: p.count('/'))
    return out
