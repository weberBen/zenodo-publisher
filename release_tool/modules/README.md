# Built-in modules

| Module | Description |
|--------|-------------|
| [digicert_timestamp](digicert_timestamp/README.md) | RFC 3161 trusted timestamp via DigiCert TSA — certifies a file's hash at a point in time |

## Standalone mode

Modules can be run outside of the pipeline via `zp modules run <name> [args...]`. This passes all arguments directly to the module's entry point, allowing access to module-specific standalone subcommands (e.g. `certify`, `verify` for digicert_timestamp).

```bash
zp modules list                                          # list available modules
zp modules run digicert_timestamp --help                 # show module help
zp modules run digicert_timestamp certify paper.pdf      # certify a file
zp modules run digicert_timestamp verify paper.pdf f.tsr # verify a timestamp
```

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
| `ZP_DEBUG` | Set to `"1"` when `--debug` is passed to ZP. Modules can use this to enable verbose output. |
| `ZP_TEST_MODE` | Set to `"1"` when `--test-mode` is active. Modules can use this to adapt their behavior for testing. |
| `ZP_TEST_CONFIG` | Path to the test config JSON file when `--test-config` is provided. |

These variables are only present when the corresponding flag/option is active — modules should check with `os.environ.get("ZP_DEBUG") == "1"`.
