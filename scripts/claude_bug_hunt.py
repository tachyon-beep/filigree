#!/usr/bin/env python3
"""Per-file bug hunt using Claude CLI — example scanner for filigree.

Scans source files, runs `claude --print` on each with a static-analysis prompt,
writes markdown reports, and optionally POSTs findings to filigree's scan API.

This is a reference implementation (documentation-by-code) showing how external
tools integrate with filigree. The API is the first-class product.

No external dependencies beyond Python 3.11+ and `claude` on PATH.

Usage:
    python scripts/claude_bug_hunt.py                      # scan src/filigree/
    python scripts/claude_bug_hunt.py --root src/           # scan all of src/
    python scripts/claude_bug_hunt.py --dry-run             # list files + token estimate
    python scripts/claude_bug_hunt.py --no-ingest           # markdown only, skip API
    python scripts/claude_bug_hunt.py --max-files 20        # limit file count
    python scripts/claude_bug_hunt.py --model opus          # override model
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import re
import shutil
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

# Import shared utilities from scripts/scan_utils.py
sys.path.insert(0, str(Path(__file__).resolve().parent))
from scan_utils import (
    estimate_tokens,
    find_files,
    load_context,
    parse_findings,
    post_to_api,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(levelname)s: %(message)s",
)
logger = logging.getLogger(__name__)

# ── Config ──────────────────────────────────────────────────────────────

MAX_RETRIES = 3
RETRY_BASE_S = 2
STDERR_TRUNCATE = 500
DEFAULT_TIMEOUT_S = 300

PROMPT_TEMPLATE = """\
You are a static analysis agent doing a deep bug audit.
Target file: {file_path}

{context}

Instructions:
- Analyse the target file for real, concrete bugs.
- You may read any repo file to verify integration behaviour.
- Report bugs only if the primary fix belongs in the target file.
- If you find multiple distinct bugs, separate them with a line containing only '---'.
- If you find no credible bug, output exactly:
  "No concrete bug found in {file_path}"
- Cite file paths and line numbers in evidence.

Bug categories to check (use these exact rule_id values):
1. **logic-error** — off-by-one, wrong comparison, unreachable branches
2. **error-handling** — bare except, swallowed exceptions, missing cleanup
3. **resource-leak** — unclosed files/connections, missing context managers
4. **race-condition** — shared mutable state in async code, missing locks
5. **type-error** — wrong return types, schema mismatches
6. **injection** — unsanitised inputs, missing parameterisation, XSS
7. **performance** — O(n²) where avoidable, blocking I/O in async
8. **api-misuse** — wrong argument order, deprecated calls, silent failures
9. **other** — anything that doesn't fit the above

For each bug found, use this format:

## Summary
<one-line description>

## Severity
- Severity: <critical|major|minor|trivial>
- Priority: <P0|P1|P2|P3>

## Evidence
<file:line citations, code snippets>

## Root Cause Hypothesis
<why the bug exists>

## Suggested Fix
<concrete fix>
"""


# ── Helpers ─────────────────────────────────────────────────────────────


def _display_path(path: Path, base: Path) -> Path:
    try:
        return path.relative_to(base)
    except ValueError:
        return path


# ── Claude CLI execution ────────────────────────────────────────────────


async def run_claude(
    *,
    prompt: str,
    output_path: Path,
    model: str,
    repo_root: Path,
    timeout: int,
) -> None:
    """Run `claude --print` once. Raises RuntimeError on failure."""
    output_path.parent.mkdir(parents=True, exist_ok=True)

    cmd: list[str] = [
        "claude",
        "--print",
        "--model",
        model,
        "-p",
        prompt,
    ]

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        cwd=repo_root,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except TimeoutError:
        proc.kill()
        await proc.wait()
        raise TimeoutError(f"claude timed out after {timeout}s") from None
    finally:
        if proc.returncode is None:
            proc.terminate()
            await proc.wait()

    if proc.returncode != 0:
        err = stderr.decode("utf-8", errors="replace")[:STDERR_TRUNCATE]
        raise RuntimeError(f"claude failed (rc={proc.returncode}): {err}")

    # Write stdout to output file
    output_path.write_bytes(stdout)


async def run_claude_with_retry(
    *,
    prompt: str,
    output_path: Path,
    model: str,
    repo_root: Path,
    timeout: int,
) -> None:
    """Run claude with exponential backoff retries."""
    last_exc: Exception | None = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            await run_claude(
                prompt=prompt,
                output_path=output_path,
                model=model,
                repo_root=repo_root,
                timeout=timeout,
            )
            return
        except (RuntimeError, TimeoutError) as exc:
            last_exc = exc
            if attempt < MAX_RETRIES:
                wait = RETRY_BASE_S * (2 ** (attempt - 1))
                print(f"  retry {attempt}/{MAX_RETRIES} in {wait}s ...", file=sys.stderr)
                await asyncio.sleep(wait)
    raise RuntimeError(f"all {MAX_RETRIES} attempts failed") from last_exc


# ── Batch runner ────────────────────────────────────────────────────────


async def analyse_files(
    *,
    files: list[Path],
    output_dir: Path,
    root_dir: Path,
    repo_root: Path,
    model: str,
    batch_size: int,
    context: str,
    skip_existing: bool,
    timeout: int,
    api_url: str,
    no_ingest: bool,
    scan_run_id: str,
) -> dict[str, int]:
    """Run analysis on all files in batches. Returns summary stats."""
    failed: list[tuple[Path, Exception]] = []
    report_paths: list[Path] = []
    done = 0
    total = len(files)
    api_successes = 0
    api_failures = 0

    for batch_start in range(0, total, batch_size):
        batch = files[batch_start : batch_start + batch_size]
        tasks: list[tuple[Path, Path, asyncio.Task[None]]] = []

        for fpath in batch:
            rel = fpath.relative_to(root_dir)
            out = (output_dir / rel).with_suffix(rel.suffix + ".md")

            if skip_existing and out.exists():
                done += 1
                report_paths.append(out)
                print(f"  [skip] {_display_path(fpath, repo_root)}", file=sys.stderr)
                continue

            prompt = PROMPT_TEMPLATE.format(file_path=fpath, context=context)
            task = asyncio.create_task(
                run_claude_with_retry(
                    prompt=prompt,
                    output_path=out,
                    model=model,
                    repo_root=repo_root,
                    timeout=timeout,
                )
            )
            tasks.append((fpath, out, task))

        results = await asyncio.gather(*(t for _, _, t in tasks), return_exceptions=True)
        for (fpath, out, _), result in zip(tasks, results, strict=True):
            done += 1
            if isinstance(result, Exception):
                failed.append((fpath, result))
                print(f"  FAIL {_display_path(fpath, repo_root)}: {result}", file=sys.stderr)
            else:
                report_paths.append(out)
                print(f"  [{done}/{total}] {_display_path(fpath, repo_root)}", file=sys.stderr)

                # Parse findings and POST to API
                if not no_ingest and out.exists():
                    text = out.read_text(encoding="utf-8")
                    rel_path = str(_display_path(fpath, repo_root))
                    findings = parse_findings(text, file_path=rel_path)
                    if findings:
                        ok = post_to_api(
                            api_url=api_url,
                            scan_source="claude",
                            scan_run_id=scan_run_id,
                            findings=findings,
                        )
                        if ok:
                            api_successes += len(findings)
                        else:
                            api_failures += len(findings)

    # ── Summary stats ────────
    stats: Counter[str] = Counter()
    for md in report_paths:
        if not md.exists():
            continue
        text = md.read_text(encoding="utf-8")
        if "No concrete bug found" in text:
            stats["clean"] += 1
        else:
            priorities = re.findall(r"Priority:\s*(P\d)", text, re.IGNORECASE)
            if priorities:
                for pri in priorities:
                    stats[pri.upper()] += 1
            else:
                stats["unknown"] += 1

    stats["failed"] = len(failed)
    stats["api_posted"] = api_successes
    stats["api_failed"] = api_failures
    return dict(stats)


# ── CLI ─────────────────────────────────────────────────────────────────


def main() -> int:
    parser = argparse.ArgumentParser(description="Per-file bug hunt via Claude CLI.")
    parser.add_argument("--root", default="src/filigree", help="Directory to scan (default: src/filigree)")
    parser.add_argument("--output-dir", default="docs/bugs/generated", help="Report output dir")
    parser.add_argument("--batch-size", type=int, default=5, help="Concurrent claude runs (default: 5)")
    parser.add_argument("--model", default="sonnet", help="Claude model (default: sonnet)")
    parser.add_argument("--file-type", choices=["python", "all"], default="python", help="File filter")
    parser.add_argument("--skip-existing", action="store_true", help="Skip files with existing reports")
    parser.add_argument(
        "--timeout",
        type=int,
        default=DEFAULT_TIMEOUT_S,
        help=f"Per-file timeout in seconds (default: {DEFAULT_TIMEOUT_S})",
    )
    parser.add_argument("--dry-run", action="store_true", help="List files with count and token estimate")
    parser.add_argument("--max-files", type=int, default=50, help="Maximum files to scan (default: 50)")
    parser.add_argument("--api-url", default="http://localhost:8377", help="Filigree dashboard URL")
    parser.add_argument("--no-ingest", action="store_true", help="Skip API POST (markdown-only mode)")
    parser.add_argument("--scan-run-id", default=None, help="External scan run ID (from MCP trigger)")

    args = parser.parse_args()

    if args.batch_size < 1:
        print("Error: --batch-size must be at least 1", file=sys.stderr)
        return 1

    repo_root = Path(__file__).resolve().parents[1]
    root_dir = (repo_root / args.root).resolve()
    output_dir = (repo_root / args.output_dir).resolve()

    if not root_dir.is_dir():
        print(f"Error: scan root is not a directory: {root_dir}", file=sys.stderr)
        return 1

    files = find_files(
        root_dir,
        file_type=args.file_type,
        exclude_dirs={output_dir},
        max_files=args.max_files,
    )
    if not files:
        print(f"No files found under {root_dir}", file=sys.stderr)
        return 1

    if args.dry_run:
        tokens = estimate_tokens(files)
        print(f"Would analyse {len(files)} files (~{tokens:,} estimated tokens):")
        for f in files:
            print(f"  {_display_path(f, repo_root)}")
        return 0

    if shutil.which("claude") is None:
        print("Error: `claude` not found on PATH", file=sys.stderr)
        return 1

    context = load_context(repo_root)
    scan_run_id = args.scan_run_id or f"claude-{datetime.now(datetime.UTC).isoformat()}"

    print(f"Analysing {len(files)} files (batch={args.batch_size}, model={args.model}) ...", file=sys.stderr)
    if not args.no_ingest:
        print(f"  API: {args.api_url}  run_id: {scan_run_id}", file=sys.stderr)

    stats = asyncio.run(
        analyse_files(
            files=files,
            output_dir=output_dir,
            root_dir=root_dir,
            repo_root=repo_root,
            model=args.model,
            batch_size=args.batch_size,
            context=context,
            skip_existing=args.skip_existing,
            timeout=args.timeout,
            api_url=args.api_url,
            no_ingest=args.no_ingest,
            scan_run_id=scan_run_id,
        )
    )

    # ── Print summary ───────────────────────────────────────────────
    print("\n" + "=" * 50)
    print(f"Bug Hunt Summary (claude/{args.model})")
    print("=" * 50)
    defects = sum(v for k, v in stats.items() if k not in ("clean", "failed", "unknown", "api_posted", "api_failed"))
    print(f"  Defects found:  {defects}")
    for pri in ("P0", "P1", "P2", "P3"):
        c = stats.get(pri, 0)
        if c:
            print(f"    {pri}: {c}")
    print(f"  Clean files:    {stats.get('clean', 0)}")
    if stats.get("failed", 0):
        print(f"  Failed:         {stats['failed']}")
    if not args.no_ingest:
        print(f"  API posted:     {stats.get('api_posted', 0)}")
        if stats.get("api_failed", 0):
            print(f"  API failures:   {stats['api_failed']}")
    print("=" * 50)

    # Exit non-zero if all API posts failed
    if not args.no_ingest and stats.get("api_posted", 0) == 0 and stats.get("api_failed", 0) > 0:
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
