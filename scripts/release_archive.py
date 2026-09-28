"""Safe archive extraction and non-secret release manifest operations."""

import argparse
import hashlib
import json
import re
import shutil
import sys
import tarfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath


FORMAT_VERSION = 1
MAX_ARCHIVE_MEMBERS = 20_000
MAX_ARCHIVE_BYTES = 512 * 1024 * 1024
RELEASE_ID_PATTERN = re.compile(r"^[0-9]{8}_[0-9]{6}_[0-9a-f]{7,12}$")
GIT_SHA_PATTERN = re.compile(r"^[0-9a-f]{40,64}$")
ARCHIVE_SHA_PATTERN = re.compile(r"^[0-9a-f]{64}$")
MANIFEST_FIELDS = {
    "release_id",
    "full_git_sha",
    "short_git_sha",
    "created_at_utc",
    "deployment_format_version",
    "archive_sha256",
}


class ReleaseArchiveError(RuntimeError):
    """Raised for unsafe archives or invalid release metadata."""


def validate_release_identity(release_id: str, full_git_sha: str) -> str:
    if not RELEASE_ID_PATTERN.fullmatch(release_id):
        raise ReleaseArchiveError("Invalid release ID.")
    if not GIT_SHA_PATTERN.fullmatch(full_git_sha):
        raise ReleaseArchiveError("Invalid full Git revision.")
    short_git_sha = release_id.rsplit("_", 1)[1]
    if not full_git_sha.startswith(short_git_sha):
        raise ReleaseArchiveError("Release ID does not match the Git revision.")
    return short_git_sha


def _safe_member_path(name: str) -> PurePosixPath:
    if not name or name.startswith("/") or "\\" in name:
        raise ReleaseArchiveError("Archive contains an unsafe path.")
    path = PurePosixPath(name)
    if path.is_absolute() or ".." in path.parts:
        raise ReleaseArchiveError("Archive contains an unsafe path.")
    cleaned = PurePosixPath(*[part for part in path.parts if part not in {"", "."}])
    if not cleaned.parts:
        raise ReleaseArchiveError("Archive contains an empty path.")
    return cleaned


def safe_extract(archive_path: Path, destination: Path) -> None:
    """Extract regular files and directories while rejecting link/path escapes."""
    destination = destination.resolve()
    destination.mkdir(parents=True, exist_ok=True)
    seen: set[PurePosixPath] = set()
    total_size = 0

    try:
        archive = tarfile.open(archive_path, mode="r:gz")
    except (OSError, tarfile.TarError) as exc:
        raise ReleaseArchiveError("Release archive is unreadable.") from exc

    with archive:
        members = archive.getmembers()
        if len(members) > MAX_ARCHIVE_MEMBERS:
            raise ReleaseArchiveError("Release archive contains too many entries.")

        validated: list[tuple[tarfile.TarInfo, PurePosixPath]] = []
        for member in members:
            relative = _safe_member_path(member.name)
            if relative in seen:
                raise ReleaseArchiveError("Release archive contains duplicate paths.")
            seen.add(relative)
            if not (member.isdir() or member.isfile()):
                raise ReleaseArchiveError(
                    "Release archive may contain only directories and regular files."
                )
            if member.isfile():
                total_size += member.size
                if total_size > MAX_ARCHIVE_BYTES:
                    raise ReleaseArchiveError("Release archive is too large.")
            target = (destination / Path(*relative.parts)).resolve()
            try:
                target.relative_to(destination)
            except ValueError as exc:
                raise ReleaseArchiveError("Archive path escapes its destination.") from exc
            validated.append((member, relative))

        for member, relative in validated:
            target = destination / Path(*relative.parts)
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
                target.chmod(0o755)
                continue

            target.parent.mkdir(parents=True, exist_ok=True)
            source = archive.extractfile(member)
            if source is None:
                raise ReleaseArchiveError("Unable to read an archive member.")
            with source, target.open("xb") as output:
                shutil.copyfileobj(source, output)
            target.chmod(0o755 if member.mode & 0o111 else 0o644)


def archive_sha256(archive_path: Path) -> str:
    digest = hashlib.sha256()
    with archive_path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_manifest(
    output_path: Path,
    *,
    release_id: str,
    full_git_sha: str,
    archive_path: Path,
) -> dict:
    short_git_sha = validate_release_identity(release_id, full_git_sha)
    manifest = {
        "release_id": release_id,
        "full_git_sha": full_git_sha,
        "short_git_sha": short_git_sha,
        "created_at_utc": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "deployment_format_version": FORMAT_VERSION,
        "archive_sha256": archive_sha256(archive_path),
    }
    output_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    output_path.chmod(0o644)
    return manifest


def load_manifest(manifest_path: Path, expected_release_id: str | None = None) -> dict:
    if not manifest_path.is_file() or manifest_path.is_symlink():
        raise ReleaseArchiveError("Release manifest is missing or is not a regular file.")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ReleaseArchiveError("Release manifest is malformed.") from exc
    if not isinstance(manifest, dict) or set(manifest) != MANIFEST_FIELDS:
        raise ReleaseArchiveError("Release manifest has an unexpected schema.")

    release_id = manifest.get("release_id")
    full_git_sha = manifest.get("full_git_sha")
    short_git_sha = manifest.get("short_git_sha")
    if not all(isinstance(value, str) for value in (release_id, full_git_sha, short_git_sha)):
        raise ReleaseArchiveError("Release manifest identity fields are invalid.")
    validate_release_identity(release_id, full_git_sha)
    if short_git_sha != release_id.rsplit("_", 1)[1]:
        raise ReleaseArchiveError("Release manifest revision fields do not match.")
    if expected_release_id is not None and release_id != expected_release_id:
        raise ReleaseArchiveError("Release manifest does not match its directory.")
    if manifest.get("deployment_format_version") != FORMAT_VERSION:
        raise ReleaseArchiveError("Unsupported release manifest version.")
    if not ARCHIVE_SHA_PATTERN.fullmatch(str(manifest.get("archive_sha256", ""))):
        raise ReleaseArchiveError("Release manifest archive checksum is invalid.")
    created_at = manifest.get("created_at_utc")
    if not isinstance(created_at, str) or not created_at.endswith("Z"):
        raise ReleaseArchiveError("Release manifest timestamp is invalid.")
    try:
        datetime.fromisoformat(created_at.removesuffix("Z") + "+00:00")
    except ValueError as exc:
        raise ReleaseArchiveError("Release manifest timestamp is invalid.") from exc
    return manifest


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    extract = commands.add_parser("safe-extract")
    extract.add_argument("archive", type=Path)
    extract.add_argument("destination", type=Path)

    write = commands.add_parser("write-manifest")
    write.add_argument("output", type=Path)
    write.add_argument("release_id")
    write.add_argument("full_git_sha")
    write.add_argument("archive", type=Path)

    validate = commands.add_parser("validate-manifest")
    validate.add_argument("manifest", type=Path)
    validate.add_argument("expected_release_id")

    value = commands.add_parser("manifest-value")
    value.add_argument("manifest", type=Path)
    value.add_argument("expected_release_id")
    value.add_argument("field", choices=sorted(MANIFEST_FIELDS))
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        if args.command == "safe-extract":
            safe_extract(args.archive, args.destination)
        elif args.command == "write-manifest":
            write_manifest(
                args.output,
                release_id=args.release_id,
                full_git_sha=args.full_git_sha,
                archive_path=args.archive,
            )
        elif args.command == "validate-manifest":
            load_manifest(args.manifest, args.expected_release_id)
        elif args.command == "manifest-value":
            manifest = load_manifest(args.manifest, args.expected_release_id)
            print(manifest[args.field])
    except ReleaseArchiveError as exc:
        print(f"Release validation failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
