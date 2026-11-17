#!/usr/bin/env python3
"""Prepare the pinned official frontend with the downstream paper extension."""

import argparse
import hashlib
import json
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OVERLAY = ROOT / "frontend_extensions" / "ashare-paper"


def run(*args, cwd=None):
    return subprocess.run(args, cwd=cwd, check=True)


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def prepare(target):
    manifest = json.loads((OVERLAY / "manifest.json").read_text())
    if not target.exists():
        run("git", "clone", "--no-checkout", "--depth", "1", manifest["upstream"], str(target))
        run("git", "fetch", "--depth", "1", "origin", manifest["commit"], cwd=target)
        run("git", "checkout", "--detach", manifest["commit"], cwd=target)
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=target, text=True).strip()
    if head != manifest["commit"]:
        raise SystemExit("Unexpected upstream revision; review and update the pinned overlay first")
    if digest(target / "LICENSE") != digest(OVERLAY / "UPSTREAM_LICENSE.txt"):
        raise SystemExit("Upstream license differs from the reviewed version")
    for name in ("upstream.patch", "password-profile.patch", "watchlist-names.patch"):
        patch = str(OVERLAY / name)
        if subprocess.run(["git", "apply", "--check", patch], cwd=target, capture_output=True, check=False).returncode == 0:
            run("git", "apply", patch, cwd=target)
        elif subprocess.run(["git", "apply", "--reverse", "--check", patch], cwd=target, capture_output=True, check=False).returncode != 0:
            raise SystemExit(f"Upstream integration points changed; refusing unsafe patch: {name}")
    marker = target / ".ashare-overlay.json"
    previous = json.loads(marker.read_text()) if marker.exists() else {}
    files = {}
    for source in sorted((OVERLAY / "src").rglob("*")):
        if not source.is_file():
            continue
        relative = source.relative_to(OVERLAY)
        destination = target / relative
        if destination.exists() and digest(destination) not in (digest(source), previous.get(str(relative))):
            raise SystemExit(f"Refusing to overwrite an independently modified file: {relative}")
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        files[str(relative)] = digest(source)
    marker.write_text(json.dumps(files, indent=2) + "\n")
    print(f"Prepared official frontend {head} at {target}; route /#/ashare-paper")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("checkout", type=Path, help="Separate official frontend checkout")
    parser.add_argument("--build", action="store_true", help="Install the locked dependencies without lifecycle hooks, then build")
    args = parser.parse_args()
    target = args.checkout.expanduser().resolve()
    prepare(target)
    if args.build:
        run("pnpm", "install", "--frozen-lockfile", "--ignore-scripts", cwd=target)
        run("pnpm", "build", cwd=target)
        shutil.copy2(OVERLAY / "UPSTREAM_LICENSE.txt", target / "dist" / "LICENSE.quantdinger-frontend.txt")


if __name__ == "__main__":
    main()
