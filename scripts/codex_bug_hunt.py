#!/usr/bin/env python3
"""Simple per-file bug hunt using Codex.

Scans Python files, runs `codex exec` on each with a static-analysis prompt,
and writes one markdown report per file into an output directory.

No external dependencies beyond Python 3.12+ and `codex` on PATH.

Usage:
    python scripts/codex_bug_hunt.py                    # scan src/filigree/
    python scripts/codex_bug_hunt.py --root src/        # scan all of src/
    python scripts/codex_bug_hunt.py --dry-run           # list files only
    python scripts/codex_bug_hunt.py --batch-size 5      # 5 concurrent
    python scripts/codex_bug_hunt.py --model o3          # override model
"""

from __future__ import annotations

import argparse
import asyncio
import re
import shutil
import sys
from collections import Counter
from pathlib import Path

# ── Config ──────────────────────────────────────────────────────────────

MAX_RETRIES = 3
RETRY_BASE_S = 2  # exponential backoff: 2s, 4s, 8s
STDERR_TRUNCATE = 500
DEFAULT_TIMEOUT_S = 300  # 5 minutes per codex invocation

EXCLUDE_DIRS = {
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".git",
    ".tox",
    ".nox",
    ".venv",
    "venv",
    ".eggs",
    "htmlcov",
    "node_modules",
    ".filigree",
}

BINARY_EXTENSIONS = {
    ".pyc",
    ".pyo",
    ".so",
    ".dylib",
    ".dll",
    ".exe",
    ".db",
    ".sqlite",
    ".sqlite3",
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".ico",
    ".bmp",
    ".svg",
    ".webp",
    ".woff",
    ".woff2",
    ".ttf",
    ".eot",
    ".otf",
    ".whl",
    ".egg",
    ".tar",
    ".gz",
    ".zip",
    ".bz2",
    ".xz",
    ".pdf",
    ".doc",
    ".docx",
    ".bin",
    ".dat",
    ".pickle",
    ".pkl",
}

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

Bug categories to check:
1. **Logic errors** — off-by-one, wrong comparison, unreachable branches
2. **Error handling gaps** — bare except, swallowed exceptions, missing cleanup
3. **Resource leaks** — unclosed files/connections, missing context managers
4. **Race conditions** — shared mutable state in async code, missing locks
5. **Type/contract violations** — wrong return types, schema mismatches
6. **SQL/injection risks** — unsanitised inputs, missing parameterisation
7. **Performance issues** — O(n²) where avoidable, blocking I/O in async
8. **API misuse** — wrong argument order, deprecated calls, silent failures

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


# ── File discovery ──────────────────────────────────────────────────────


def _is_excluded(path: Path) -> bool:
    return any(part in EXCLUDE_DIRS for part in path.parts) or path.suffix in {".pyc", ".pyo"}


def _is_python(path: Path) -> bool:
    return path.suffix == ".py" and not path.name.startswith("test_")


def find_files(root: Path, *, file_type: str) -> list[Path]:
    candidates = sorted(p for p in root.rglob("*") if p.is_file() and not _is_excluded(p))
    if file_type == "python":
        candidates = [p for p in candidates if _is_python(p)]
    else:
        candidates = [p for p in candidates if p.suffix not in BINARY_EXTENSIONS]
    return candidates


# ── Codex execution with retry ──────────────────────────────────────────


async def run_codex(
    *,
    prompt: str,
    output_path: Path,
    model: str | None,
    repo_root: Path,
    timeout: int,
) -> None:
    """Run `codex exec` once. Raises RuntimeError on failure."""
    output_path.parent.mkdir(parents=True, exist_ok=True)

    cmd: list[str] = [
        "codex",
        "exec",
        "--sandbox",
        "read-only",
        "-c",
        'approval_policy="never"',
        "--output-last-message",
        str(output_path),
    ]
    if model:
        cmd.extend(["--model", model])
    cmd.append(prompt)

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        cwd=repo_root,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        _, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except TimeoutError:
        proc.kill()
        await proc.wait()
        raise TimeoutError(f"codex exec timed out after {timeout}s") from None
    finally:
        if proc.returncode is None:
            proc.terminate()
            await proc.wait()

    if proc.returncode != 0:
        err = stderr.decode("utf-8", errors="replace")[:STDERR_TRUNCATE]
        raise RuntimeError(f"codex exec failed (rc={proc.returncode}): {err}")


async def run_codex_with_retry(
    *,
    prompt: str,
    output_path: Path,
    model: str | None,
    repo_root: Path,
    timeout: int,
) -> None:
    """Run codex exec with exponential backoff retries."""
    last_exc: Exception | None = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            await run_codex(
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
    model: str | None,
    batch_size: int,
    context: str,
    skip_existing: bool,
    timeout: int,
) -> dict[str, int]:
    """Run analysis on all files in batches. Returns summary stats."""
    failed: list[tuple[Path, Exception]] = []
    report_paths: list[Path] = []
    done = 0
    total = len(files)

    for batch_start in range(0, total, batch_size):
        batch = files[batch_start : batch_start + batch_size]
        tasks: list[tuple[Path, Path, asyncio.Task[None]]] = []

        for fpath in batch:
            rel = fpath.relative_to(root_dir)
            out = (output_dir / rel).with_suffix(rel.suffix + ".md")

            if skip_existing and out.exists():
                done += 1
                report_paths.append(out)
                print(f"  [skip] {fpath.relative_to(repo_root)}", file=sys.stderr)
                continue

            prompt = PROMPT_TEMPLATE.format(file_path=fpath, context=context)
            task = asyncio.create_task(
                run_codex_with_retry(
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
                print(f"  FAIL {fpath.relative_to(repo_root)}: {result}", file=sys.stderr)
            else:
                report_paths.append(out)
                print(f"  [{done}/{total}] {fpath.relative_to(repo_root)}", file=sys.stderr)

    # ── Summary stats (scoped to this run's reports only) ────────
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
    return dict(stats)


# ── Organise by priority (optional) ────────────────────────────────────


def organise_by_priority(output_dir: Path) -> None:
    """Copy reports into by-priority/ dirs using the highest severity found."""
    pri_order = {"P0": 0, "P1": 1, "P2": 2, "P3": 3}
    by_pri = output_dir / "by-priority"
    for md in output_dir.rglob("*.md"):
        if "by-priority" in md.parts:
            continue
        text = md.read_text(encoding="utf-8")
        if "No concrete bug found" in text:
            continue
        priorities = re.findall(r"Priority:\s*(P\d)", text, re.IGNORECASE)
        pri = min((p.upper() for p in priorities), key=lambda p: pri_order.get(p, 99)) if priorities else "unknown"
        dest = by_pri / pri
        dest.mkdir(parents=True, exist_ok=True)
        shutil.copy2(md, dest / md.name)


# ── Context loader ──────────────────────────────────────────────────────


def load_context(repo_root: Path) -> str:
    parts: list[str] = []
    for name in ("CLAUDE.md", "ARCHITECTURE.md"):
        path = repo_root / name
        if path.exists():
            parts.append(f"--- {name} ---\n{path.read_text(encoding='utf-8')}")
    return "\n\n".join(parts)


# ── CLI ─────────────────────────────────────────────────────────────────


def main() -> int:
    parser = argparse.ArgumentParser(description="Per-file bug hunt via Codex.")
    parser.add_argument("--root", default="src/filigree", help="Directory to scan (default: src/filigree)")
    parser.add_argument("--output-dir", default="docs/bugs/generated", help="Report output dir")
    parser.add_argument("--batch-size", type=int, default=10, help="Concurrent codex runs (default: 10)")
    parser.add_argument("--model", default=None, help="Override codex model")
    parser.add_argument("--file-type", choices=["python", "all"], default="python", help="File filter")
    parser.add_argument("--skip-existing", action="store_true", help="Skip files with existing reports")
    parser.add_argument("--organise-by-priority", action="store_true", help="Copy reports into by-priority/ dirs")
    parser.add_argument(
        "--timeout",
        type=int,
        default=DEFAULT_TIMEOUT_S,
        help=f"Per-file timeout in seconds (default: {DEFAULT_TIMEOUT_S})",
    )
    parser.add_argument("--dry-run", action="store_true", help="List files without running analysis")

    args = parser.parse_args()

    if shutil.which("codex") is None:
        print("Error: `codex` not found on PATH", file=sys.stderr)
        return 1

    repo_root = Path(__file__).resolve().parents[1]
    root_dir = (repo_root / args.root).resolve()
    output_dir = (repo_root / args.output_dir).resolve()

    files = find_files(root_dir, file_type=args.file_type)
    if not files:
        print(f"No files found under {root_dir}", file=sys.stderr)
        return 1

    if args.dry_run:
        print(f"Would analyse {len(files)} files:")
        for f in files:
            print(f"  {f.relative_to(repo_root)}")
        return 0

    context = load_context(repo_root)

    print(f"Analysing {len(files)} files (batch={args.batch_size}) ...", file=sys.stderr)
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
        )
    )

    if args.organise_by_priority:
        organise_by_priority(output_dir)

    # ── Print summary ───────────────────────────────────────────────
    print("\n" + "=" * 50)
    print("Bug Hunt Summary")
    print("=" * 50)
    defects = sum(v for k, v in stats.items() if k not in ("clean", "failed", "unknown"))
    print(f"  Defects found:  {defects}")
    for pri in ("P0", "P1", "P2", "P3"):
        c = stats.get(pri, 0)
        if c:
            print(f"    {pri}: {c}")
    print(f"  Clean files:    {stats.get('clean', 0)}")
    if stats.get("failed", 0):
        print(f"  Failed:         {stats['failed']}")
    print("=" * 50)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
