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

Modules use subcommands for pipeline integration:
- `<name>.py check --config <json>` — validate config and connectivity (called at pipeline start)
- `<name>.py run --input <json>` — normal execution (process files, produce output)

Modules may also define additional standalone subcommands alongside the pipeline ones.
