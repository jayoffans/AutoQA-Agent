"""Inspect working tree and all reachable Git blobs; print locations, never secret values."""
import json
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PATTERN = re.compile(rb"(?:sk-[A-Za-z0-9_-]{20,}|gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,})")


def git(*args):
    return subprocess.check_output(["git", *args], cwd=ROOT)


def audit():
    known = []
    envfile = ROOT / ".env"
    if envfile.exists():
        for line in envfile.read_text(encoding="utf-8-sig").splitlines():
            key, separator, value = line.partition("=")
            if separator and re.search(r"KEY|SECRET|TOKEN|PASSWORD", key):
                value = value.strip().strip("\"'")
                if len(value) >= 8:
                    known.append(value.encode())
    leaked = []
    def check(data, location):
        if PATTERN.search(data) or any(secret in data for secret in known):
            leaked.append(location)
    tracked = git("ls-files", "-z").decode().split("\0")
    forbidden = [p for p in tracked if p and (Path(p).name == ".env" or p.endswith((".db", ".sqlite", ".sqlite3", ".log")))]
    for relative in filter(None, tracked):
        path = ROOT / relative
        if path.is_file():
            check(path.read_bytes(), "working:" + relative)
    objects = git("rev-list", "--objects", "--all").decode().splitlines()
    checked = set()
    for entry in objects:
        object_id, _, path = entry.partition(" ")
        if object_id in checked:
            continue
        checked.add(object_id)
        if git("cat-file", "-t", object_id).strip() == b"blob":
            check(git("cat-file", "blob", object_id), "history:" + path)
    result = {"known_local_secret_match_or_token_pattern": sorted(set(leaked)),
              "forbidden_tracked_files": forbidden, "reachable_objects_checked": len(checked),
              "scope": "Current tracked files and all reachable history; known local secrets and token patterns."
                       " Cannot prove absence of every unknown credential or removed remote history."}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if leaked or forbidden:
        raise SystemExit(1)


if __name__ == "__main__":
    audit()
