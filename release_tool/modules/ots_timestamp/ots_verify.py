"""Verify an OpenTimestamps proof (.ots) against a file."""

import time as _time
from binascii import hexlify
from pathlib import Path

from opentimestamps.core.notary import (
    BitcoinBlockHeaderAttestation, PendingAttestation,
)
from opentimestamps.core.serialize import StreamDeserializationContext
from opentimestamps.core.timestamp import DetachedTimestampFile

from _shared import create_emitter
from ots_timestamp import verify_block_attestation

emit = create_emitter("ots_timestamp")


def verify_file(file_path: Path, ots_path: Path, check_blockchain: bool = True) -> bool:
    """Verify that an .ots proof matches the given file.

    1. Hash match: file digest == proof digest
    2. Attestation status: pending or Bitcoin-confirmed
    3. If Bitcoin-confirmed and check_blockchain: verify merkle root via Blockstream API
    """
    emit("detail", "Verifying '{filename}' against '{ots}'...",
         filename=file_path.name, ots=ots_path.name, name="verify.start")

    # Load the .ots proof
    try:
        with open(ots_path, "rb") as fd:
            ctx = StreamDeserializationContext(fd)
            detached = DetachedTimestampFile.deserialize(ctx)
    except Exception as e:
        emit("error", "Failed to load OTS proof: {error}",
             error=str(e), name="verify.load_failed")
        return False

    # Compute the file hash using the same op as the proof
    hash_op = detached.file_hash_op
    algo = getattr(hash_op, "TAG_NAME", type(hash_op).__name__)

    try:
        with open(file_path, "rb") as fd:
            actual_digest = hash_op.hash_fd(fd)
    except Exception as e:
        emit("error", "Failed to hash file: {error}",
             error=str(e), name="verify.hash_failed")
        return False

    proof_digest = detached.file_digest

    emit("detail", "Algorithm: {algo}", algo=algo, name="verify.algo")
    emit("detail", "File:  {hash}", hash=hexlify(actual_digest).decode(),
         name="verify.hash.file")
    emit("detail", "Proof: {hash}", hash=hexlify(proof_digest).decode(),
         name="verify.hash.proof")

    if actual_digest != proof_digest:
        emit("error", "MISMATCH — file hash does not match proof",
             name="verify.hash.mismatch")
        return False

    emit("step_ok", "Digest match", name="verify.hash.match")

    # Classify attestations using built-in traversal
    bitcoin_atts = []
    pending_atts = []
    for msg, att in detached.timestamp.all_attestations():
        if isinstance(att, BitcoinBlockHeaderAttestation):
            bitcoin_atts.append((msg, att))
        elif isinstance(att, PendingAttestation):
            pending_atts.append(att)

    if bitcoin_atts:
        all_ok = True
        for msg, att in bitcoin_atts:
            emit("step_ok", "Bitcoin attestation at block {height}",
                 height=att.height, name="verify.attestation.bitcoin")

            if check_blockchain:
                emit("detail", "Verifying block {height} via Blockstream API...",
                     height=att.height, name="verify.block.start")
                try:
                    header = verify_block_attestation(msg, att)
                except ValueError as e:
                    emit("error", str(e), name="verify.block.mismatch")
                    all_ok = False
                    continue

                if header:
                    block_time = _time.strftime("%Y-%m-%d %H:%M:%S UTC",
                                                _time.gmtime(header["timestamp"]))
                    emit("step_ok", "Block {height} verified — file existed at or before {time}",
                         height=att.height, time=block_time,
                         block_hash=header["hash"], name="verify.block.ok")
                else:
                    emit("warn", "Could not verify block {height} (API unreachable)",
                         height=att.height, name="verify.block.unreachable")

        if all_ok:
            emit("step_ok", "Verified: {filename}",
                 filename=file_path.name, name="verify.ok")
        else:
            emit("error", "Blockchain verification failed",
                 name="verify.failed")
        return all_ok

    if pending_atts:
        for att in pending_atts:
            uri = att.uri.decode() if isinstance(att.uri, bytes) else str(att.uri)
            emit("detail", "Pending: {calendar}", calendar=uri,
                 name="verify.attestation.pending")
        emit("warn", "PENDING — hash matches but not yet confirmed on Bitcoin. "
             "Run 'upgrade' to check for confirmation.",
             name="verify.pending")
        return True

    emit("error", "No attestations found in proof", name="verify.no_attestations")
    return False
