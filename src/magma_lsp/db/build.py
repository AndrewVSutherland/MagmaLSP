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
from .spec import attached_files
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
        if res.timed_out or res.returncode != 0:
            return "unknown"
        for line in res.stdout.strip().splitlines():
            line = line.strip()
            if line and line[0].isdigit():
                return line
    except (FileNotFoundError, RuntimeError):
        pass
    return "unknown"


def _norm_type(t: str | None) -> str:
    """Canonical type spelling: package shorthands -> the ``ListSignatures`` form, so the
    same overload declared as ``Q::[RngIntElt]`` and enumerated as ``SeqEnum[RngIntElt]``
    (or ``x::.`` vs ``x::Any``) compares equal."""
    s = " ".join((t or "").split())
    s = s.replace("[ ", "[").replace(" ]", "]").replace("{ ", "{").replace(" }", "}")
    if s in (".", ""):
        return "Any"
    if s == "[]":
        return "SeqEnum"
    if s == "{}":
        return "SetEnum"
    if s.startswith("{@") and s.endswith("@}"):
        inner = s[2:-2].strip()
        return f"SetIndx[{_norm_type(inner)}]" if inner else "SetIndx"
    if s.startswith("{*") and s.endswith("*}"):
        inner = s[2:-2].strip()
        return f"SetMulti[{_norm_type(inner)}]" if inner else "SetMulti"
    if s.startswith("[") and s.endswith("]"):
        return f"SeqEnum[{_norm_type(s[1:-1])}]"
    if s.startswith("{") and s.endswith("}"):
        return f"SetEnum[{_norm_type(s[1:-1])}]"
    return s


def _sig_type_key(sig: Signature) -> tuple:
    """Identity for dedup across sources: name + positional arg types (names ignored), with
    normalized type spellings and the ``~`` reference marker (``Foo(~x::T)`` and
    ``Foo(x::T)`` are genuinely different intrinsics: in-place procedure vs function)."""
    return (
        sig.name,
        tuple(
            ("~" if p.name.startswith("~") else "") + _norm_type(p.type) for p in sig.args
        ),
    )


def extract_package(
    root: str, *, workers: int | None = None, only_files: set[str] | None = None
) -> list[Signature]:
    files = list(iter_package_files(root))
    if only_files is not None:
        norm = {os.path.normpath(f) for f in only_files}
        files = [f for f in files if os.path.normpath(f) in norm]
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
    use_spec: bool = True,
    harvest_docs: bool = True,
) -> MagmaDB:
    # A relative root would silently defeat the spec-attachment filter (attached_files
    # absolutizes; iter_package_files would not) -> zero package signatures. Absolutize first.
    package_root = os.path.abspath(package_root)
    version = detect_version(magma_path, package_root)

    # Only extract intrinsics from spec-attached files: a default Magma session loads just those,
    # so extracting from every .m over-includes intrinsics from non-attached packages (CompTree,
    # test files, ...) that Magma doesn't register. Validated to lift Magma-confirmation to ~100%.
    only_files: set[str] | None = None
    if use_spec:
        spec_path = os.path.join(package_root, "spec")
        if os.path.isfile(spec_path):
            only_files = attached_files(spec_path)
            print(f"spec: {len(only_files)} attached .m files", file=sys.stderr)
    package_sigs = extract_package(package_root, workers=workers, only_files=only_files)

    have_magma = find_magma(magma_path) is not None
    kernel_sigs: list[Signature] = []
    kernel_error: str | None = None
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
                # enumerate_signatures REFUSES partial (timed-out/nonzero-exit) output — a
                # truncated kernel set saved as complete would silently mis-flag real
                # intrinsics as unknown. Degrade to a package-only DB and record why.
                print(
                    f"warning: ListSignatures enumeration failed ({exc}); "
                    "building package-only DB. This is usually a Magma licensing/auth issue.",
                    file=sys.stderr,
                )
                kernel_error = str(exc)

    db = merge(package_sigs, kernel_sigs, version)
    if kernel_error is not None:
        db.stats["kernel_enumeration_failed"] = kernel_error

    # ListSignatures(Cat) omits variadic intrinsics (Sprintf, Explode, ...). Recover the ones
    # actually used in package code by probing missing-from-DB call-targets with `name;`.
    if probe_missing and have_magma and kernel_sigs:
        candidates = sorted(harvest_call_targets(package_root) - set(db.intrinsics))
        try:
            recovered = probe_names(candidates, magma_path=magma_path, timeout=enum_timeout)
        except RuntimeError as exc:
            print(
                f"warning: variadic-intrinsic probe incomplete ({exc}); the DB may lack "
                "variadic kernel intrinsics (Sprintf, Explode, ...). Raise --enum-timeout "
                "and rebuild.",
                file=sys.stderr,
            )
            recovered = {}
            db.stats["probe_incomplete"] = str(exc)
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

    # Kernel doc harvest: ListSignatures shows no doc strings, but the REPL `name;` form does
    # (plus optional-parameter names). Probe every undocumented name and back-fill. Lifts doc
    # coverage from ~65% to ~96% for a couple of minutes of build time.
    if harvest_docs and have_magma and kernel_sigs:
        undocumented = sorted(n for n, i in db.intrinsics.items() if not i.has_doc)
        try:
            harvested = probe_names(undocumented, magma_path=magma_path, timeout=enum_timeout)
        except RuntimeError as exc:
            print(
                f"warning: kernel doc harvest incomplete ({exc}); some kernel intrinsics "
                "will lack doc strings. Raise --enum-timeout and rebuild.",
                file=sys.stderr,
            )
            harvested = {}
            db.stats["doc_harvest_incomplete"] = str(exc)
        filled_docs = filled_opts = 0
        for name, psigs in harvested.items():
            intr = db.intrinsics.get(name)
            if intr is None:
                continue
            by_key = {_sig_type_key(s): s for s in psigs}
            for sig in intr.signatures:
                psig = by_key.get(_sig_type_key(sig))
                if psig is None:
                    continue
                if psig.doc and not sig.doc:
                    sig.doc = psig.doc
                    filled_docs += 1
                if psig.opt_params and not sig.opt_params:
                    sig.opt_params = list(psig.opt_params)
                    filled_opts += 1
        db.stats["harvested_docs"] = filled_docs
        db.stats["harvested_opt_params"] = filled_opts
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
    ap.add_argument(
        "--no-doc-harvest",
        action="store_true",
        help="skip probing `name;` for kernel doc strings / optional-parameter names "
        "(faster build, ~30%% fewer documented names)",
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
        harvest_docs=not args.no_doc_harvest,
    )
    out = Path(args.out) if args.out else db_path_for_version(db.version)
    out.parent.mkdir(parents=True, exist_ok=True)
    db.save(out)
    print(f"Magma {db.version}: {db.stats}", file=sys.stderr)
    print(f"wrote {out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
