# ots_timestamp

Anchors file hashes in the Bitcoin blockchain via the [OpenTimestamps](https://opentimestamps.org/) protocol. The timestamp proves that a file with a given hash existed before a specific Bitcoin block was mined.

Unlike DigiCert (centralized, instant, trust-based), OpenTimestamps is **decentralized and trustless** — verification relies on the Bitcoin blockchain, not a certificate authority. However, the full proof is **delayed** (hours) because it requires a Bitcoin block confirmation.

## How it works

1. Computes SHA256 hash of the file
2. Submits to OpenTimestamps calendar servers (aggregation layer)
3. Calendar servers build a Merkle tree and anchor its root in a Bitcoin transaction
4. The `.ots` file initially contains a **pending proof** (promise from the calendar)
5. After Bitcoin confirmation (~hours), the proof can be **upgraded** to include the full Merkle path to the Bitcoin block header

## Configuration

```yaml
modules:
  ots_timestamp:
    calendar_urls:                          # optional, defaults to OTS pool
      - https://a.pool.opentimestamps.org
      - https://b.pool.opentimestamps.org
```

## Pipeline behavior

In pipeline mode (`run`), the module stamps each file immediately and returns `.ots` files with **pending proofs**. The upgrade step (Bitcoin confirmation) must be done manually later via the `upgrade` standalone subcommand.

## Standalone usage

```bash
# Show available subcommands
zp modules run ots_timestamp --help

# Stamp file(s) — produces .ots (pending proof)
zp modules run ots_timestamp stamp paper.pdf
zp modules run ots_timestamp stamp paper.pdf manifest.json --output-dir ./timestamps

# Upgrade pending proof(s) — check for Bitcoin confirmation
zp modules run ots_timestamp upgrade paper.pdf.ots
zp modules run ots_timestamp upgrade paper.pdf.ots manifest.json.ots

# Verify a file against its .ots proof
zp modules run ots_timestamp verify paper.pdf paper.pdf.ots

# Display proof metadata (hash, status, attestations)
zp modules run ots_timestamp info paper.pdf.ots
```

| Subcommand | Description |
|------------|-------------|
| `stamp` | Submit file(s) to OTS calendar servers. Produces `.ots` files (pending proof). Options: `--output-dir`, `--calendar-urls` |
| `upgrade` | Attempt to upgrade pending `.ots` to Bitcoin-attested proof. Options: `--calendar-urls` |
| `verify` | Verify a file against its `.ots` proof. Reports pending vs confirmed status |
| `info` | Display `.ots` metadata: hash, attestation type, block height (if confirmed) |

## Proof lifecycle

```
stamp → .ots (pending)
         │
         ├── upgrade (after ~hours) → .ots (Bitcoin-attested) ✓
         │
         └── verify → hash match + pending/confirmed status
```

## Dependencies

- [`opentimestamps-client`](https://pypi.org/project/opentimestamps-client/) — official OpenTimestamps Python client
- [`requests`](https://pypi.org/project/requests/) — HTTP for calendar server connectivity check
