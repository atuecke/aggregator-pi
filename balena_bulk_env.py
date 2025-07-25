#!/usr/bin/env python3
"""
Bulk-import environment variables into a balena fleet.
(Because I'm lazy and I don't want to manually add them in the dashboard)

Usage:
    ./balena_bulk_env.py --fleet <slug> [-e ./path/to/file]
"""

import argparse, subprocess, sys, pathlib

def dequote(s: str) -> str:
    """Strip a single pair of matching ' or " from both ends, if present."""
    s = s.strip()
    if len(s) >= 2 and s[0] == s[-1] and s[0] in ("'", '"'):
        return s[1:-1]
    return s

def parse_env_file(path: str) -> dict:
    """Return {KEY: VALUE} from lines like KEY=VALUE; ignores blanks/#comments."""
    pairs = {}
    for i, raw in enumerate(pathlib.Path(path).read_text().splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise ValueError(f"Line {i}: missing '=' -> {raw!r}")
        k, v = line.split("=", 1)
        k = k.strip()
        v = dequote(v.strip())
        pairs[k] = v
    return pairs

def set_env_var(fleet: str, key: str, value: str, quiet: bool):
    # balena CLI auto-creates or overwrites with `env set`
    cmd = ["balena", "env", "set", key, value, "--fleet", fleet]
    if quiet:
        cmd.append("--quiet")
    subprocess.run(cmd, check=True)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--fleet", "-f", required=True, help="Fleet slug or name")
    ap.add_argument("--envfile", "-e", default=".env", help="Path to env file")
    ap.add_argument("--quiet", "-q", action="store_true", help="Hide CLI output")
    ap.add_argument("--dry-run", action="store_true", help="Print what would run")
    args = ap.parse_args()

    pairs = parse_env_file(args.envfile)
    if not pairs:
        print("No variables found. Exiting.")
        return

    for k, v in pairs.items():
        if args.dry_run:
            print(f"balena env set {k} {v} --fleet {args.fleet}")
        else:
            print(f"⏩ {k}")
            set_env_var(args.fleet, k, v, args.quiet)

if __name__ == "__main__":
    try:
        main()
    except subprocess.CalledProcessError as e:
        sys.exit(e.returncode)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)