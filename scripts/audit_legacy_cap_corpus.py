#!/usr/bin/env python3
"""Run the private legacy CAP reference corpus through the native importer.

The repository manifest contains only relative paths and hashes.  The actual
CAP/PDF roots stay outside Git and are supplied at runtime.
"""
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
import subprocess
import sys


def pdf_pages(path: Path) -> int | None:
    try:
        result = subprocess.run(
            ["pdfinfo", str(path)], capture_output=True, text=True,
            timeout=15, check=True,
        )
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return None
    for line in result.stdout.splitlines():
        if line.startswith("Pages:"):
            try:
                return int(line.split(":", 1)[1].strip())
            except ValueError:
                return None
    return None


def resolve_manifest_path(root: Path, relative: str, prefix: str) -> Path:
    candidate = Path(relative)
    if candidate.parts and candidate.parts[0].casefold() == prefix.casefold():
        candidate = Path(*candidate.parts[1:])
    return root / candidate


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cap-root", type=Path, required=True,
                        help="Directory containing the legacy CAP files")
    parser.add_argument("--pdf-root", type=Path, required=True,
                        help="Directory containing the paired reference PDFs")
    parser.add_argument("--manifest", type=Path,
                        default=Path("docs/legacy-cap-pdf-inventory.csv"))
    parser.add_argument("--output", type=Path, required=True,
                        help="Directory for generated MSCZ files and JSON results")
    parser.add_argument("--limit", type=int, default=None,
                        help="Optional number of manifest rows to process")
    args = parser.parse_args()

    from app.main import process_score

    rows = list(csv.DictReader(args.manifest.open(newline="", encoding="utf-8")))
    if args.limit is not None:
        rows = rows[:max(0, args.limit)]
    args.output.mkdir(parents=True, exist_ok=True)
    results = []
    for row in rows:
        cap = resolve_manifest_path(args.cap_root, row["cap_relative_path"], "Cap")
        pdf = resolve_manifest_path(args.pdf_root, row["pdf_relative_path"], "Pdf")
        work = args.output / row["id"]
        work.mkdir(parents=True, exist_ok=True)
        result = {
            "id": row["id"],
            "cap": str(cap),
            "pdf": str(pdf),
            "expected_pdf_pages": int(row["pdf_pages"]),
        }
        try:
            if not cap.is_file():
                raise FileNotFoundError(cap)
            if not pdf.is_file():
                raise FileNotFoundError(pdf)
            converted = process_score(
                cap.name, ".cap", cap.read_bytes(), work, "balanced"
            )
            result.update({
                "ok": True,
                "verification": converted.get("verification"),
                "certification": converted.get("certification"),
                "native_audit": converted.get("native_audit"),
                "warnings": converted.get("warnings", []),
                "files": converted.get("files", {}),
                "output_pdf_pages": pdf_pages(work / converted["files"]["pdf"])
                if converted.get("files", {}).get("pdf") else None,
            })
        except Exception as exc:  # one corpus item must not stop the audit
            result.update({"ok": False, "error": str(exc)[:1000]})
        results.append(result)
        print(json.dumps(result, ensure_ascii=False), flush=True)

    summary = {
        "total": len(results),
        "ok": sum(1 for result in results if result["ok"]),
        "failed": sum(1 for result in results if not result["ok"]),
        "manual_review": sum(
            1 for result in results
            if result.get("verification") == "manual-review"
        ),
        "page_count_mismatches": sum(
            1 for result in results
            if result.get("output_pdf_pages") is not None
            and result["output_pdf_pages"] != result["expected_pdf_pages"]
        ),
    }
    (args.output / "results.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (args.output / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(summary, ensure_ascii=False), flush=True)
    return 0 if summary["failed"] == 0 else 2


if __name__ == "__main__":
    sys.exit(main())
