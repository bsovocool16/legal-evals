#!/usr/bin/env python3
"""Verify the RFC 3161 timestamps in stamps/ and the stamped files in stamped/.

For each stamp <name>:
1. `openssl ts -verify` checks that freetsa.org signed stamps/<name>/files.sha256 (certificates in tsa/), and the signed
   time is read from the reply itself.
2. Every file the listing names is looked up at stamped/<name>/<path> (the path it had when it was stamped) and its
   SHA-256 compared with the listing. Files not published here are reported as withheld.

    python3 verify_stamps.py            # needs openssl on the PATH

Exit status 0 when every signature verifies and every published file matches.
"""
from __future__ import annotations

import hashlib
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
TSA = HERE / "tsa"


def verify_token(stamp: Path) -> tuple[bool, str]:
    v = subprocess.run(["openssl", "ts", "-verify", "-data", str(stamp / "files.sha256"), "-in", str(stamp / "response.tsr"),
                        "-CAfile", str(TSA / "freetsa-cacert.pem"), "-untrusted", str(TSA / "freetsa-tsa.crt")],
                       capture_output=True, text=True)
    ok = "Verification: OK" in (v.stdout + v.stderr)
    t = subprocess.run(["openssl", "ts", "-reply", "-in", str(stamp / "response.tsr"), "-text"], capture_output=True, text=True).stdout
    m = re.search(r"Time stamp:\s*(.+)", t)
    return ok, (m.group(1).strip() if m else "?")


def main() -> int:
    bad = 0
    for stamp in sorted(p for p in (HERE / "stamps").iterdir() if p.is_dir()):
        ok, when = verify_token(stamp)
        matched = mismatched = withheld = 0
        for line in (stamp / "files.sha256").read_text().splitlines():
            digest, path = line.split("  ", 1)
            f = HERE / "stamped" / stamp.name / path
            if not f.exists():
                withheld += 1
            elif hashlib.sha256(f.read_bytes()).hexdigest() == digest:
                matched += 1
            else:
                mismatched += 1
                print(f"  MISMATCH {stamp.name}: {path}")
        bad += (not ok) + mismatched
        print(f"{stamp.name:28s} signature {'OK' if ok else 'FAILED':6s} {when:28s} "
              f"files: {matched} match, {mismatched} differ, {withheld} withheld")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
