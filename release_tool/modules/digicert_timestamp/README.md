# digicert_timestamp

Requests a free RFC 3161 timestamp from the [DigiCert TSA](http://timestamp.digicert.com) for each configured file and produces a `.tsr` (timestamp response) file.

The timestamp certifies that a file with a given hash existed at a specific point in time. The `.tsr` is a signed ASN.1 structure verifiable with standard tools (`openssl ts`).

## How it works

1. Takes the pre-computed `identity_hash_algo` hash from the ZP pipeline input (no file re-read)
2. Builds a RFC 3161 `TimeStampReq` (DER-encoded) and POSTs it to `http://timestamp.digicert.com`
3. Saves the `TimeStampResp` as `<filename>.tsr` in the ZP output directory
4. Returns the `.tsr` path to ZP as a `MODULE_ENTRY` with `module_entry_type = "tsr"`

## Configuration

```yaml
modules:
  digicert_timestamp:
    full_chain: true   # embed TSA certificate chain in the response (default: true)
```

`full_chain: true` (recommended) embeds the full DigiCert certificate chain inside the `.tsr`, making it self-contained for verification without downloading external certificates.

`full_chain: false` produces a smaller `.tsr` but requires fetching the DigiCert CA certificates separately to verify.

### Supported hash algorithms

`identity_hash_algo` must be one of: `sha1`, `sha256`, `sha384`, `sha512`. MD5 is not supported by the RFC 3161 protocol.

## Verification

```bash
# Inspect the timestamp (hash, time, cert chain presence)
openssl ts -reply -in file.tsr -text

# Extract the embedded root CA from the TSR itself
openssl ts -reply -in file.tsr -token_out \
  | openssl pkcs7 -inform DER -print_certs \
  | awk '/CN = DigiCert Assured ID Root CA/,/END CERTIFICATE/' \
  | openssl x509 -out digicert-root.pem

# Verify the timestamp against the original file
openssl ts -verify -in file.tsr -data <original_file> -CAfile digicert-root.pem
# Expected: Verification: OK
```

## Further reading

- [`DIGICERT_TSA_CHAIN.md`](DIGICERT_TSA_CHAIN.md) — full trust chain explanation, certificate lifetimes, cross-signing, long-term archiving, and alternatives (OpenTimestamps, CINES, eIDAS QTSPs)
- [`verify_tsr.py`](verify_tsr.py) — standalone script to verify a `.tsr` against an original file; auto-resolves the Root CA from the system trust store

## Dependencies

- [`rfc3161ng`](https://pypi.org/project/rfc3161ng/) — RFC 3161 request/response encoding
- [`requests`](https://pypi.org/project/requests/) — HTTP POST to DigiCert TSA
