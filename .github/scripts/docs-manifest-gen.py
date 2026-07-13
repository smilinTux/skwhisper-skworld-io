#!/usr/bin/env python3
"""
docs-manifest-gen.py — build docs-manifest.json for the SKWorld repo-docs viewer.

The viewer (docs.html, from docs.html.tmpl) lists a repo's docs/*.md live from the
GitHub Contents API. When that API is unavailable or rate-limited (unauthenticated
GitHub allows only ~60 req/hr/IP), the viewer falls back to a committed
`docs-manifest.json` sitting next to it. This script generates that manifest.

Two modes (stdlib only, Python 3.8+):

  # remote — list docs via the GitHub API, titles from each file's first H1
  python docs-manifest-gen.py smilinTux/skcomms --out docs-manifest.json

  # local — walk a checked-out repo's docs/ folder (no network)
  python docs-manifest-gen.py smilinTux/skcomms \
      --local ~/clawd/skcapstone-repos/skcomms --out docs-manifest.json

Options:
  --branch main       branch to reference in the manifest (default: main)
  --path docs         folder within the repo holding the *.md files (default: docs)
  --out PATH          write here (default: stdout)
  --local DIR         read files from a local checkout instead of the network
  --no-titles         skip H1 extraction (faster; titles = prettified filenames)

Manifest shape (matches the viewer's expectations):
  {
    "repo": "smilinTux/skcomms", "branch": "main", "path": "docs",
    "generated": "YYYY-MM-DD", "note": "...",
    "docs": [ { "name": "file.md", "title": "First H1 or prettified name" }, ... ]
  }
The `docs` order is the manifest's curated nav order; the API path preserves it.
"""
import argparse
import json
import os
import re
import sys
import urllib.request
import urllib.error
from datetime import date

UA = {"User-Agent": "skworld-docs-manifest-gen"}
H1_RE = re.compile(r"^\s{0,3}#\s+(.+?)\s*#*\s*$", re.MULTILINE)


def prettify(name: str) -> str:
    stem = re.sub(r"\.mdx?$", "", name, flags=re.IGNORECASE)
    stem = re.sub(r"[_-]+", " ", stem)
    return re.sub(r"\b\w", lambda m: m.group(0).upper(), stem)


def first_h1(text: str) -> str | None:
    m = H1_RE.search(text)
    if not m:
        return None
    # strip inline markdown emphasis/backticks/links for a clean title
    t = m.group(1).strip()
    t = re.sub(r"`([^`]*)`", r"\1", t)
    t = re.sub(r"\*\*([^*]*)\*\*", r"\1", t)
    t = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", t)
    return t.strip() or None


def http_get(url: str) -> bytes:
    headers = {**UA, "Accept": "application/vnd.github+json"}
    # Optional auth: in CI, GITHUB_TOKEN/GH_TOKEN raises the anon 60 req/hr
    # ceiling to 1000+/hr and lets private source repos resolve. No token = anon.
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read()


def list_local(local_dir: str, sub: str, want_titles: bool) -> list[dict]:
    docs_dir = os.path.join(local_dir, sub)
    if not os.path.isdir(docs_dir):
        sys.exit(f"error: no such directory: {docs_dir}")
    out = []
    for name in sorted(os.listdir(docs_dir)):
        if not re.search(r"\.mdx?$", name, re.IGNORECASE):
            continue
        title = prettify(name)
        if want_titles:
            try:
                with open(os.path.join(docs_dir, name), encoding="utf-8", errors="replace") as fh:
                    h1 = first_h1(fh.read())
                if h1:
                    title = h1
            except OSError:
                pass
        out.append({"name": name, "title": title})
    return out


def list_remote(repo: str, branch: str, sub: str, want_titles: bool) -> list[dict]:
    api = f"https://api.github.com/repos/{repo}/contents/{sub}?ref={branch}"
    try:
        data = json.loads(http_get(api))
    except urllib.error.HTTPError as e:
        sys.exit(f"error: GitHub API {e.code} for {api} "
                 f"({'rate-limited — try --local' if e.code == 403 else e.reason})")
    except urllib.error.URLError as e:
        sys.exit(f"error: cannot reach GitHub ({e.reason}) — try --local")
    if not isinstance(data, list):
        sys.exit(f"error: unexpected API response for {sub} (is the path a directory?)")
    out = []
    for x in data:
        if x.get("type") != "file" or not re.search(r"\.mdx?$", x.get("name", ""), re.IGNORECASE):
            continue
        name = x["name"]
        title = prettify(name)
        if want_titles:
            raw = f"https://raw.githubusercontent.com/{repo}/{branch}/{sub}/{name}"
            try:
                h1 = first_h1(http_get(raw).decode("utf-8", "replace"))
                if h1:
                    title = h1
            except (urllib.error.URLError, OSError):
                pass
        out.append({"name": name, "title": title})
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description="Generate docs-manifest.json for the SKWorld repo-docs viewer.")
    ap.add_argument("repo", help="GitHub owner/repo, e.g. smilinTux/skcomms")
    ap.add_argument("--branch", default="main")
    ap.add_argument("--path", default="docs", help="docs folder within the repo (default: docs)")
    ap.add_argument("--out", default="-", help="output file (default: stdout)")
    ap.add_argument("--local", default=None, help="read from a local checkout instead of the network")
    ap.add_argument("--no-titles", action="store_true", help="skip H1 extraction")
    args = ap.parse_args()

    want_titles = not args.no_titles
    if args.local:
        docs = list_local(args.local, args.path, want_titles)
    else:
        docs = list_remote(args.repo, args.branch, args.path, want_titles)

    if not docs:
        sys.exit(f"error: no *.md files found under {args.path}/")

    manifest = {
        "repo": args.repo,
        "branch": args.branch,
        "path": args.path,
        "generated": date.today().isoformat(),
        "note": ("Committed fallback for the /docs viewer when the GitHub Contents API is "
                 "unavailable or rate-limited. Regenerate from: "
                 f"python docs-manifest-gen.py {args.repo} --out docs-manifest.json"),
        "docs": docs,
    }
    text = json.dumps(manifest, indent=2, ensure_ascii=False) + "\n"

    if args.out == "-":
        sys.stdout.write(text)
    else:
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(text)
        print(f"wrote {args.out} — {len(docs)} docs from {args.repo}@{args.branch}:{args.path}/", file=sys.stderr)


if __name__ == "__main__":
    main()
