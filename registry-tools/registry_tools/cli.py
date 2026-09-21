"""``python -m registry_tools <command>`` (Requirement 8.3, 8.6, 8.7).

| Command | What it does |
|---|---|
| ``keygen --out KEY [--comment TEXT]`` | an Ed25519 key pair: ``KEY`` (private, mode 0600, never committed) and ``KEY.pub.json`` (the public record an edition adapter pins) |
| ``build REPO [--out index.json] [--compat compat.json] [--sign KEY] [--app-registry] [--check] [--floofycrew VERSION] [--resolve-links]`` | ``index.json`` from ``mods/**`` with the matrix cells folded in; ``--check`` only compares with the committed index (CI); ``--sign`` writes ``index.json.sig``; ``--app-registry`` also emits the KiroCrew-compatible ``app-registry.json``; ``--resolve-links`` reads every unresolved link record at its branch and pins ``commit``, ``manifestSha256`` and ``files[]`` (Requirement 8.9) |
| ``sign PAYLOAD --key KEY [--out SIG]`` | the detached signature of any JSON document (``index.json``, ``compat.json``) |
| ``verify PAYLOAD [--sig SIG] [--public-key RECORD …] [--key-id ID] [--edition-keys]`` | verify a detached signature against the given public records, or the keys pinned by the installed edition adapters |
| ``validate-submission MOD [--registry REPO] [--tag TAG] [--allowlist FILE] [--terms t,t] [--json]`` | the CI gate for a pull request (schema, ``floofy validate``, hashes, licence, masquerade, tag, record consistency) |
| ``validate-submission --record ID@VERSION --registry REPO [--json]`` | the same gate for a **link record**: fetch ``link.repo`` at the pinned commit, compare commit / manifest hash / ``files[]`` with the record, then every check above on the checkout (Requirement 8.9, 8.10) |
| ``compat-merge COMPAT ROW [--sign KEY] [--source LABEL]`` | add/replace a matrix row from a Forge run file; ``--override CELL=VERDICT --reason … --by …`` records a human override instead |
| ``app-registry REPO [--out app-registry.json]`` | the KiroCrew-compatible ``app-registry.json`` for the app-kind mods of ``index.json`` (task 7.4) |
| ``record REPO ID [--repo URL] [--path DIR] [--ref BRANCH] [--source CHECKOUT [--source-ref COMMITISH]] [--contact ALIAS] [--refresh] [--json]`` | make (or ``--refresh``) the link record of a mod from its repository: read the mod under ``DIR`` (default ``mods/ID``) on ``BRANCH`` (default the repository's default branch) — or from a local checkout — and pin the **commit** it was read at; no tag is involved (Requirement 8.9). Follow with ``build --sign`` and ``readme`` |
| ``readme REPO [--check]`` | rewrite the README's mod table (between ``<!-- mods:begin -->`` / ``<!-- mods:end -->``) from ``index.json``; ``--check`` fails when the committed table is stale (Requirement 8.10) |

Exit status: 0 ok, 1 a check failed, 2 usage or an unreadable input.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from floofy_core.compat import CompatCache
from floofy_core.signing import generate_keypair, load_private_key, load_public_key_record, sign_document
from floofy_core.sigverify import SignatureError, verify_detached

from .build import build_index, index_matches, write_index
from .compat_merge import CompatMergeError, load_compat, merge_row, override_cell
from .record import RecordError, make_record
from .repo import RegistryRepo, RepoError
from .submission import validate_submission

__all__ = ["main"]


def _say(message: str) -> None:
    print(message)


def _fail(message: str, code: int = 1) -> int:
    print(f"error: {message}", file=sys.stderr)
    return code


def _write_json(path: Path, document: Any) -> None:
    Path(path).write_text(json.dumps(document, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


# --- commands ---------------------------------------------------------------------------------------------------


def cmd_keygen(args: argparse.Namespace) -> int:
    out = Path(args.out)
    if out.exists() and not args.force:
        return _fail(f"{out} exists (use --force to overwrite)")
    pair = generate_keypair(args.comment or "")
    pair.save_private(out)
    public = pair.save_public(out.with_name(out.name + ".pub.json"))
    _say(f"key id {pair.key_id}\nprivate key: {out} (mode 0600 — keep it out of every repository)\npublic record: {public}")
    return 0


def cmd_build(args: argparse.Namespace) -> int:
    try:
        repo = RegistryRepo.load(Path(args.repo))
    except RepoError as exc:
        return _fail(str(exc), 2)
    compat = CompatCache.load(Path(args.compat)) if args.compat else None
    result = build_index(repo, compat=compat, floofycrew_version=args.floofycrew, resolve_links=args.resolve_links)
    for note in result.notes:
        _say(f"note: {note}")
    if not result.ok:
        for problem in result.problems:
            print(f"error: {problem}", file=sys.stderr)
        return 1
    if args.check:
        ok, message = index_matches(Path(args.out) if args.out else repo.index_path, result)
        _say(message)
        if ok and args.app_registry:
            from .app_registry import app_registry_matches  # noqa: PLC0415

            ok, message = app_registry_matches(repo, result.index)
            _say(message)
        return 0 if ok else 1
    target = write_index(repo, result, out=Path(args.out) if args.out else None)
    _say(f"wrote {target}: {len(result.index['mods'])} mod(s), {sum(len(m['versions']) for m in result.index['mods'])} version(s)")
    if args.app_registry:
        from .app_registry import write_app_registry  # noqa: PLC0415

        path, rows = write_app_registry(repo, result.index)
        _say(f"wrote {path}: {len(rows)} app row(s)")
    if args.sign:
        try:
            pair = load_private_key(Path(args.sign))
        except SignatureError as exc:
            return _fail(str(exc), 2)
        if repo.key_id and pair.key_id != repo.key_id:
            return _fail(f"registry.json expects key {repo.key_id}, but {args.sign} is key {pair.key_id}")
        signature = sign_document(target, pair)
        _say(f"signed with key {pair.key_id} -> {signature}")
    return 0


def cmd_sign(args: argparse.Namespace) -> int:
    try:
        pair = load_private_key(Path(args.key))
        target = sign_document(Path(args.payload), pair, out=Path(args.out) if args.out else None)
    except (SignatureError, OSError) as exc:
        return _fail(str(exc), 2)
    _say(f"signed {args.payload} with key {pair.key_id} -> {target}")
    return 0


def cmd_verify(args: argparse.Namespace) -> int:
    payload = Path(args.payload)
    signature = Path(args.sig) if args.sig else payload.with_name(payload.name + ".sig")
    keys: dict[str, bytes] = {}
    try:
        for record in args.public_key or []:
            kid, public = load_public_key_record(Path(record))
            keys[kid] = public
    except SignatureError as exc:
        return _fail(str(exc), 2)
    if args.edition_keys:
        from floofy_core.editions import registry_keys  # noqa: PLC0415

        keys.update(registry_keys())
    if not keys:
        return _fail("no public keys given (--public-key RECORD or --edition-keys)", 2)
    try:
        sig_bytes: bytes | None = signature.read_bytes()
    except FileNotFoundError:
        sig_bytes = None
    try:
        verdict = verify_detached(payload.read_bytes(), sig_bytes, keys, expected_key_id=args.key_id)
    except OSError as exc:
        return _fail(str(exc), 2)
    _say(f"{payload}: {verdict.status}" + (f" (key {verdict.key_id})" if verdict.key_id else "") + f" — {verdict.detail}")
    return 0 if verdict.verified else 1


def cmd_validate_submission(args: argparse.Namespace) -> int:
    terms = tuple(t.strip() for t in args.terms.split(",") if t.strip()) if args.terms else None
    allow: set[str] | None = None
    if args.allowlist:
        try:
            allow = {line.split("#", 1)[0].strip() for line in Path(args.allowlist).read_text(encoding="utf-8").splitlines() if line.split("#", 1)[0].strip()}
        except OSError as exc:
            return _fail(str(exc), 2)
    kwargs: dict[str, Any] = {"registry": Path(args.registry) if args.registry else None, "tag": args.tag, "allowlist": allow, "record": args.record}
    if terms:
        kwargs["terms"] = terms
    if args.mod is None and args.record is None:
        return _fail("give the mod (a directory or archive) or --record <id>@<version>", 2)
    report = validate_submission(Path(args.mod) if args.mod else None, **kwargs)
    _say(json.dumps(report.to_dict(), indent=2) if args.json else report.format())
    return 0 if report.ok else 1


def cmd_compat_merge(args: argparse.Namespace) -> int:
    try:
        document = load_compat(Path(args.compat))
        if args.override:
            if not (args.reason and args.by and args.edition and args.channel and args.host_version):
                return _fail("--override needs --edition, --channel, --host-version, --reason and --by", 2)
            cell, _, verdict = args.override.partition("=")
            merged = override_cell(document, edition=args.edition, channel=args.channel, host_version=args.host_version, cell=cell, verdict=verdict, reason=args.reason, by=args.by)
        else:
            if not args.row:
                return _fail("a ROW file is required unless --override is given", 2)
            row = json.loads(Path(args.row).read_text(encoding="utf-8"))
            if not isinstance(row, dict):
                return _fail(f"{args.row}: not a JSON object", 2)
            merged = merge_row(document, row, source=args.source)
    except (CompatMergeError, OSError, ValueError) as exc:
        return _fail(str(exc), 1 if isinstance(exc, CompatMergeError) else 2)
    _write_json(Path(args.compat), merged)
    _say(f"wrote {args.compat}: {len(merged['rows'])} row(s)")
    if args.sign:
        try:
            pair = load_private_key(Path(args.sign))
        except SignatureError as exc:
            return _fail(str(exc), 2)
        _say(f"signed with key {pair.key_id} -> {sign_document(Path(args.compat), pair)}")
    return 0


def cmd_app_registry(args: argparse.Namespace) -> int:
    from .app_registry import write_app_registry  # noqa: PLC0415

    try:
        repo = RegistryRepo.load(Path(args.repo))
        index = json.loads(repo.index_path.read_text(encoding="utf-8"))
    except (RepoError, OSError, ValueError) as exc:
        return _fail(str(exc), 2)
    path, rows = write_app_registry(repo, index, out=Path(args.out) if args.out else None)
    _say(f"wrote {path}: {len(rows)} app row(s)")
    return 0


def cmd_record(args: argparse.Namespace) -> int:
    try:
        repo = RegistryRepo.load(Path(args.repo))
        result = make_record(repo, args.id, repo_url=args.repo_url, path=args.path, ref=args.ref, source=Path(args.source) if args.source else None, source_ref=args.source_ref, channel=args.channel, contact=args.contact, refresh=args.refresh)
    except (RepoError, RecordError) as exc:
        return _fail(str(exc))
    if args.json:
        _say(json.dumps(result.to_dict(), indent=2))
        return 0
    verb = "recorded" if result.created else "re-pinned"
    _say(f"{verb} {result.mod_id}@{result.version}: {result.repo} {result.path} at {result.ref or 'the default branch'} → commit {result.commit[:12]} ({result.files} file(s), manifest {result.manifest_sha256[:12]}…)")
    for path in result.written:
        _say(f"  wrote {path}")
    _say("next: registry_tools build <repo> --sign <key> [--app-registry] && registry_tools readme <repo>")
    return 0


def cmd_readme(args: argparse.Namespace) -> int:
    from .readme import readme_matches, update_readme  # noqa: PLC0415

    try:
        repo = RegistryRepo.load(Path(args.repo))
        json.loads(repo.index_path.read_text(encoding="utf-8"))
    except (RepoError, OSError, ValueError) as exc:
        return _fail(str(exc), 2)
    if args.check:
        ok, message = readme_matches(repo)
        _say(message)
        return 0 if ok else 1
    changed, path = update_readme(repo)
    _say(f"{'rewrote' if changed else 'unchanged'} the mod table in {path}")
    return 0


# --- parser -----------------------------------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m registry_tools", description="build, sign and validate FloofyCrew mod registries")
    sub = parser.add_subparsers(dest="command", required=True)

    keygen = sub.add_parser("keygen", help="generate an Ed25519 key pair")
    keygen.add_argument("--out", required=True, help="private key file to write (KEY.pub.json is written beside it)")
    keygen.add_argument("--comment", default="", help="free text stored with both records")
    keygen.add_argument("--force", action="store_true")
    keygen.set_defaults(func=cmd_keygen)

    build = sub.add_parser("build", help="build index.json from mods/** (and optionally sign it)")
    build.add_argument("repo", help="the registry repository root (holds registry.json)")
    build.add_argument("--out", default=None, help="write the index here instead of REPO/index.json")
    build.add_argument("--compat", default=None, help="fold the cells of this compat.json in (default REPO/compat.json)")
    build.add_argument("--sign", default=None, metavar="KEY", help="sign the written index with this private key")
    build.add_argument("--app-registry", action="store_true", help="also emit the KiroCrew-compatible app-registry.json")
    build.add_argument("--check", action="store_true", help="do not write; fail when the committed index differs from the rebuilt one")
    build.add_argument("--floofycrew", default=None, help="the FloofyCrew release to stamp into the index")
    build.add_argument("--resolve-links", action="store_true", help="read every unresolved link record at its branch (default branch unless link.ref) and pin commit, manifestSha256 and files[] into its release.json")
    build.set_defaults(func=cmd_build)

    sign = sub.add_parser("sign", help="write the detached signature of a JSON document")
    sign.add_argument("payload")
    sign.add_argument("--key", required=True)
    sign.add_argument("--out", default=None)
    sign.set_defaults(func=cmd_sign)

    verify = sub.add_parser("verify", help="verify a detached signature")
    verify.add_argument("payload")
    verify.add_argument("--sig", default=None, help="signature file (default PAYLOAD.sig)")
    verify.add_argument("--public-key", action="append", default=[], metavar="RECORD", help="a public key record file (repeatable)")
    verify.add_argument("--key-id", default=None, help="require this key id")
    verify.add_argument("--edition-keys", action="store_true", help="also trust the keys pinned by the installed edition adapters")
    verify.set_defaults(func=cmd_verify)

    submission = sub.add_parser("validate-submission", help="the CI gate for a registry pull request")
    submission.add_argument("mod", nargs="?", default=None, help="the mod directory or release archive (omit with --record)")
    submission.add_argument("--record", default=None, metavar="ID@VERSION", help="validate a link record of --registry: fetch link.repo at the pinned commit and compare with the record")
    submission.add_argument("--registry", default=None, help="the registry repository the submission targets")
    submission.add_argument("--tag", default=None, help="the release tag the assets were published under")
    submission.add_argument("--allowlist", default=None, help="masquerade allowlist file (in addition to REPO/masquerade-allow.txt)")
    submission.add_argument("--terms", default=None, help="comma-separated protected terms (default kirocrew,kiro,crew,amazon,aws)")
    submission.add_argument("--json", action="store_true")
    submission.set_defaults(func=cmd_validate_submission)

    compat = sub.add_parser("compat-merge", help="add/replace a compat.json row from a Forge run file, or record a human override")
    compat.add_argument("compat", help="the compat.json to update (created when missing)")
    compat.add_argument("row", nargs="?", default=None, help="the run file (a row: edition, channel, hostVersion, framework, mods, run, checkedAt)")
    compat.add_argument("--source", default=None, help="the registry label to stamp into the document")
    compat.add_argument("--sign", default=None, metavar="KEY")
    compat.add_argument("--override", default=None, metavar="CELL=VERDICT", help="record a human override instead of merging a row")
    compat.add_argument("--edition", default=None)
    compat.add_argument("--channel", default=None)
    compat.add_argument("--host-version", default=None)
    compat.add_argument("--reason", default=None)
    compat.add_argument("--by", default=None)
    compat.set_defaults(func=cmd_compat_merge)

    app_registry = sub.add_parser("app-registry", help="emit the KiroCrew-compatible app-registry.json from index.json")
    app_registry.add_argument("repo")
    app_registry.add_argument("--out", default=None)
    app_registry.set_defaults(func=cmd_app_registry)

    record = sub.add_parser("record", help="make or refresh a mod's link record from its repository (pinned at the commit read; no tag)")
    record.add_argument("repo", help="the registry repository root")
    record.add_argument("id", help="the mod id")
    record.add_argument("--repo", dest="repo_url", default=None, help="the mod's repository (default: mods/<id>/mod.json repo, else registry.json floofycrew)")
    record.add_argument("--path", default=None, help="the mod's directory inside the repository (default mods/<id>)")
    record.add_argument("--ref", default=None, help="the branch to read (default: the branch an earlier record names, else the repository's default branch)")
    record.add_argument("--source", default=None, metavar="CHECKOUT", help="read a local checkout instead of cloning (its committed tree at --source-ref)")
    record.add_argument("--source-ref", default="HEAD", metavar="COMMITISH", help="with --source: the commit, branch or HEAD to pin (default HEAD)")
    record.add_argument("--channel", default="stable")
    record.add_argument("--contact", default=None, help="who answers for the mod (default: the manifest's first author, for a new mod.json)")
    record.add_argument("--refresh", action="store_true", help="re-pin an already recorded version at the commit read now")
    record.add_argument("--json", action="store_true")
    record.set_defaults(func=cmd_record)

    readme = sub.add_parser("readme", help="regenerate the README mod table from index.json")
    readme.add_argument("repo")
    readme.add_argument("--check", action="store_true", help="do not write; fail when the committed table differs from the regenerated one")
    readme.set_defaults(func=cmd_readme)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.func(args))
