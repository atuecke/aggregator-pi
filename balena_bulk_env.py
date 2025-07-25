#!/usr/bin/env python3
"""
Bulk-import environment variables into a balena fleet.
(Because I'm lazy and I don't want to manually add them in the dashboard)

Usage:
    ./balena_bulk_env.py --fleet <slug> [-e ./path/to/file] [--quiet]
The file can be either KEY=VALUE lines or "KEY VALUE" (space-separated).
"""

import argparse, subprocess, sys, pathlib

def parse_env_file(path):
    """Return dict of KEY:VALUE, skipping blanks and comments."""
    pairs = {}
    for raw in pathlib.Path(path).read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            k, v = line.split("=", 1)
        else:  # allow "KEY VALUE" style (forum trick)
            k, v = line.split(None, 1)
        pairs[k.strip()] = v.strip()
    return pairs

def balena_set(fleet, key, val, quiet=False):
    """Invoke balena CLI for a single variable."""
    cmd = ["balena", "env", "set", key, val, "--fleet", fleet]
    if quiet:
        cmd.append("--quiet")
    subprocess.run(cmd, check=True)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--fleet", "-f", required=True, help="Fleet slug or name")
    ap.add_argument("--envfile", "-e", default=".env",
                    help="Defaults to ./ .env")
    ap.add_argument("--quiet", "-q", action="store_true")
    args = ap.parse_args()

    for k, v in parse_env_file(args.envfile).items():
        print(f"⏩ {k}")
        balena_set(args.fleet, k, v, args.quiet)

if __name__ == "__main__":
    try:
        main()
    except subprocess.CalledProcessError as exc:
        sys.exit(exc.returncode)
