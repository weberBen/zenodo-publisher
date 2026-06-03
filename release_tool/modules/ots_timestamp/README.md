# ots_timestamp

Anchors file hashes in the Bitcoin blockchain via the [OpenTimestamps](https://opentimestamps.org/) protocol. The timestamp proves that a file with a given hash existed before a specific Bitcoin block was mined.

Unlike DigiCert (centralized, instant, trust-based), OpenTimestamps is **decentralized and trustless** — verification relies on the Bitcoin blockchain, not a certificate authority. However, the full proof is **delayed** (hours) because it requires a Bitcoin block confirmation.

## How it works

1. Computes SHA256 hash of the file (with optional privacy nonce)
2. Submits to OpenTimestamps calendar servers (aggregation layer)
3. Calendar servers build a Merkle tree and anchor its root in a Bitcoin transaction
4. The `.ots` file initially contains a **pending proof** (promise from the calendar)
5. After Bitcoin confirmation (~hours), the proof can be **upgraded** to include the full Merkle path to the Bitcoin block header
6. Verification checks the hash match + confirms the merkle root against the Bitcoin blockchain (via Blockstream API)

## Configuration

```yaml
modules:
  ots_timestamp:
    calendars:                              # optional, defaults to OTS pool (4 servers)
      - https://a.pool.opentimestamps.org
      - https://b.pool.opentimestamps.org
      - https://a.pool.eternitywall.com
      - https://ots.btc.catallaxy.com
    nonce: true                             # privacy nonce (default: true)
    upgrade:
      save_header: true                     # save Bitcoin block header on upgrade (default: true)
```

| Option | Default | Description |
|--------|---------|-------------|
| `calendars` | OTS pool (4 servers) | Calendar server URLs for stamping and upgrading |
| `nonce` | `true` | Add privacy nonce so calendars never see the real file hash |
| `upgrade.save_header` | `true` | Save Bitcoin block header as `.blockheader.json` after successful upgrade |

## Pipeline behavior

In pipeline mode (`run`), the module stamps each file immediately and returns `.ots` files with **pending proofs**. The upgrade step (Bitcoin confirmation) must be done manually later via the `upgrade` standalone subcommand.

The module config (`calendars`, `nonce`, `upgrade`) is passed through in the result so a future upgrade runner can use it.

## Standalone usage

```bash
# Show available subcommands
zp modules run ots_timestamp --help

# Stamp file(s) — produces .ots (pending proof)
zp modules run ots_timestamp stamp paper.pdf
zp modules run ots_timestamp stamp paper.pdf --algo sha256 --no-nonce

# Upgrade pending proof(s) — check for Bitcoin confirmation
zp modules run ots_timestamp upgrade paper.pdf.ots
zp modules run ots_timestamp upgrade paper.pdf.ots --save-header

# Verify a file against its .ots proof (checks blockchain via Blockstream API)
zp modules run ots_timestamp verify paper.pdf paper.pdf.ots

# Display proof metadata (hash, status, attestations, proof chains)
zp modules run ots_timestamp info paper.pdf.ots
```

| Subcommand | Description |
|------------|-------------|
| `stamp` | Submit file(s) to OTS calendar servers. Produces `.ots` files (pending proof). Options: `--algo`, `--no-nonce`, `--output-dir`, `--calendar-urls` |
| `upgrade` | Attempt to upgrade pending `.ots` to Bitcoin-attested proof. Verifies block via Blockstream API. Options: `--save-header`, `--calendar-urls` |
| `verify` | Verify a file against its `.ots` proof. Checks hash match + blockchain verification via Blockstream API |
| `info` | Display `.ots` metadata: hash, attestation type, block height (if confirmed), proof chain details |

## Proof lifecycle

```
stamp → .ots (pending)
         │
         ├── upgrade (after ~hours) → .ots (Bitcoin-attested)
         │     └── --save-header → .blockheader.json
         │
         └── verify → hash match + blockchain verification
```

## Upgrade output

After a successful upgrade, the module shows:
- Confirmed attestations with block height, timestamp, and time elapsed
- Pending attestations (calendars that haven't confirmed yet)
- Summary: `N/M attestations confirmed, K pending`

The `.blockheader.json` file (when `--save-header` is used) contains all verified block headers, appending new ones on subsequent upgrades.

## Dependencies

- [`opentimestamps-client`](https://pypi.org/project/opentimestamps-client/) — official OpenTimestamps Python client
- [`requests`](https://pypi.org/project/requests/) — HTTP for calendar server connectivity check and Blockstream API
