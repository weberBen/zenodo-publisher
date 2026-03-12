"""Main release logic."""

import json
import shutil
import tempfile
from pathlib import Path

from ..latex_build import compile
from ..git_operations import (
    check_on_main_branch,
    check_up_to_date,
    is_latest_commit_released,
    check_tag_validity,
    create_github_release,
    verify_release_on_latest_commit,
    get_last_commit_info,
    get_release_asset_digest,
    upload_release_asset,
    archive_zip_project,
)
from ..zenodo_operations import ZenodoPublisher, ZenodoError
from ..archive_operation import (
    ArchivedFile,
    compute_file_hash,
    compute_hashes,
    format_hash_info,
    generate_manifest,
    manifest_to_file,
    process_project_archive,
)
from ..file_utils import persist_files
from ..gpg_operations import gpg_sign_file, prompt_gpg_key
from ..config.generated_files import FileEntry, FileEntryKind
from ..config.signing import SignMode
from .. import output, prompts
from ..errors import PipelineError
from ._common import setup_pipeline

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def ellipse_hash(hash_str, visible_char=8):
    hash_str = hash_str.split(":")[-1]
    return f"{hash_str[:visible_char]}...{hash_str[-visible_char:]}"


# ---------------------------------------------------------------------------
# Steps
# ---------------------------------------------------------------------------

def _step_git_check(config):
    """Check branch, remote sync, and local modifications."""
    output.step("🔍 Checking git repository status...")
    check_on_main_branch(config.project_root, config.main_branch)
    output.step_ok("On {branch} branch", branch=config.main_branch, name="git.branch_check")
    check_up_to_date(config.project_root, config.main_branch)
    output.step_ok("Project is up to date with git repo", name="git.up_to_date")


def _step_release(config) -> str:
    """Check or create a GitHub release. Returns the tag name."""
    is_released, latest_release = is_latest_commit_released(config.project_root)

    if is_released:
        tag_name = latest_release["tagName"]
        output.info_ok("Latest commit already has a release: {tag_name}", tag_name=tag_name, name="release.existing")
        output.info_ok("Nothing to do for release.", name="release.noop")
        output.step_ok("Project is up to date with git release", name="release.up_to_date")
        output.data("tag_name", tag_name)
        return tag_name

    output.step("📋 Current release status:")
    if latest_release:
        output.detail("Last release: {tag}", tag=latest_release['tagName'], name="release.last_tag")
        if latest_release.get("name"):
            output.detail("Title: {title}", title=latest_release['name'], name="release.last_title")
        if latest_release.get("body"):
            body = latest_release["body"]
            preview = body[:100] + "..." if len(body) > 100 else body
            output.detail("Notes: {notes}", notes=preview, name="release.last_notes")
    else:
        output.detail("No releases found (this will be the first release)")

    output.step("📝 Creating new release...")

    while True:
        result = prompts.enter_tag.ask("Enter new tag name")
        if result.is_accept:
            new_tag = result.value
            break
        output.warn("Tag name cannot be empty")

    result = prompts.release_title.ask(
        f"Enter release title (press Enter to use '{new_tag}')",
    )
    release_title = result.value if result.value else new_tag
    if release_title == new_tag:
        output.detail("Using default title: {title}", title=release_title, name="release.default_title")

    result = prompts.release_notes.ask("Enter release notes (press Enter to skip)")
    release_notes = result.value
    if not release_notes:
        release_notes = ""
        output.detail("No release notes provided")

    output.step("🔍 Verifying tag validity...")
    check_tag_validity(config.project_root, new_tag, config.main_branch)
    create_github_release(config.project_root, new_tag, release_title, release_notes)
    output.step_ok("Release {tag} created successfully!", tag=new_tag, name="release.created")
    output.data("tag_name", new_tag)
    return new_tag


def _step_commit_info(config, tag_name):
    output.step("Retrieve commit info")
    commit_env = get_last_commit_info(config.project_root, tag_name=tag_name)
    output.info_ok("Commit SHA: {sha}", sha=commit_env['ZP_COMMIT_SHA'], name="commit.sha")
    output.info_ok("Commit timestamp: {timestamp}", timestamp=commit_env['ZP_COMMIT_DATE_EPOCH'], name="commit.timestamp")
    output.info_ok("Commit subject: {subject}", subject=commit_env['ZP_COMMIT_SUBJECT'], name="commit.subject")
    output.info_ok("Author: {author_name} <{author_email}>",
                   author_name=commit_env['ZP_COMMIT_AUTHOR_NAME'],
                   author_email=commit_env['ZP_COMMIT_AUTHOR_EMAIL'], name="commit.author")
    output.info_ok("Committer: {committer_name} <{committer_email}>",
                   committer_name=commit_env['ZP_COMMIT_COMMITTER_NAME'],
                   committer_email=commit_env['ZP_COMMIT_COMMITTER_EMAIL'], name="commit.committer")
    output.info_ok("Branch: {branch}", branch=commit_env['ZP_BRANCH'], name="commit.branch")
    output.info_ok("Origin: {origin}", origin=commit_env['ZP_ORIGIN_URL'], name="commit.origin")
    output.data("commit_env", commit_env)
    output.step_ok("", silent=True)
    return commit_env


def _step_project_name(config, tag_name, commit_env):
    output.step("Resolving project name")
    config.generate_project_name({
        "tag_name": tag_name,
        "sha_commit": commit_env["ZP_COMMIT_SHA"],
    })
    output.data("project_name", config.project_name)
    output.step_ok("Formatted project name: {project_name}", project_name=config.project_name, name="project.name")


def _step_compile(config, env_vars=None):
    """Compile project via make (with user prompt)."""
    if not config.compile_enabled:
        output.step_warn("Skipping project compilation (see config file)")
        return

    if not prompts.confirm_build.ask("Start building project ?").is_accept:
        raise PipelineError("Build aborted by user.", name="build_aborted")

    output.step("📋 Starting build process...")
    compile(config.compile_dir, config.make_args, env_vars=env_vars)
    output.step_ok("Compilation ended")


# ---------------------------------------------------------------------------
# Step 7: Resolve generated files
# ---------------------------------------------------------------------------

def _step_resolve_generated_files(config) -> list[FileEntry]:
    """Resolve pattern templates and glob for matches."""
    output.step("Resolving generated files...")
    for entry in config.generated_files:
        if entry.kind == FileEntryKind.PATTERN:
            pattern = entry.pattern
            # Resolve {project_name} (available after step 4)
            if "{project_name}" in pattern:
                pattern = pattern.replace("{project_name}", config.project_name)

            resolved_path = Path(pattern)
            if not resolved_path.is_absolute():
                resolved_path = (config.project_root or Path.cwd()) / resolved_path

            parent = resolved_path.parent
            glob_pat = resolved_path.name
            matches = sorted(parent.glob(glob_pat))

            if not matches:
                raise PipelineError(
                    f"Pattern '{entry.pattern_template}' (generated_files.{entry.key}) "
                    f"matched no files",
                    name=f"no_match.{entry.key}",
                )
            entry.resolved_paths = matches
            for m in matches:
                output.detail("{key}: {filename}", key=entry.key, filename=m.name, name="files.resolved")

    output.step_ok("Generated files resolved")
    return config.generated_files


# ---------------------------------------------------------------------------
# Step 8: Archive
# ---------------------------------------------------------------------------

def _step_archive(config, tag_name, output_dir, file_entries) -> list[ArchivedFile]:
    """Create archives: copy/rename generated files, create project ZIP."""
    output.step("Archiving files...")
    archived_files: list[ArchivedFile] = []

    for entry in file_entries:
        if entry.kind == FileEntryKind.PATTERN:
            for src_path in entry.resolved_paths:
                if entry.rename:
                    ext = src_path.suffix
                    dst = output_dir / f"{config.project_name}{ext}"
                else:
                    dst = output_dir / src_path.name
                shutil.copy2(src_path, dst)
                archived_files.append(ArchivedFile(
                    file_path=dst,
                    config_key=entry.key,
                    filename=dst.stem,
                    extension=dst.suffix.lstrip("."),
                    kind="generated",
                    is_preview=(dst.suffix.lstrip(".") == "pdf"),
                    persist=entry.archive,
                    has_signature=entry.effective_sign(config.signing.sign),
                    publishers=entry.publishers,
                ))
                output.detail("{src} → {dst}", src=src_path.name, dst=dst.name, name="archive.copy")

        elif entry.kind == FileEntryKind.PROJECT:
            result = archive_zip_project(
                config.project_root, tag_name,
                config.project_name, output_dir,
            )
            # Post-process: tree hashes + optional TAR conversion
            hash_algos = list(config.hash_algorithms or [])
            from ..config.transform_common import TREE_ALGORITHMS
            tree_algos = [a for a in hash_algos if a in TREE_ALGORITHMS]

            final_path, final_format, tree_hashes = process_project_archive(
                result.file_path, result.archive_name,
                tree_algos=tree_algos, archive_format=config.archive_format,
                tar_args=config.archive_tar_extra_args,
                gzip_args=config.archive_gzip_extra_args,
            )
            # Pre-format tree hashes into hashes dict (computed during
            # extraction, can't recompute from the packed archive file)
            pre_hashes = {
                algo: format_hash_info(algo, value)
                for algo, value in tree_hashes.items()
            }
            af = ArchivedFile(
                file_path=final_path,
                config_key=entry.key,
                filename=result.archive_name,
                extension=final_format,
                kind="project",
                persist=entry.archive,
                has_signature=entry.effective_sign(config.signing.sign),
                publishers=entry.publishers,
                hashes=pre_hashes,
            )
            archived_files.append(af)
            output.detail("project archive: {filename}", filename=final_path.name, name="archive.project")

        # MANIFEST kind is handled in step 9

    output.step_ok("Files archived")
    return archived_files


# ---------------------------------------------------------------------------
# Step 9: Manifest
# ---------------------------------------------------------------------------

def _load_manifest_metadata(config, metadata_fields: list[str]) -> dict | None:
    """Extract metadata fields from .zenodo.json for inclusion in manifest."""
    if not metadata_fields:
        return None

    zenodo_json = config.project_root / ".zenodo.json"
    if not zenodo_json.exists():
        return None

    with open(zenodo_json) as f:
        data = json.load(f)
    source = data.get("metadata", data)

    if metadata_fields == ["*"]:
        return source

    metadata = {}
    for field_name in metadata_fields:
        if field_name in source:
            metadata[field_name] = source[field_name]
    return metadata or None


def _filter_manifest_files(archived_files: list[ArchivedFile],
                           file_refs: list[str]) -> list[ArchivedFile]:
    """Filter archived_files to only those referenced by manifest.files."""
    if not file_refs:
        return [af for af in archived_files if not af.is_signature]

    included = []
    for ref in file_refs:
        if ref.endswith("_sig"):
            base_key = ref.removesuffix("_sig")
            matches = [af for af in archived_files
                       if af.is_signature and af.signed_file_key == base_key]
        else:
            matches = [af for af in archived_files
                       if af.config_key == ref and not af.is_signature]
        included.extend(matches)
    return included


def _step_manifest(config, tag_name, archived_files, commit_env, output_dir):
    """Generate manifest if a manifest FileEntry exists."""
    manifest_entry_cfg = next(
        (e for e in config.generated_files if e.kind == FileEntryKind.MANIFEST),
        None,
    )
    if manifest_entry_cfg is None:
        return

    output.step("📋 Generating manifest...")
    mc = manifest_entry_cfg.manifest_config

    included = _filter_manifest_files(archived_files, mc.files if mc else [])
    metadata = _load_manifest_metadata(config, mc.zenodo_metadata if mc else [])

    manifest_dict = generate_manifest(
        included, tag_name, commit_env,
        commit_fields=mc.commit_info if mc else None,
        metadata=metadata,
    )
    manifest_path = manifest_to_file(config, manifest_dict, output_dir)
    output.detail("Manifest: {path}", path=str(manifest_path), name="manifest.path")

    archived_files.append(ArchivedFile(
        file_path=manifest_path,
        config_key="manifest",
        filename="manifest",
        extension="json",
        kind="manifest",
        persist=manifest_entry_cfg.archive,
        has_signature=manifest_entry_cfg.effective_sign(config.signing.sign),
        publishers=manifest_entry_cfg.publishers,
    ))
    output.step_ok("Manifest generated")


# ---------------------------------------------------------------------------
# Step 10: Compute hashes
# ---------------------------------------------------------------------------

def _step_compute_hashes(config, archived_files):
    """Compute hashes for all entries."""
    output.step("Computing hashes...")
    algos = list(config.hash_algorithms or [])
    if config.sign_hash_algo not in algos:
        algos.append(config.sign_hash_algo)
    compute_hashes(archived_files, algos)

    for af in archived_files:
        output.detail("{filename}", filename=af.file_path.name, name="hash.file")
        for algo, h in af.hashes.items():
            output.detail("  {algo}: {hash}", algo=algo, hash=h['value'], name="hash.value")
    output.data("file_hashes", {
        af.file_path.name: {algo: h["value"] for algo, h in af.hashes.items()}
        for af in archived_files
    })
    output.step_ok("Hashes computed")


# ---------------------------------------------------------------------------
# Step 11: Sign
# ---------------------------------------------------------------------------

def _step_sign(config, archived_files, output_dir):
    """Sign all entries that have has_signature=True."""
    to_sign = [af for af in archived_files if af.has_signature]
    if not to_sign:
        output.step_ok("Signing skipped", silent=True)
        return

    output.step("🔏 Signing files...")
    prompt_gpg_key(config.gpg_uid, config.gpg_extra_args)

    sig_ext = "asc" if "--armor" in config.gpg_extra_args else "sig"

    for af in to_sign:
        effective_mode = _get_effective_sign_mode(af, config)

        if effective_mode == SignMode.FILE:
            sig_path = gpg_sign_file(
                af.file_path, output_dir,
                gpg_uid=config.gpg_uid,
                extra_args=config.gpg_extra_args,
            )
        elif effective_mode == SignMode.FILE_HASH:
            hash_value = af.hashes[config.sign_hash_algo]["formatted_value"]
            hash_file = output_dir / f"{af.file_path.name}.{config.sign_hash_algo}"
            hash_file.write_text(hash_value, encoding="ascii")
            sig_path = gpg_sign_file(
                hash_file, output_dir,
                gpg_uid=config.gpg_uid,
                extra_args=config.gpg_extra_args,
            )
            hash_file.unlink(missing_ok=True)

        sig_af = ArchivedFile(
            file_path=sig_path,
            config_key=f"{af.config_key}_sig",
            filename=sig_path.stem,
            extension=sig_ext,
            kind="signature",
            is_signature=True,
            persist=af.persist,
            signed_file_key=af.config_key,
            publishers=af.publishers,
        )
        compute_hashes([sig_af], config.hash_algorithms)
        archived_files.append(sig_af)

    output.step_ok("Files signed")


def _get_effective_sign_mode(af: ArchivedFile, config) -> SignMode:
    """Get the effective sign mode for an entry."""
    for fe in config.generated_files:
        if fe.key == af.config_key:
            return fe.effective_sign_mode(config.signing.sign_mode)
    return config.signing.sign_mode


# ---------------------------------------------------------------------------
# Step 12: Compute identifiers
# ---------------------------------------------------------------------------

def _step_compute_identifiers(config, archived_files):
    """Compute alternate identifiers for entries with identifier config."""
    has_identifiers = False
    for fe in config.generated_files:
        if fe.identifier is None:
            continue

        ic = fe.identifier
        if ic.source == "file":
            target = next(
                (af for af in archived_files if af.config_key == fe.key), None,
            )
        elif ic.source == "sig_file":
            target = next(
                (af for af in archived_files if af.config_key == f"{fe.key}_sig"), None,
            )
        else:
            continue

        if target is None:
            output.warn("Identifier source not found for '{key}'", key=fe.key, name="identifier.missing")
            continue

        hash_val = target.hashes.get(config.sign_hash_algo)
        if hash_val is None:
            output.warn("Hash {algo} not found for identifier '{key}'", algo=config.sign_hash_algo, key=fe.key, name="identifier.hash_missing")
            continue

        formatted = hash_val['formatted_value']
        identifier_value = f"{ic.prefix}{formatted}" if ic.prefix else formatted
        target.identifier_value = identifier_value

        if not has_identifiers:
            output.step("Computing identifiers...")
            has_identifiers = True
        output.detail("Identifier ({key}): {value}", key=fe.key, value=identifier_value, name="identifier.computed")

    if has_identifiers:
        output.step_ok("Identifiers computed")


# ---------------------------------------------------------------------------
# Step 13: Publish (per-file destination routing)
# ---------------------------------------------------------------------------

def _step_publish(config, tag_name, archived_files) -> dict | None:
    """Route each file to its configured destinations."""
    record_info = None

    zenodo_files = _files_for_destination(archived_files, "zenodo")
    github_files = _files_for_destination(archived_files, "github")

    if zenodo_files and config.has_zenodo_config():
        record_info = _publish_zenodo(
            config, tag_name, zenodo_files, archived_files,
        )

    if github_files:
        _publish_github(config, tag_name, github_files)

    return record_info


def _files_for_destination(archived_files: list[ArchivedFile],
                           destination: str) -> list[ArchivedFile]:
    """Get files destined for a specific publisher."""
    result = []
    for af in archived_files:
        if af.publishers is None:
            continue
        if af.is_signature:
            if destination in af.publishers.sig_destination:
                result.append(af)
        else:
            if destination in af.publishers.file_destination:
                result.append(af)
    return result


def _publish_zenodo(config, tag_name, zenodo_files, all_files) -> dict | None:
    """Publish to Zenodo."""
    output.step("Zenodo process...")

    publisher = ZenodoPublisher(config)

    up_to_date, msg, record_info = publisher.is_up_to_date(tag_name, zenodo_files)
    if up_to_date and record_info:
        output.info("Last record url: https://doi.org/{doi}", doi=record_info['doi'], name="zenodo.doi")
        output.info("Last record url: {url}", url=record_info['record_url'], name="zenodo.url")

    if msg:
        output.step_ok(msg)
    if up_to_date and not config.zenodo_force_update:
        output.info("No publication made.")
        return record_info
    if up_to_date:
        output.step_warn("Forcing zenodo update")

    if not prompts.confirm_publish.ask("Publish version ?").is_accept:
        output.warn("No publication made")
        return record_info

    identifiers = [af for af in all_files if af.identifier_value]

    try:
        record_info = publisher.publish_new_version(
            zenodo_files, tag_name, identifiers=identifiers,
        )
        output.data("record_info", record_info)
        output.detail("Zenodo DOI: {doi}", doi=record_info['doi'], name="zenodo.published_doi")
        output.step_ok("Publication {tag} completed successfully!", tag=tag_name, name="zenodo.publication_done")
        return record_info

    except ZenodoError as e:
        output.error("GitHub release created but Zenodo publication failed: {err}", err=str(e), name="zenodo.publish_error")
        output.detail("You can manually upload files to Zenodo")
    finally:
        return record_info


def _publish_github(config, tag_name, github_files):
    """Upload files to GitHub release."""
    output.step("Uploading to GitHub release...")

    for af in github_files:
        local_sha = compute_file_hash(af.file_path, "sha256")["formatted_value"]
        remote_sha = get_release_asset_digest(
            config.project_root, tag_name, af.file_path.name,
        )

        if remote_sha and local_sha == remote_sha:
            output.detail("{filename} already up to date on release", filename=af.file_path.name, name="github.asset_ok")
            continue

        if remote_sha:
            output.step_warn("{filename} differs from release asset", filename=af.file_path.name, name="github.asset_diff")
            output.detail("Remote: {hash}", hash=ellipse_hash(remote_sha), name="github.remote_hash")
            output.detail("Local: {hash}", hash=ellipse_hash(local_sha), name="github.local_hash")
            if not prompts.confirm_github_overwrite.ask(f"Overwrite {af.file_path.name} on release ?").is_accept:
                output.warn("{filename} not updated on release", filename=af.file_path.name, name="github.asset_skipped")
                continue

        upload_release_asset(
            config.project_root, tag_name, af.file_path,
            clobber=bool(remote_sha),
        )
        output.detail_ok("{filename} uploaded to release", filename=af.file_path.name, name="github.asset_uploaded")

    output.step_ok("GitHub release updated")


# ---------------------------------------------------------------------------
# Entry points
# ---------------------------------------------------------------------------

def run_release(config, *, test=None) -> None:
    """Run the release process with the given config."""
    try:
        _run_release(config, test=test)
    except KeyboardInterrupt:
        output.info("\nExited.")
    except Exception as e:
        if config.debug:
            raise
        output.fatal("Error during process execution", exc=e)


def _run_release(config, *, test=None) -> None:
    """Main release pipeline."""
    setup_pipeline(config, test=test)
    prompts.init_prompts(config)

    output.info_ok("Main branch: {branch}", branch=config.main_branch, name="config.main_branch")

    # 1. Git check
    _step_git_check(config)

    # 2. Release check/creation
    tag_name = _step_release(config)

    # 3. Commit info
    commit_env = _step_commit_info(config, tag_name)

    # 4. Resolve project name
    _step_project_name(config, tag_name, commit_env)

    # 5. Compile
    _step_compile(config, env_vars=commit_env)

    # 6. Re-check git + release still valid after compilation
    _step_git_check(config)
    verify_release_on_latest_commit(config.project_root, tag_name)

    # Working directory for all generated files
    with tempfile.TemporaryDirectory() as tmp:
        output_dir = Path(tmp)

        # 7. Resolve generated files (scan compile_dir for patterns)
        file_entries = _step_resolve_generated_files(config)

        # 8. Archive (create project ZIP, copy/rename generated files)
        archived_files = _step_archive(config, tag_name, output_dir, file_entries)

        # 9. Manifest
        _step_manifest(config, tag_name, archived_files, commit_env, output_dir)

        # 10. Compute hashes
        _step_compute_hashes(config, archived_files)

        # 11. Sign files
        _step_sign(config, archived_files, output_dir)

        # 12. Compute identifiers
        _step_compute_identifiers(config, archived_files)

        # 13. Publish (per-file routing)
        record_info = _step_publish(config, tag_name, archived_files)

        # 14. Persist
        persist_files(archived_files, config.archive_dir, tag_name)
