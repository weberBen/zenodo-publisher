# DigiCert RFC 3161 Timestamping — Trust Chain

## Overview

A TSR (Timestamp Response) per RFC 3161 proves that:
- A file existed at a specific point in time
- It has not changed by a single bit since then

DigiCert server used: `http://timestamp.digicert.com`

---

## Full hierarchy

```
DigiCert Assured ID Root CA                        [Root CA — expires 2031]
    │   Self-signed, present in all OS trust stores
    │
    └── DigiCert Trusted Root G4                   [Intermediate root — ~2038]
              │   Signed by Assured ID Root CA
              │   Present in /etc/ssl/certs
              │
              └── DigiCert Trusted G4 TimeStamping RSA4096 SHA256 2025 CA1   [~15 months]
                            │   Signed by Trusted Root G4
                            │   Embedded in the TSR
                            │
                            └── DigiCert SHA256 RSA4096 Timestamp Responder 2025 1   [~15 months]
                                          │   Signed by CA1
                                          │   Embedded in the TSR
                                          │
                                          └── YOUR TSR (manifest-v2.0.22.json.tsr)
```

---

## Certificate details

### DigiCert Assured ID Root CA
- **Role**: Root of all DigiCert trust
- **Created**: November 10, 2006
- **Expires**: November 10, 2031
- **Self-signed**: yes (issuer = subject)
- **SHA-256 Fingerprint**: `3E:90:99:B5:01:5E:8F:48:6C:00:BC:EA:9D:11:1E:E7:21:FA:BA:35:5A:89:BC:F1:DF:69:56:1E:3D:C6:32:5C`
- **Presence**: pre-installed in all OS, browsers, and certificate stores
- **Changes?**: never — modifying this certificate would invalidate the entire chain built on top of it
- **Its private key**: used a handful of times in its entire lifetime (signs only Trusted Root G4 and a few other Root G4 certificates)
- **2031 transition**: DigiCert anticipated the expiration through `Trusted Root G4`, which will become an independent Root CA before 2031

### DigiCert Trusted Root G4
- **Role**: Intermediate root — signs all DigiCert PKIs (timestamping, SSL, code signing…)
- **Created**: 2022
- **Expires**: ~2038
- **Presence**: in `/etc/ssl/certs`, progressively pre-installed in OS updates
- **Changes?**: no, stable like a Root CA
- **Relationship with Assured ID Root CA**: signed by it (cross-signing), enabling the transition before 2031

### DigiCert Trusted G4 TimeStamping RSA4096 SHA256 2025 CA1
- **Role**: Intermediate CA dedicated to timestamping
- **Lifetime**: ~15 months (CA/Browser Forum requirement)
- **Embedded in TSR**: yes
- **Signs**: only Responder certificates (the ones that sign TSRs)
- **Rotation**: replaced by `2026 CA1` etc. each cycle

### DigiCert SHA256 RSA4096 Timestamp Responder 2025 1
- **Role**: physically signs TSRs
- **Lifetime**: ~15 months (CA/Browser Forum requirement)
- **Embedded in TSR**: yes
- **Signs**: millions of individual TSRs
- **Rotation**: regularly replaced (`2025 2`, `2026 1`, etc.)
- **Variants**: SHA256, SHA384, SHA512 — depending on the hash algorithm used

---

## What changes vs. what stays the same

| Element | Changes? | Frequency | Where |
|---|---|---|---|
| `Assured ID Root CA` | No | Never (expires 2031) | System (`/etc/ssl/certs`) |
| `Trusted Root G4` | No | Never (~expires 2038) | System (`/etc/ssl/certs`) |
| `TimeStamping 2025 CA1` | Yes | ~15 months | Embedded in the TSR |
| `Timestamp Responder 2025 1` | Yes | ~15 months | Embedded in the TSR |
| Your `.tsr` | No | Frozen at creation | Your file |

---

## Why 15 months for intermediate certificates?

Rule imposed by the **CA/Browser Forum** (the SSL/PKI industry consortium): code signing and timestamping certificates have short lifetimes to limit exposure in case of private key compromise.

The rationale: the lower you go in the hierarchy, the more the key is used (and thus exposed), so the faster it must rotate.

| Level | Private key usages |
|---|---|
| Root CA | ~10 times in its entire lifetime |
| Intermediate CA | ~a few thousand |
| Responder | Millions (one per TSR) |

---

## Contents of a `.tsr` file

An RFC 3161 TSR is a PKCS#7 token that embeds:
- The timestamped response (timestamp + file hash)
- The cryptographic signature
- The certificate chain (**excluding the Root CA**)

Root CAs are never embedded in TSRs — this is a universal convention, as they are assumed to already be present on the system.

---

## Future verification (in 5, 10, 20 years)

The TSR is **self-contained**: it embeds the 2025 certificates that signed the response.

`openssl ts -verify` verifies the signature **at the time it was made**, not at the time of verification. Expired 2025 certificates are not a problem.

**The only external dependency**: `DigiCert Trusted Root G4` (or `Assured ID Root CA`) must be present on the system.

```
In 2035:
  TSR contains the 2025 certs (expired but still present in the file)
    + Root G4 found in /etc/ssl/certs (still valid until ~2038)
      → Verification OK
```

If the Root CA disappears from the system (extreme case), solution: provide it manually via `-CAfile`.

---

## What verify_tsr.py does

```
1. extract_chain()        → extracts the certificates embedded in the TSR
2. print_chain_subjects() → prints the subject/issuer of each cert (informational)
3. get_root_issuer()      → reads the last issuer= in the chain → this is the Root CA to find
4. build_full_chain()     → looks for this Root CA in /etc/ssl/certs, concatenates chain + Root CA
5. verify()               → runs openssl ts -verify with the full chain
```

The final `full_chain` = certs extracted from the TSR + Root CA from the system.

---

## Events to watch

| Date | Event | Impact |
|---|---|---|
| ~every 15 months | Responder + CA1 rotation | None — new certs are in new TSRs |
| November 2031 | `Assured ID Root CA` expiration | None if `Trusted Root G4` is present on the system |
| ~2038 | `Trusted Root G4` expiration | DigiCert will publish a successor well in advance |

---

## What will happen to `DigiCert Assured ID Root CA` after 2031

### It does not disappear instantly

OS and browsers remove expired Root CAs **gradually**, sometimes years after expiration, to avoid breaking legacy systems. It will likely remain in `/etc/ssl/certs` for some time after 2031.

### TSRs signed before 2031 remain valid

The signature was made when the certificate was valid. `openssl ts -verify` accepts this — it is the very principle of long-term timestamping. A TSR is not invalidated by the subsequent expiration of the certificates that signed it.

### `Trusted Root G4` takes over

Before 2031, DigiCert will fully migrate to `Trusted Root G4` as the new trust anchor. It is already present on most systems. The chain will evolve from:

```
Assured ID Root CA → Trusted Root G4 → CA1 → Responder
```

to:

```
Trusted Root G4 (independent Root) → CA1 → Responder
```

### The residual risk

If in 2032:
- `Assured ID Root CA` has been removed from the system **AND**
- `Trusted Root G4` is not yet recognized as an independent trust anchor

then `verify_tsr.py` would fail to build the full chain. Solution: provide the Root CA manually via `-CAfile`.

**Recommendation**: keep the `.pem` files of the full certificate chain alongside each `.tsr` so the full chain can be reconstructed without relying on the system store.

---

## The cross-signing mechanism

### What signing a certificate means

Signing a certificate = signing **{identity + public key}** with one's own private key.

`Trusted Root G4` has:
- a private key (secret, never shared)
- a public key (distributed in certificates)

### What happened concretely

**Step 1** — DigiCert creates `Trusted Root G4` and self-signs it:
```
Trusted Root G4 signs {"CN=Trusted Root G4" + its own public key}
→ produces the self-signed certificate (in /etc/ssl/certs)
```

**Step 2** — DigiCert asks `Assured ID Root CA` to sign the same public key:
```
Assured ID Root CA signs {"CN=Trusted Root G4" + Trusted Root G4's public key}
→ produces the cross-signed certificate (embedded in the TSR)
```

Both certificates contain **exactly the same public key**. Only the signer differs.

### There are therefore two `Trusted Root G4` certificates

| Version | issuer | Where |
|---|---|---|
| Self-signed | itself | `/etc/ssl/certs` — used as independent Root |
| Cross-signed | `Assured ID Root CA` | Embedded in the TSR |

### Why this dual certificate?

To ensure the transition between the old and the new Root CA:

```
Recent systems  → trust Trusted Root G4 directly (self-signed)
Legacy systems  → chain up via Assured ID Root CA → cross-signed Trusted Root G4
```

### What cross-signing proves

`AID signs {"CN=Trusted Root G4" + TRG's public key}` proves:

> "AID attests that this public key genuinely belongs to DigiCert Trusted Root G4."

This cryptographic link is **permanently frozen** in the cross-signed certificate. It does not disappear in 2031. After AID expires, it will still be possible to prove that TRG genuinely belonged to DigiCert.

### The trust chain in terms of signatures

```
AID       signs the public key of → TRG
TRG       signs the public key of → CA1 (TimeStamping 2025)
CA1       signs the public key of → Responder 2025
Responder signs                   → your TSR
```

Each level certifies the public key of the level below. To verify the TSR, one walks up the chain verifying each signature with the public key of the level above.

---

## Long-term archiving and re-timestamping

### The problem at a 50-year horizon

Cryptography alone does not guarantee verifiability beyond ~30 years without periodic human intervention, for two reasons:
- Root CAs expire and are removed from systems
- Crypto algorithms (SHA256, RSA4096) may be broken by quantum computing (~2040+)

The solution is **re-timestamping**: periodically re-signing the previous TSR with fresh certificates and algorithms.

```
2025: TSR signed with Responder 2025 / SHA256 / RSA4096
2035: re-sign the 2025 TSR with 2035 certs
2045: re-sign the 2035 bundle with 2045 certs
...
```

Each layer proves that the previous layer existed and was intact at that point in time.

### Technical standards

| Standard | Description |
|---|---|
| **RFC 4998 — ERS** (Evidence Record Syntax) | Defines how to stack successive TSRs for long-term archiving |
| **ETSI EN 319 102 / PAdES LTV** | European standard for long-term signatures, integrates re-timestamping in signed PDFs |

### Services implementing re-timestamping

#### Institutional (most reliable at 50 years)

| Service | Country | Horizon | Target audience | Cost |
|---|---|---|---|---|
| **CINES** | France | 50–100 years | French public research | Free/near-free on application (funded by MESRI) |
| **BnF** | France | Indefinite | French publishers | Legal obligation (legal deposit) |
| **Österreichisches Staatsarchiv** | Austria | 50+ years | Institutions | On request |

#### Commercial

| Service | Horizon | Re-timestamping | Cost |
|---|---|---|---|
| **Preservica** | 50+ years | Yes | Several thousand €/year, on request |
| **Arkivum** | 50+ years | Yes | On request, research-oriented |
| **DocuSign LTV** | ~20 years | Partial | Enterprise pricing, on request |

#### eIDAS-qualified (Europe)

Under the eIDAS regulation, **QTSP** (Qualified Trust Service Providers) accredited by member states offer qualified timestamps with legal conservation obligations. In France, the list of accredited QTSPs is published by **ANSSI**.

### Crypto algorithm evolution

| Horizon | Risk |
|---|---|
| ~2030 | SHA1 already broken (retired since 2017) |
| ~2035 | RSA2048 potentially vulnerable |
| ~2040+ | RSA4096 and SHA256 under quantum pressure |

NIST standardized the first **post-quantum** algorithms in 2024 (ML-DSA). Serious services will need to migrate before RSA4096 is compromised.

### Implications for this project

For a 50-year horizon, the recommended strategy in order of priority:

1. **Current RFC 3161 TSR** — solid immediate proof (~15–30 years without intervention)
2. **Zenodo (CERN)** — institutional longevity, persistent DOI, distributed backups
3. **CINES** — if strong legal requirement, re-timestamping managed transparently, most serious option in the context of French public research

> Exact pricing for Preservica, Arkivum, and eIDAS QTSPs is on request and subject to change — consult their sites directly for up-to-date figures.

---

## Alternatives to DigiCert — Which TSA to choose?

### The problem with DigiCert

DigiCert is a US private company. Potential risks:
- Acquisition, bankruptcy, policy change
- Subject to US law (CLOUD Act)
- The URL `http://timestamp.digicert.com` may disappear

**Important**: for verification, this changes nothing — the TSR is self-contained, the DigiCert server is no longer needed once the `.tsr` has been generated.

### eIDAS-recognized RFC 3161 alternatives

| Service | Type | Free | Country |
|---|---|---|---|
| **DigiCert** | Private company | Yes (limited use) | USA |
| **Sectigo** | Private company | No | USA |
| **Bundesdruckerei (D-Trust)** | German public | No | Germany |
| **Certinomis / CertEurope** | ANSSI-accredited | No | France |
| **FreeTSA.org** | Non-profit | Yes | — |

For legal recognition in Europe, the TSA must be an **eIDAS-accredited QTSP**. The French list is published by ANSSI.

---

## OpenTimestamps — Decentralized proof via Bitcoin

### Principle

OpenTimestamps is an open-source project (created by Peter Todd, Bitcoin Core contributor) that anchors timestamps in the **Bitcoin blockchain**.

Instead of writing one transaction per document, it aggregates thousands of documents into a **Merkle tree** and writes a single Bitcoin transaction for all of them:

```
Document A ─┐
Document B ─┤→ Merkle root → 1 Bitcoin transaction (every ~6h)
Document C ─┤
Document D ─┘
```

### Why it is free

The cost of a Bitcoin transaction (~$5–15 depending on network congestion) is shared among thousands of users. Peter Todd and public calendar operators absorb this cost, considered a public good for the Bitcoin ecosystem.

**Estimated annual cost of the service:**
```
4 transactions/day × 365 days × $5–15 = ~$7,000 to $22,000/year
```
Distributed across several independent calendar operators.

### How it proves your document individually

You receive a `.ots` file — a **Merkle proof**: the mathematical path linking your document to the Merkle root inscribed in Bitcoin.

```
hash(your document) + Merkle path → Merkle root → Bitcoin transaction in block X
```

Verifiable by anyone with a Bitcoin node, without any third-party server.

### The fundamental safety net

Even if all OpenTimestamps servers shut down tomorrow, the proof remains **in Bitcoin**. All you need is the `.ots` file + a Bitcoin node to verify — forever, as long as Bitcoin exists.

This is fundamentally different from DigiCert: if DigiCert's OCSP server disappears, verification becomes more complex. With OpenTimestamps, there is no central server on which verification depends.

### Limitation

There is a ~6-hour delay between submission and inscription in Bitcoin (batch aggregation). It is not instantaneous like a DigiCert TSR.

### Overall comparison

| Approach | eIDAS legal | Decentralized | Free | Horizon | Re-timestamping |
|---|---|---|---|---|---|
| DigiCert RFC 3161 | Yes | No | Yes | ~30 years | No |
| ANSSI QTSP | Yes | No | No | ~30 years | No |
| OpenTimestamps (Bitcoin) | No | Yes | Yes | As long as Bitcoin | N/A |
| CINES | Yes | No | Yes (public) | 100 years | Yes |
| Arweave | No | Yes | One-shot | 200 years (theoretical) | N/A |

### Optimal recommendation

The combination **DigiCert + OpenTimestamps** on the same file is probably the most robust:
- DigiCert TSR → eIDAS-legally-recognized proof
- OpenTimestamps → decentralized proof anchored in Bitcoin, independent of any company

Two independent proofs that reinforce each other.
