"""Main release logic."""

import json
import shutil
import tempfile
from pathlib import Path
from collections import Counter

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
    list_release_assets,
    delete_release_asset,
    archive_zip_project,
)
from ..zenodo_operations import ZenodoPublisher, ZenodoError
from ..archive_operation import (
    FileEntry,
    FileEntryType,
    compute_file_hash,
    compute_identity_hash,
    compute_hashes,
    format_hash_info,
    generate_manifest,
    manifest_to_file,
    process_project_archive,
)
from ..file_utils import persist_files
from ..gpg_operations import gpg_sign_file, prompt_gpg_key
from ..config.generated_files import FileConfigEntry, FileEntryKind, PublisherDestinations
from ..config.signing import SignMode
from .. import output, prompts
from ..errors import PipelineError
from ..modules import run_module, check_module, find_module_path, is_builtin
from ..modules import ModuleError as _ModuleError
from ._common import setup_pipeline
from .context import PipelineContext, HookPoint, HookRegistry


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _resolve_archive(entry_type: FileEntryType, module_name: str | None,
                     fce: FileConfigEntry | None, config,
                     module_entry_type: str | None = None) -> bool:
    """Resolve whether this FileEntry should be persisted to archive_dir.

    Key mapping:
      FILE / PROJECT / MANIFEST → "file"
      SIG                       → "sig"
      MODULE_ENTRY              → module_name  (matches all outputs of that module)
                                  module_name.module_entry_type  (matches specific sub-type only)

    For MODULE_ENTRY, a match on either key is sufficient.

    Priority: per-file fce.archive_types > global config.archive_types.
    Empty list [] = veto (archive nothing for this entry).
    """
    if fce is not None and fce.archive_types is not None:
        effective = fce.archive_types
    else:
        effective = config.archive_types

    if entry_type == FileEntryType.SIG:
        return "sig" in effective
    elif entry_type == FileEntryType.MODULE_ENTRY:
        if module_name in effective:
            return True
        if module_entry_type:
            return f"{module_name}.{module_entry_type}" in effective
        return False
    else:
        return "file" in effective


def ellipse_hash(hash_str, visible_char=8):
    hash_str = hash_str.split(":")[-1]
    return f"{hash_str[:visible_char]}...{hash_str[-visible_char:]}"


# ---------------------------------------------------------------------------
# Handlers — one per HookPoint
# ---------------------------------------------------------------------------

def _step_module_check(ctx: PipelineContext) -> None:
    """Verify all configured modules exist and pass their --check before the pipeline starts."""
    if not ctx.config.modules_config:
        return

    output.step("Checking modules...")

    for module_name, module_cfg in ctx.config.modules_config.items():
        module_path = find_module_path(module_name, project_root=ctx.config.project_root)
        if is_builtin(module_name):
            origin = "built-in"
        else:
            try:
                origin = str(module_path.parent.relative_to(ctx.config.project_root))
            except (ValueError, TypeError):
                origin = str(module_path.parent)
        output.detail(
            "Module '{module_name}' found ({origin})",
            module_name=module_name, origin=origin, name="module.found",
        )
        output.detail(
            "Checking module '{module_name}'...",
            module_name=module_name, name="module.checking",
        )
        check_module(module_name, module_cfg, output,
                     project_root=ctx.config.project_root)
        output.detail_ok(
            "Module '{module_name}' check passed",
            module_name=module_name, name="module.check_ok",
        )

    output.step_ok("Modules ready", name="modules.check_ok")


def _step_git_check(ctx: PipelineContext) -> None:
    """Check branch, remote sync, and local modifications."""
    output.step("🔍 Checking git repository status...")
    check_on_main_branch(ctx.config.project_root, ctx.config.main_branch)
    output.step_ok("On {branch} branch", branch=ctx.config.main_branch, name="git.branch_check")
    check_up_to_date(ctx.config.project_root, ctx.config.main_branch)
    output.step_ok("Project is up to date with git repo", name="git.up_to_date")


def _step_release(ctx: PipelineContext) -> None:
    """Check or create a GitHub release. Sets ctx.tag_name."""
    is_released, latest_release = is_latest_commit_released(ctx.config.project_root)

    if is_released:
        tag_name = latest_release["tagName"]
        output.info_ok("Latest commit already has a release: {tag_name}", tag_name=tag_name, name="release.existing")
        output.info_ok("Nothing to do for release.", name="release.noop")
        output.step_ok("Project is up to date with git release", name="release.up_to_date")
        output.data("tag_name", tag_name)
        ctx.tag_name = tag_name
        return

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
    check_tag_validity(ctx.config.project_root, new_tag, ctx.config.main_branch, check_draft=ctx.config.check_gh_draft)
    create_github_release(ctx.config.project_root, new_tag, release_title, release_notes)
    output.step_ok("Release {tag} created successfully!", tag=new_tag, name="release.created")
    output.data("tag_name", new_tag)
    ctx.tag_name = new_tag


def _step_commit_info(ctx: PipelineContext) -> None:
    """Retrieve commit info. Sets ctx.commit_env."""
    output.step("Retrieve commit info")
    commit_env = get_last_commit_info(ctx.config.project_root, tag_name=ctx.tag_name)
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
    ctx.commit_env = commit_env


def _step_project_name(ctx: PipelineContext) -> None:
    """Resolve project name template."""
    output.step("Resolving project name")
    ctx.config.generate_project_name({
        "tag_name": ctx.tag_name,
        "sha_commit": ctx.commit_env["ZP_COMMIT_SHA"],
    })
    output.data("project_name", ctx.config.project_name)
    output.step_ok("Formatted project name: {project_name}", project_name=ctx.config.project_name, name="project.name")


def _step_compile(ctx: PipelineContext) -> None:
    """Compile project via make (with user prompt)."""
    if not ctx.config.compile_enabled:
        output.step_warn("Skipping project compilation (see config file)")
        return

    if not prompts.confirm_build.ask("Start building project ?").is_accept:
        raise PipelineError("Build aborted by user.", name="build_aborted")

    output.step("📋 Starting build process...")
    compile(ctx.config.compile_dir, ctx.config.make_args, env_vars=ctx.commit_env)
    output.step_ok("Compilation ended")


def _step_post_compile(ctx: PipelineContext) -> None:
    """Re-check git status and verify release is still valid after compilation."""
    _step_git_check(ctx)
    verify_release_on_latest_commit(ctx.config.project_root, ctx.tag_name)


# ---------------------------------------------------------------------------
# Step: Resolve generated files
# ---------------------------------------------------------------------------

def _step_resolve_generated_files(ctx: PipelineContext) -> None:
    """Resolve pattern templates and glob for matches."""
    output.step("Resolving generated files...")
    for entry in ctx.config.generated_files:
        if entry.type == FileEntryKind.PATTERN:
            pattern = entry.pattern
            if "{project_name}" in pattern:
                pattern = pattern.replace("{project_name}", ctx.config.project_name)
            pattern = pattern.lstrip("/")
            base = ctx.config.project_root or Path.cwd()
            matches = sorted(base.glob(pattern))

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


# ---------------------------------------------------------------------------
# Step: Archive
# ---------------------------------------------------------------------------

def _step_archive(ctx: PipelineContext) -> None:
    """Create archives: copy/rename generated files, create project ZIP."""
    output.step("Archiving files...")

    for entry in ctx.config.generated_files:
        if entry.type == FileEntryKind.PATTERN:
            # Count how many files share the same extension to detect collisions
            ext_counts = Counter(p.suffix for p in entry.resolved_paths)
            for src_path in entry.resolved_paths:
                if entry.rename:
                    ext = src_path.suffix
                    # Only add original stem suffix when multiple files share the same extension
                    suffix = f"_{src_path.stem}" if ext_counts[ext] > 1 else ""
                    dst = ctx.output_dir / f"{ctx.config.project_name}{suffix}{ext}"
                else:
                    dst = ctx.output_dir / src_path.name
                if dst.exists():
                    raise PipelineError(
                        f"File name collision: '{dst.name}' already exists in "
                        f"output directory (from generated_files.{entry.key})",
                        name=f"archive.collision.{entry.key}",
                    )
                shutil.copy2(src_path, dst)
                ctx.archived_files.append(FileEntry(
                    file_path=dst,
                    config_key=entry.key,
                    filename=dst.stem,
                    extension=dst.suffix.lstrip("."),
                    type=FileEntryType.FILE,
                    archive=_resolve_archive(FileEntryType.FILE, None, entry, ctx.config),
                    publishers=entry.publishers or ctx.config.default_publishers,
                    sign_mode=entry.effective_sign_mode(ctx.config.signing.sign_mode),
                    is_preview=(dst.suffix.lstrip(".") == "pdf"),
                    has_signature=entry.effective_sign(ctx.config.signing.sign),
                    internal_identifier=compute_identity_hash(dst, ctx.config.identity_hash_algo),
                ))
                output.detail("{src} → {dst}", src=src_path.name, dst=dst.name, name="archive.copy")

        elif entry.type == FileEntryKind.PROJECT:
            # Use project_name (e.g. MyProject-v1.0.0) if rename=true,
            # otherwise use the repo directory name (e.g. my-repo)
            archive_name = ctx.config.project_name if entry.rename else ctx.config.project_root.name
            result = archive_zip_project(
                ctx.config.project_root, ctx.tag_name,
                archive_name, ctx.output_dir,
            )
            # Post-process: tree hashes + optional TAR conversion
            hash_algos = list(ctx.config.hash_algorithms or [])
            from ..config.transform_common import TREE_ALGORITHMS
            tree_algos = [a for a in hash_algos if a in TREE_ALGORITHMS]

            final_path, final_format, tree_hashes = process_project_archive(
                result.file_path, result.archive_name,
                tree_algos=tree_algos, archive_format=ctx.config.archive_format,
                tar_args=ctx.config.archive_tar_extra_args,
                gzip_args=ctx.config.archive_gzip_extra_args,
            )
            # Pre-format tree hashes into hashes dict (computed during
            # extraction, can't recompute from the packed archive file)
            pre_hashes = {
                algo: format_hash_info(algo, value)
                for algo, value in tree_hashes.items()
            }
            ctx.archived_files.append(FileEntry(
                file_path=final_path,
                config_key=entry.key,
                filename=result.archive_name,
                extension=final_format,
                type=FileEntryType.PROJECT,
                archive=_resolve_archive(FileEntryType.PROJECT, None, entry, ctx.config),
                publishers=entry.publishers or ctx.config.default_publishers,
                sign_mode=entry.effective_sign_mode(ctx.config.signing.sign_mode),
                has_signature=entry.effective_sign(ctx.config.signing.sign),
                internal_identifier=compute_identity_hash(final_path, ctx.config.identity_hash_algo),
                hashes=pre_hashes,
            ))
            output.detail("project archive: {filename}", filename=final_path.name, name="archive.project")

        # MANIFEST kind is handled in _step_manifest

    output.step_ok("Files archived")


# ---------------------------------------------------------------------------
# Step: Compute hashes
# ---------------------------------------------------------------------------

def _step_compute_hashes(ctx: PipelineContext) -> None:
    """Compute hashes for all entries (skips already-hashed files)."""
    output.step("Computing hashes...")
    algos = list(ctx.config.hash_algorithms or [])
    if ctx.config.identity_hash_algo not in algos:
        algos.append(ctx.config.identity_hash_algo)
    compute_hashes(ctx.archived_files, algos)

    for af in ctx.archived_files:
        output.detail("{filename}", filename=af.file_path.name, name="hash.file")
        for algo, h in af.hashes.items():
            output.detail("  {algo}: {hash}", algo=algo, hash=h['value'], name="hash.value")
    output.data("file_hashes", {
        af.file_path.name: {algo: h["value"] for algo, h in af.hashes.items()}
        for af in ctx.archived_files
    })
    output.step_ok("Hashes computed")


# ---------------------------------------------------------------------------
# Step: Manifest
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


def _filter_manifest_files(archived_files: list[FileEntry],
                           content: dict[str, list[str]]) -> list[FileEntry]:
    """Filter archived_files based on manifest.content config.

    content maps config_key → list of type keys:
      "file" = FILE/PROJECT/MANIFEST entries
      "sig"  = SIG entries
      "<module_name>" = MODULE_ENTRY entries from that module
    """
    included = []
    for config_key, types in content.items():
        for type_key in types:
            if type_key == "sig":
                matches = [af for af in archived_files
                           if af.config_key == config_key and af.type == FileEntryType.SIG]
            elif type_key == "file":
                matches = [af for af in archived_files
                           if af.config_key == config_key
                           and af.type not in (FileEntryType.SIG, FileEntryType.MODULE_ENTRY)]
            else:
                # module name
                matches = [af for af in archived_files
                           if af.config_key == config_key and af.module_name == type_key]
            included.extend(matches)
    return included


def _step_manifest(ctx: PipelineContext) -> None:
    """Generate manifest if a manifest FileConfigEntry exists."""
    manifest_entry_cfg = next(
        (e for e in ctx.config.generated_files if e.type == FileEntryKind.MANIFEST),
        None,
    )
    if manifest_entry_cfg is None:
        return

    output.step("📋 Generating manifest...")
    mc = manifest_entry_cfg.manifest_config

    content = mc.content if mc else None
    if content is None:
        # Default: include all "file"-type entries (FILE/PROJECT/MANIFEST), not sig or module outputs
        included = [af for af in ctx.archived_files
                    if af.type not in (FileEntryType.SIG, FileEntryType.MODULE_ENTRY)]
    else:
        included = _filter_manifest_files(ctx.archived_files, content)
    metadata = _load_manifest_metadata(ctx.config, mc.zenodo_metadata if mc else [])

    manifest_dict = generate_manifest(
        included, ctx.tag_name, ctx.commit_env,
        commit_fields=mc.commit_info if mc else None,
        metadata=metadata,
        identity_key=ctx.config.identity_key,
        identity_hash_algo=ctx.config.identity_hash_algo,
    )
    manifest_path = manifest_to_file(ctx.config, manifest_dict, ctx.output_dir)
    output.detail("Manifest: {path}", path=str(manifest_path), name="manifest.path")

    manifest_entry = FileEntry(
        file_path=manifest_path,
        config_key="manifest",
        filename="manifest",
        extension="json",
        type=FileEntryType.MANIFEST,
        archive=_resolve_archive(FileEntryType.MANIFEST, None, manifest_entry_cfg, ctx.config),
        publishers=manifest_entry_cfg.publishers or ctx.config.default_publishers,
        sign_mode=manifest_entry_cfg.effective_sign_mode(ctx.config.signing.sign_mode),
        has_signature=manifest_entry_cfg.effective_sign(ctx.config.signing.sign),
        internal_identifier=compute_identity_hash(manifest_path, ctx.config.identity_hash_algo),
    )
    # Compute hashes immediately so the manifest entry is ready for signing/identifiers
    algos = list(ctx.config.hash_algorithms or [])
    if ctx.config.identity_hash_algo not in algos:
        algos.append(ctx.config.identity_hash_algo)
    compute_hashes([manifest_entry], algos)

    ctx.archived_files.append(manifest_entry)
    output.step_ok("Manifest generated")


# ---------------------------------------------------------------------------
# Step: Sign
# ---------------------------------------------------------------------------

def _step_sign(ctx: PipelineContext) -> None:
    """Sign all entries that have has_signature=True."""
    to_sign = [af for af in ctx.archived_files if af.has_signature]
    if not to_sign:
        output.step_ok("Signing skipped", silent=True)
        return

    output.step("🔏 Signing files...")
    prompt_gpg_key(ctx.config.gpg_uid, ctx.config.gpg_extra_args)

    sig_ext = "asc" if "--armor" in ctx.config.gpg_extra_args else "sig"

    for af in to_sign:
        if af.sign_mode == SignMode.FILE:
            sig_path = gpg_sign_file(
                af.file_path, ctx.output_dir,
                gpg_uid=ctx.config.gpg_uid,
                extra_args=ctx.config.gpg_extra_args,
            )
        elif af.sign_mode == SignMode.FILE_HASH:
            hash_value = af.hashes[ctx.config.identity_hash_algo]["formatted_value"]
            hash_file = ctx.output_dir / f"{af.file_path.name}.{ctx.config.identity_hash_algo}"
            hash_file.write_text(hash_value, encoding="ascii")
            sig_path = gpg_sign_file(
                hash_file, ctx.output_dir,
                gpg_uid=ctx.config.gpg_uid,
                extra_args=ctx.config.gpg_extra_args,
            )
            hash_file.unlink(missing_ok=True)

        parent_fce = next(
            (fce for fce in ctx.config.generated_files if fce.key == af.config_key), None
        )
        sig_af = FileEntry(
            file_path=sig_path,
            config_key=af.config_key,
            filename=sig_path.stem,
            extension=sig_ext,
            type=FileEntryType.SIG,
            archive=_resolve_archive(FileEntryType.SIG, None, parent_fce, ctx.config),
            publishers=af.publishers,
            internal_identifier=compute_identity_hash(sig_path, ctx.config.identity_hash_algo),
        )
        compute_hashes([sig_af], ctx.config.hash_algorithms)
        ctx.archived_files.append(sig_af)

    output.step_ok("Files signed")



# ---------------------------------------------------------------------------
# Step: Custom modules
# ---------------------------------------------------------------------------

def _step_modules(ctx: PipelineContext) -> None:
    """Run all configured custom modules for files that declare them."""
    if not ctx.config.modules_config:
        return

    output.step("Running modules...")

    for module_name in ctx.config.modules_config:
        global_cfg = ctx.config.modules_config[module_name]

        files_input = []
        for fe in ctx.config.generated_files:
            if module_name not in fe.modules:
                continue
            per_file_cfg = fe.modules[module_name]
            merged = {**global_cfg, **per_file_cfg}
            for af in ctx.archived_files:
                if af.config_key == fe.key and af.type != "sig":
                    files_input.append({
                        "file_path": str(af.file_path),
                        "config_key": af.config_key,
                        "type": af.type,
                        "hashes": af.hashes,
                        "module_config": merged,
                    })

        if not files_input:
            raise _ModuleError(
                f"Module '{module_name}' is configured but no generated_files entry "
                f"declares it under 'modules:'. Add 'modules: {{{module_name}: {{}}}}' "
                f"to the relevant generated_files entry, or remove the module from config.",
                name="no_files",
            )

        if is_builtin(module_name):
            module_origin = "built-in module"
        else:
            module_origin = f"custom module (.zp/modules/{module_name} or ~/.zp/modules/{module_name})"
        if not prompts.confirm_run_module.ask(
            f"Run {module_origin} '{module_name}' on {len(files_input)} file(s)?"
        ).is_accept:
            output.warn(
                "Module '{module_name}' skipped by user",
                module_name=module_name, name="module.skipped",
            )
            continue

        output.detail(
            "Module '{module_name}' confirmed, running on {n} file(s)...",
            module_name=module_name, n=len(files_input), name="module.confirmed",
        )

        module_output_dir = ctx.output_dir / module_name
        module_output_dir.mkdir(exist_ok=True)

        input_data = {
            "config": {"identity_hash_algo": ctx.config.identity_hash_algo},
            "output_dir": str(module_output_dir),
            "files": files_input,
        }

        output.detail(
            "Running module '{module_name}' ({n} file(s))...",
            module_name=module_name, n=len(files_input), name="module.running",
        )

        raw_files = run_module(module_name, input_data, output,
                               project_root=ctx.config.project_root)

        for rf in raw_files:
            config_key = rf["config_key"]
            parent_fce = next(
                (fce for fce in ctx.config.generated_files if fce.key == config_key), None
            )
            publishers_sentinel = object()
            publishers_raw = rf.get("publishers", publishers_sentinel)
            if publishers_raw is publishers_sentinel:
                # Key absent — fall back to parent entry's publishers, then global default.
                effective_pub = (
                    (parent_fce.publishers if parent_fce else None)
                    or ctx.config.default_publishers
                )
                dest_raw = effective_pub.destination
            elif publishers_raw is None:
                # Explicit null — publish nowhere.
                dest_raw = {}
            else:
                dest_raw = publishers_raw.get("destination", {})
            met = rf.get("module_entry_type")
            # Module output can override archive via archive_types list in JSON
            module_archive_types = rf.get("archive_types")
            if module_archive_types is not None:
                module_archive = module_name in module_archive_types or (
                    met and f"{module_name}.{met}" in module_archive_types
                )
            else:
                module_archive = _resolve_archive(
                    FileEntryType.MODULE_ENTRY, module_name, parent_fce, ctx.config,
                    module_entry_type=met,
                )
            fe = FileEntry(
                file_path=Path(rf["file_path"]),
                config_key=rf["config_key"],
                filename=Path(rf["file_path"]).stem,
                extension=Path(rf["file_path"]).suffix.lstrip("."),
                type=FileEntryType.MODULE_ENTRY,
                archive=module_archive,
                publishers=PublisherDestinations(destination=dest_raw),
                module_name=module_name,
                module_entry_type=rf.get("module_entry_type"),
                internal_identifier=compute_identity_hash(Path(rf["file_path"]), ctx.config.identity_hash_algo),
            )
            ctx.archived_files.append(fe)
            output.detail(
                "Module entry: {filename} (key={config_key}, entry_type={module_entry_type},"
                " archive={archive})",
                filename=fe.file_path.name,
                module_name=module_name,
                module_entry_type=fe.module_entry_type,
                config_key=fe.config_key,
                archive=fe.archive,
                publishers=dest_raw,
                name="module.entry",
            )

        output.detail_ok(
            "Module '{module_name}' returned {n} file(s)",
            module_name=module_name, n=len(raw_files), name="module.done",
        )

    output.step_ok("Modules completed", name="modules.completed")


# ---------------------------------------------------------------------------
# Step: Publish
# ---------------------------------------------------------------------------

def _step_publish(ctx: PipelineContext) -> None:
    """Route each file to its configured destinations."""
    zenodo_files = _files_for_destination(ctx.archived_files, "zenodo")
    github_files = _files_for_destination(ctx.archived_files, "github")

    if zenodo_files and ctx.config.has_zenodo_config():
        ctx.record_info = _publish_zenodo(ctx, zenodo_files)

    if github_files:
        _publish_github(ctx, github_files)


def _entry_destinations(pub: PublisherDestinations, fe: FileEntry) -> set[str]:
    """Return the set of destinations for a FileEntry from a PublisherDestinations.

    Shared by publishers and publish_identity_hash lookups.
    """
    if fe.type == FileEntryType.MODULE_ENTRY:
        platforms = set(pub.destinations_for(fe.module_name))
        if fe.module_entry_type:
            platforms |= set(pub.destinations_for(f"{fe.module_name}.{fe.module_entry_type}"))
        return platforms
    if fe.type == FileEntryType.SIG:
        return set(pub.destinations_for(FileEntryType.SIG))
    return set(pub.destinations_for(FileEntryType.FILE))


def _files_for_destination(archived_files: list[FileEntry], destination: str) -> list[FileEntry]:
    """Get files destined for a specific publisher."""
    return [
        fe for fe in archived_files
        if fe.publishers and destination in _entry_destinations(fe.publishers, fe)
    ]


def _files_for_identity_destination(
    archived_files: list[FileEntry],
    config_entries: list,
    destination: str,
) -> list[FileEntry]:
    """Get FileEntry instances whose publish_identity_hash includes the given destination."""
    result = []
    for af in archived_files:
        fce = next((f for f in config_entries if f.key == af.config_key), None)
        if fce is None:
            continue
        if fce.publish_identity_hash is None:
            continue
        if destination in _entry_destinations(fce.publish_identity_hash, af):
            result.append(af)
    return result


def _publish_zenodo(ctx: PipelineContext, zenodo_files: list[FileEntry]) -> dict | None:
    """Publish to Zenodo."""
    output.step("Zenodo process...")

    publisher = ZenodoPublisher(ctx.config)

    up_to_date, msg, record_info = publisher.is_up_to_date(ctx.tag_name, zenodo_files)
    if up_to_date and record_info:
        output.info("Last record url: https://doi.org/{doi}", doi=record_info['doi'], name="zenodo.doi")
        output.info("Last record url: {url}", url=record_info['record_url'], name="zenodo.url")

    if msg:
        output.step_ok(msg)
    if up_to_date and not ctx.config.zenodo_force_update:
        output.info("No publication made.")
        for af in zenodo_files:
            output.detail_skip("{filename} already up to date", filename=af.file_path.name, name="zenodo.asset_ok")
        output.step_ok("Zenodo publication skipped")
        return record_info
    if up_to_date:
        output.step_warn("Forcing zenodo update")

    if not prompts.confirm_publish.ask("Publish version ?").is_accept:
        output.warn("No publication made")
        return record_info

    zenodo_identifiers = _files_for_identity_destination(
        ctx.archived_files, ctx.config.generated_files, "zenodo",
    )

    try:
        record_info = publisher.publish_new_version(
            zenodo_files, ctx.tag_name, identifiers=zenodo_identifiers,
        )
        output.data("record_info", record_info)
        for af in zenodo_files:
            output.detail_ok("{filename} uploaded to Zenodo", filename=af.file_path.name, name="zenodo.asset_uploaded")
        output.detail("Zenodo DOI: {doi}", doi=record_info['doi'], name="zenodo.published_doi")
        output.step_ok("Publication {tag} completed successfully!", tag=ctx.tag_name, name="zenodo.publication_done")
        return record_info

    except ZenodoError as e:
        output.error("GitHub release created but Zenodo publication failed: {err}", err=str(e), name="zenodo.publish_error")
        output.detail("You can manually upload files to Zenodo")
    finally:
        return record_info


def _github_identity_hash_files(ctx: PipelineContext, github_files: list[FileEntry]) -> list[Path]:
    """Create .identity_hash.txt files for entries with publish_identity_hash: [github, ...]."""
    txt_files = []
    for af in github_files:
        fce = next((f for f in ctx.config.generated_files if f.key == af.config_key), None)
        if not fce or not fce.publish_identity_hash:
            continue
        if "github" not in _entry_destinations(fce.publish_identity_hash, af):
            continue
        txt_path = af.file_path.parent / f"{af.file_path.name}.identity_hash.txt"
        txt_path.write_text(af.internal_identifier or "", encoding="ascii")
        txt_files.append(txt_path)
    return txt_files


def _publish_github(ctx: PipelineContext, github_files: list[FileEntry]) -> None:
    """Upload files to GitHub release."""
    output.step("Uploading to GitHub release...")

    # Build the set of names we will upload (including .identity_hash.txt files)
    identity_txt_files = _github_identity_hash_files(ctx, github_files)
    local_names = {af.file_path.name for af in github_files} | {f.name for f in identity_txt_files}

    # Cleanup: list remote assets and ask to delete leftovers
    remote_assets = list_release_assets(ctx.config.project_root, ctx.tag_name)
    leftovers = [a for a in remote_assets if a.get("name") not in local_names]
    if leftovers:
        output.step_warn("Leftover assets found on release (not in current upload set)")
        for asset in leftovers:
            remote_digest = asset.get("digest") or "unknown"
            output.detail(
                "Leftover: {filename} (remote sha256: {hash})",
                filename=asset["name"], hash=ellipse_hash(remote_digest), name="github.leftover",
            )
            if prompts.confirm_delete_asset.ask(f"Delete {asset['name']} from release?").is_accept:
                delete_release_asset(ctx.config.project_root, asset["id"])
                output.detail_ok("{filename} deleted", filename=asset["name"], name="github.leftover_deleted")
            else:
                output.detail_skip("{filename} kept", filename=asset["name"], name="github.leftover_kept")

    # Upload regular files
    for af in github_files:
        local_sha = compute_file_hash(af.file_path, "sha256")["formatted_value"]
        remote_sha = get_release_asset_digest(
            ctx.config.project_root, ctx.tag_name, af.file_path.name,
        )

        if remote_sha and local_sha == remote_sha:
            output.detail_skip("{filename} already up to date", filename=af.file_path.name, name="github.asset_ok")
            continue

        if remote_sha:
            output.step_warn("{filename} differs from release asset", filename=af.file_path.name, name="github.asset_diff")
            output.detail("Remote: {hash}", hash=ellipse_hash(remote_sha), name="github.remote_hash")
            output.detail("Local: {hash}", hash=ellipse_hash(local_sha), name="github.local_hash")
            if not prompts.confirm_github_overwrite.ask(f"Overwrite {af.file_path.name} on release ?").is_accept:
                output.detail_skip("{filename} skipped", filename=af.file_path.name, name="github.asset_skipped")
                continue

        upload_release_asset(
            ctx.config.project_root, ctx.tag_name, af.file_path,
            clobber=bool(remote_sha),
        )
        output.detail_ok("{filename} uploaded to release", filename=af.file_path.name, name="github.asset_uploaded")

    # Upload .identity_hash.txt files
    for txt_path in identity_txt_files:
        upload_release_asset(
            ctx.config.project_root, ctx.tag_name, txt_path, clobber=True,
        )
        output.detail_ok("{filename} uploaded to release", filename=txt_path.name, name="github.identity_hash_uploaded")

    output.step_ok("GitHub release updated")


# ---------------------------------------------------------------------------
# Step: Persist
# ---------------------------------------------------------------------------

def _step_persist(ctx: PipelineContext) -> None:
    """Move files with archive=True to the archive directory."""
    persist_files(ctx.archived_files, ctx.config.archive_dir, ctx.tag_name, ctx.output_dir)


# ---------------------------------------------------------------------------
# Registry builder
# ---------------------------------------------------------------------------

def _build_registry() -> HookRegistry:
    """Register all built-in pipeline handlers and return the registry."""
    reg = HookRegistry()
    reg.register(HookPoint.MODULE_CHECK,    _step_module_check)
    reg.register(HookPoint.GIT_CHECK,       _step_git_check)
    reg.register(HookPoint.RELEASE,         _step_release)
    reg.register(HookPoint.COMMIT_INFO,     _step_commit_info)
    reg.register(HookPoint.PROJECT_NAME,    _step_project_name)
    reg.register(HookPoint.COMPILE,         _step_compile)
    reg.register(HookPoint.POST_COMPILE,    _step_post_compile)
    reg.register(HookPoint.RESOLVE_FILES,   _step_resolve_generated_files)
    reg.register(HookPoint.ARCHIVE,         _step_archive)
    reg.register(HookPoint.HASH,            _step_compute_hashes)
    reg.register(HookPoint.MANIFEST,        _step_manifest)
    reg.register(HookPoint.SIGN,            _step_sign)
    reg.register(HookPoint.CUSTOM_MODULES,  _step_modules)
    reg.register(HookPoint.PUBLISH,         _step_publish)
    reg.register(HookPoint.PERSIST,         _step_persist)
    return reg


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
    """Main release pipeline — generic hook-based runner."""
    setup_pipeline(config, test=test)
    prompts.init_prompts(config)

    output.info_ok("Main branch: {branch}", branch=config.main_branch, name="config.main_branch")

    with tempfile.TemporaryDirectory() as tmp:
        ctx = PipelineContext(config=config, output_dir=Path(tmp))
        registry = _build_registry()
        registry.run_pipeline(ctx)
