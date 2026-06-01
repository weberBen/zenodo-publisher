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
zp modules run digicert_timestamp certify paper.pdf      # certify a file
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

Without arguments, the module should display help and exit with code 1.

Modules may also define additional standalone subcommands alongside the pipeline ones (e.g. `certify`, `verify`).

### Environment variables

ZP sets the following environment variables in the module subprocess:

| Variable | Description |
|----------|-------------|
| `ZP_DEBUG` | Set to `"true"` when `--debug` is passed to ZP. Modules can use this to enable verbose output. |
| `ZP_TEST_MODE` | Set to `"true"` when `--test-mode` is active. Modules can use this to adapt their behavior for testing. |
| `ZP_TEST_CONFIG` | Path to the test config JSON file when `--test-config` is provided. |

These variables are only present when the corresponding flag/option is active — modules should check with `os.environ.get("ZP_DEBUG") == "true"`.
