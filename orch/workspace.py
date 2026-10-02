"""Workspace resolution (SPEC-0.3 section 2). Python 3.10 stdlib only."""
import hashlib
import os
import re

ENV_OVERRIDE = "SWITCHYARD_WORKSPACE"


def _clean(path):
    if not isinstance(path, (str, os.PathLike)):
        raise ValueError("path must be a string")
    p = os.fspath(path)
    if not isinstance(p, str):
        raise ValueError("path must be a string")
    p = p.strip().strip('"')
    if not p or "\x00" in p:
        raise ValueError("empty or invalid path")
    return p


def _canon(path):
    """Absolute, '..'-resolved, symlink/junction-resolved path (works for non-existent tails)."""
    return os.path.realpath(os.path.abspath(path))


def _key(path):
    return os.path.normcase(_canon(path))


def _is_fs_root(p):
    """True for '/', 'C:\\', '\\\\server\\share\\' (no components below the anchor)."""
    drive, tail = os.path.splitdrive(p)
    return tail.strip("\\/") == ""


def _home(env):
    h = env.get("USERPROFILE") or env.get("HOME") or os.path.expanduser("~")
    return h if h and h != "~" else None


def slug(name):
    s = re.sub(r"[^a-z0-9]+", "-", str(name).lower()).strip("-")
    return (s[:40].strip("-")) or "ws"


def resolve(path, env=os.environ):
    """Return {"id","root","name","subdir"} for `path`. Raises ValueError if refused."""
    override = env.get(ENV_OVERRIDE) if env is not None else None
    start = _canon(_clean(override if override and override.strip() else path))

    if os.path.isfile(start):
        start = os.path.dirname(start)
    elif not os.path.exists(start) and not os.path.isdir(os.path.dirname(start) or start):
        raise ValueError("path does not exist and neither does its parent: %s" % start)

    root = None
    cur = start
    home_key = _key(_home(env if env is not None else os.environ) or "")
    while True:
        # a .git at the home dir or a drive root (dotfile repos) is not a project: ignore it and keep the folder itself
        if os.path.exists(os.path.join(cur, ".git")) and not _is_fs_root(cur) and _key(cur) != home_key:
            root = cur  # dir or file (worktree/submodule)
            break
        parent = os.path.dirname(cur)
        if parent == cur:
            break
        cur = parent
    if root is None:
        root = start

    if _is_fs_root(root):
        raise ValueError("refusing drive/filesystem root as workspace: %s" % root)
    home = _home(env if env is not None else os.environ)
    if home and _key(root) == _key(home):
        raise ValueError("refusing home directory as workspace: %s" % root)

    name = os.path.basename(root.rstrip("\\/")) or "ws"
    digest = hashlib.sha1(os.path.normcase(root).encode("utf-8", "surrogatepass")).hexdigest()[:8]
    rel = os.path.relpath(start, root)
    subdir = "" if rel == "." else rel.replace("\\", "/")
    return {"id": "%s-%s" % (slug(name), digest), "root": root, "name": name, "subdir": subdir}


def contains(root, path):
    """True if `path` is `root` or inside it (component-wise, case-insensitive on Windows)."""
    try:
        r = _key(_clean(root))
        p = _key(_clean(path))
        return os.path.commonpath([r, p]) == r
    except (ValueError, TypeError):  # different drives, empty, bad type
        return False
