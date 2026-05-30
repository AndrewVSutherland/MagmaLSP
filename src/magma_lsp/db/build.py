"""Build the merged signature database.

Strategy (CLAUDE.md §4): ``ListSignatures`` for completeness (incl. kernel intrinsics) +
package ``.m`` files for arg names, optional parameters, doc strings, and source locations.

    package extraction  -> rich signatures (kind="package", with location + docs + opt params)
    ListSignatures      -> spine of all (name, arg-types) incl. kernel
    merge               -> package sigs kept; kernel sigs added to fill gaps (kind="kernel")
"""

from __future__ import annotations

import argparse
import datetime
import os
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

from ..magma.runner import find_magma, run_source
from .listsig import enumerate_signatures
from .model import Intrinsic, MagmaDB, Signature
from .package import extract_file, iter_package_files
from .probe import harvest_call_targets, probe_names
from .store import db_path_for_version

DEFAULT_PACKAGE_ROOT = "/opt/magma/package"


def detect_version(magma_path: str | None = None, package_root: str | None = None) -> str:
    # Prefer the Magma-free VERSION file shipped in the package root.
    if package_root:
        vf = Path(package_root) / "VERSION"
        if vf.is_file():
            txt = vf.read_text(encoding="utf-8").strip()
            if txt:
                return txt.splitlines()[0].strip()
    # Fall back to asking Magma (requires an authorised install).
    try:
        res = run_source(
            'v1,v2,v3 := GetVersion(); printf "%o.%o-%o", v1, v2, v3;\n',
            magma_path=magma_path,
            timeout=30.0,
        )
        for line in res.stdout.strip().splitlines():
            line = line.strip()
            if line and line[0].isdigit():
                return line
    except (FileNotFoundError, RuntimeError):
        pass
    return "unknown"


def _sig_type_key(sig: Signature) -> tuple:
    """Identity for dedup across sources: name + positional arg types (names ignored)."""
    return (sig.name, tuple((p.type or "") for p in sig.args))


def extract_package(root: str, *, workers: int | None = None) -> list[Signature]:
    files = list(iter_package_files(root))
    sigs: list[Signature] = []
    workers = workers or min(32, (os.cpu_count() or 4))
    with ProcessPoolExecutor(max_workers=workers) as pool:
        for file_sigs in pool.map(extract_file, files, chunksize=16):
            sigs.extend(file_sigs)
    return sigs


def merge(package_sigs: list[Signature], kernel_sigs: list[Signature], version: str) -> MagmaDB:
    intrinsics: dict[str, Intrinsic] = {}
    for s in package_sigs:
        intrinsics.setdefault(s.name, Intrinsic(name=s.name)).signatures.append(s)

    kernel_by_name: dict[str, list[Signature]] = {}
    for s in kernel_sigs:
        kernel_by_name.setdefault(s.name, []).append(s)

    kernel_only = 0
    for name, ksigs in kernel_by_name.items():
        intr = intrinsics.get(name)
        if intr is None:
            intrinsics[name] = Intrinsic(name=name, signatures=ksigs)
            kernel_only += 1
        else:
            existing = {_sig_type_key(s) for s in intr.signatures}
            for ks in ksigs:
                if _sig_type_key(ks) not in existing:
                    intr.signatures.append(ks)
                    existing.add(_sig_type_key(ks))

    total_sigs = sum(len(i.signatures) for i in intrinsics.values())
    db = MagmaDB(
        version=version,
        intrinsics=intrinsics,
        built_at=datetime.datetime.now(datetime.UTC).isoformat(timespec="seconds"),
        stats={
            "names": len(intrinsics),
            "total_signatures": total_sigs,
            "package_signatures": len(package_sigs),
            "kernel_signatures": len(kernel_sigs),
            "kernel_only_names": kernel_only,
            "documented_names": sum(1 for i in intrinsics.values() if i.has_doc),
        },
    )
    return db


def build_db(
    *,
    package_root: str = DEFAULT_PACKAGE_ROOT,
    magma_path: str | None = None,
    enum_timeout: float = 180.0,
    workers: int | None = None,
    include_kernel: bool = True,
    probe_missing: bool = True,
) -> MagmaDB:
    version = detect_version(magma_path, package_root)
    package_sigs = extract_package(package_root, workers=workers)

    have_magma = find_magma(magma_path) is not None
    kernel_sigs: list[Signature] = []
    if include_kernel:
        if not have_magma:
            print(
                "warning: Magma not found; building package-only DB "
                "(no kernel intrinsics). Re-run when Magma is available to add them.",
                file=sys.stderr,
            )
        else:
            try:
                kernel_sigs = enumerate_signatures(magma_path=magma_path, timeout=enum_timeout)
            except (RuntimeError, FileNotFoundError) as exc:
                print(
                    f"warning: ListSignatures enumeration failed ({exc}); "
                    "building package-only DB. This is usually a Magma licensing/auth issue.",
                    file=sys.stderr,
                )

    db = merge(package_sigs, kernel_sigs, version)

    # ListSignatures(Cat) omits variadic intrinsics (Sprintf, Explode, ...). Recover the ones
    # actually used in package code by probing missing-from-DB call-targets with `name;`.
    if probe_missing and have_magma and kernel_sigs:
        candidates = sorted(harvest_call_targets(package_root) - set(db.intrinsics))
        recovered = probe_names(candidates, magma_path=magma_path, timeout=enum_timeout)
        rec_sigs = 0
        for name, sigs in recovered.items():
            if name not in db.intrinsics:
                db.intrinsics[name] = Intrinsic(name=name, signatures=sigs)
                rec_sigs += len(sigs)
        db.stats["probed_candidates"] = len(candidates)
        db.stats["recovered_intrinsics"] = len(recovered)
        db.stats["recovered_signatures"] = rec_sigs
        db.stats["names"] = len(db.intrinsics)
        db.stats["total_signatures"] = sum(len(i.signatures) for i in db.intrinsics.values())
        db.stats["documented_names"] = sum(1 for i in db.intrinsics.values() if i.has_doc)
    return db


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="magma-lsp-build-db", description=__doc__)
    ap.add_argument(
        "--package-root", default=os.environ.get("MAGMA_PACKAGE_ROOT", DEFAULT_PACKAGE_ROOT)
    )
    ap.add_argument("--magma-path", default=None)
    ap.add_argument("--out", default=None, help="output path (default: per-version cache path)")
    ap.add_argument("--workers", type=int, default=None)
    ap.add_argument("--enum-timeout", type=float, default=180.0)
    ap.add_argument(
        "--no-probe",
        action="store_true",
        help="skip recovering variadic intrinsics (Sprintf, Explode) that ListSignatures omits",
    )
    args = ap.parse_args(argv)

    if not Path(args.package_root).is_dir():
        print(f"error: package root not found: {args.package_root}", file=sys.stderr)
        return 2
    if find_magma(args.magma_path) is None:
        print(
            "note: Magma not found; building a package-only DB (no kernel intrinsics).",
            file=sys.stderr,
        )

    print(f"Building Magma signature DB from {args.package_root} ...", file=sys.stderr)
    db = build_db(
        package_root=args.package_root,
        magma_path=args.magma_path,
        enum_timeout=args.enum_timeout,
        workers=args.workers,
        probe_missing=not args.no_probe,
    )
    out = Path(args.out) if args.out else db_path_for_version(db.version)
    out.parent.mkdir(parents=True, exist_ok=True)
    db.save(out)
    print(f"Magma {db.version}: {db.stats}", file=sys.stderr)
    print(f"wrote {out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
