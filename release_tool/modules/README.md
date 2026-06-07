# Built-in modules

| Module | Description |
|--------|-------------|
| [digicert_timestamp](digicert_timestamp/README.md) | RFC 3161 trusted timestamp via DigiCert TSA — certifies a file's hash at a point in time |
| [ots_timestamp](ots_timestamp/README.md) | Bitcoin-anchored timestamp via OpenTimestamps — decentralized, trustless proof of existence |

## Standalone mode

Modules can be run outside of the pipeline via `zp modules run <name> [args...]`. Arguments after the module name are passed directly to the module subprocess.

```bash
zp modules list                                          # list available modules
zp modules run digicert_timestamp --help                 # show module help
zp modules run digicert_timestamp stamp paper.pdf        # stamp a file
zp modules run digicert_timestamp verify paper.pdf f.tsr # verify a timestamp
zp modules --debug run digicert_timestamp verify f f.tsr # with debug output
```

### NDJSON event relay

In standalone mode, ZP captures the module's stdout and relays NDJSON events through its output system. This means:

- Events of type `detail`, `detail_ok`, `warn`, `error`, `step`, `step_ok` are formatted as human-readable output
- Events of type `cmd` and `debug` are only shown when `--debug` is passed to ZP
- Non-NDJSON lines (plain text) are passed through as-is

This allows modules to use the same `emit()` function for both pipeline and standalone modes, and `--debug` controls verbosity uniformly.

## Module protocol

Every module must implement at least two **subcommands** (positional, not flags):
- `<name>.py run --input <json>` — normal execution (process files, produce output)
- `<name>.py check --config <json>` — validate config and connectivity (called at pipeline start)
- `<name>.py job --input <json>` — *(optional)* execute a deferred job (see [Async jobs](#async-jobs) below)

Without arguments, the module should display help and exit with code 1.

Modules may also define additional standalone subcommands alongside the pipeline ones (e.g. `stamp`, `verify`).

### Environment variables

ZP sets the following environment variables in the module subprocess:

| Variable | Description |
|----------|-------------|
| `ZP_DEBUG` | Set to `"true"` when `--debug` is passed to ZP. Modules can use this to enable verbose output. |
| `ZP_TEST_MODE` | Set to `"true"` when `--test-mode` is active. Modules can use this to adapt their behavior for testing. |
| `ZP_TEST_CONFIG` | Path to the test config JSON file when `--test-config` is provided. |

These variables are only present when the corresponding flag/option is active — modules should check with `os.environ.get("ZP_DEBUG") == "true"`.

### Input filtering (`input_types`)

When a module runs on a `generated_files` entry, it receives all files with that config_key (original file + outputs from previous modules, except signatures). Use `input_types` in the per-file module config to control which files the module processes:

```yaml
generated_files:
  manifest:
    modules:
      digicert_timestamp: {}
      ots_timestamp:
        input_types: [file, digicert_timestamp]   # process manifest + .tsr from digicert
```

| `input_types` value | Matches |
|---------------------|---------|
| `file` | All primary files: file, project, manifest (group key, same semantics as `archive_types`) |
| `project` / `manifest` | Exact type match (narrows down within the "file" group) |
| `sig` | GPG signatures |
| `<module_name>` | All outputs from that module |
| `<module_name>.<type>` | Specific output sub-type (e.g. `digicert_timestamp.tsr`) |
| *(not set)* | All files except signatures (default) |

> **Note**: `"file"` is a group key — it matches file, project, and manifest types (everything except sig and module_entry). This is consistent with `archive_types` where `"file"` archives FILE/PROJECT/MANIFEST entries. Use `"project"` or `"manifest"` only if you need to narrow to a specific kind.

This filtering is handled by `_shared.filter_input_files` and works for all built-in modules.

## Async jobs

Modules can schedule deferred tasks that run after the pipeline completes. This is useful when a module's output requires time to finalize (e.g. OTS proof upgrade needs hours for Bitcoin confirmation).

### Scheduling a job

In the `run` result, include a `job` key:

```json
{"type": "result", "files": [...], "job": {
  "description": "Upgrade pending OTS proofs to Bitcoin attestation",
  "retry_interval": "1h",
  "retry_max": null
}}
```

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `description` | str | `""` | Shown in `zp jobs list` table |
| `retry_interval` | str/int | `1800` | Minimum time between retries (`"30m"`, `"1h"`, `"5min"`, or seconds) |
| `retry_max` | int/null | `100` | Max retry attempts. `null` = unlimited |

ZP stores the job in `~/.zp/jobs/` with:
- ZP-controlled fields at root level (`id`, `module_name`, `tag_name`, `status`, `retry_count`, `files[]`, etc.)
- Module-provided data under `input` key (`input.job_descriptor` = raw descriptor, `input.files` = per-file `module_config` keyed by `config_key`)

### Implementing the `job` subcommand

```
<name>.py job --input <json>
```

**Input JSON** (same structure as `run`, without `command`):
```json
{
  "config": {"identity_hash_algo": "sha256"},
  "output_dir": "/tmp/zp-job-XXXX/",
  "files": [
    {
      "file_path": "/tmp/.../module_name/file.ext",
      "config_key": "manifest",
      "hashes": {},
      "module_config": {"calendars": ["..."], "nonce": true}
    }
  ]
}
```

The module works **in-place** on the files in `output_dir`. Files are copies from the archive — the originals are untouched until ZP syncs back.

The `module_config` per file is the same merged config (global + per-file overrides) that was passed during the original `run`.

**Output**: NDJSON events + final result:
```json
{"type": "result", "status": "complete|pending|error"}
```

| Status | Meaning |
|--------|---------|
| `complete` | Job is done. Changed files are synced to archive. Job marked complete. |
| `pending` | Not ready yet (e.g. OTS not confirmed). Will retry after `retry_interval`. |
| `error` | Failed. Error recorded in job file. |

**Built-in helper**: `run_module_job_files(args, handler)` in `_shared.py` handles JSON parsing, file iteration, and status aggregation (same pattern as `run_module_files`).

### Archive sync

After the module runs, ZP scans only `workdir/{module_name}/` and compares with `archive_dir/{tag}/{module_name}/`:
- **New files** → copied to archive
- **Unchanged** (same SHA256) → skipped
- **Modified** → user prompted: overwrite / skip / backup (`.backup` with old content)

### Example: OTS timestamp

The `ots_timestamp` module schedules a job to upgrade pending proofs:

```python
# In _cmd_run: return job descriptor
job_descriptor = {
    "description": "Upgrade pending OTS proofs to Bitcoin attestation",
    "retry_interval": retry_interval,  # from module config
    "retry_max": None,                 # unlimited — proof will eventually arrive
}
run_module_files(args, handler=_process_file, result_extra={"job": job_descriptor})

# In _cmd_job: upgrade each .ots file
def _process_job_file(f):
    ots_path = f["file_path"]
    if is_ots_complete(ots_path):
        return {"status": "complete"}
    changed = upgrade_ots(ots_path, calendar_urls=cfg.get("calendars"))
    if changed and is_ots_complete(ots_path):
        return {"status": "complete"}
    return {"status": "pending"}
```
