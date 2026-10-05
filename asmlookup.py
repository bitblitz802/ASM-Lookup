#!/usr/bin/env python3
"""
Look up purchase info for a list of iPad serial numbers via the
Apple School Manager API and write the results to CSV.

Setup:
  pip install requests pyjwt cryptography
  In ASM: Preferences > API > create an API account; note Client ID and Key ID,
  download the private key (.pem).

Usage:
  export ASM_CLIENT_ID="SCHOOLAPI.xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
  export ASM_KEY_ID="xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
  export ASM_KEY_PATH="./asm_private_key.pem"
  python asm_purchase_lookup.py serials.txt [--debug]

  --debug prints the raw device and AppleCare JSON for each serial.
"""
import csv
import json
import os
import sys
import time
import uuid

import jwt
import requests

TOKEN_URL = "https://account.apple.com/auth/oauth2/token"
AUDIENCE = "https://account.apple.com/auth/oauth2/v2/token"
API_BASE = "https://api-school.apple.com/v1"
SCOPE = "school.api"

FIELDS = [
    "serialNumber", "deviceModel", "productFamily", "productType",
    "deviceCapacity", "color", "partNumber",
    "orderNumber", "orderDateTime", "purchaseSourceType", "purchaseSourceId",
    "addedToOrgDateTime", "status",
]


def load_private_key(key_path):
    """Load the ASM EC private key, tolerating a mislabeled PEM header.

    Some ASM/ABM key downloads carry an 'EC PRIVATE KEY' label around PKCS#8
    data (or the reverse), which cryptography refuses to parse. If the file
    fails to load as-is, retry with the other label.
    """
    from cryptography.hazmat.primitives.serialization import load_pem_private_key

    with open(key_path, "rb") as f:
        pem = f.read()

    # Drop any 'EC PARAMETERS' block that precedes the key.
    if b"-----BEGIN EC PARAMETERS-----" in pem:
        pem = pem[pem.index(b"-----BEGIN", pem.index(b"-----END EC PARAMETERS-----") + 1):]

    candidates = [pem]
    if b"EC PRIVATE KEY" in pem:
        candidates.append(pem.replace(b"EC PRIVATE KEY", b"PRIVATE KEY"))
    elif b"PRIVATE KEY" in pem:
        candidates.append(pem.replace(b"PRIVATE KEY", b"EC PRIVATE KEY"))

    last_err = None
    for c in candidates:
        try:
            return load_pem_private_key(c, password=None)
        except ValueError as e:
            last_err = e
    raise ValueError(f"Could not load private key from {key_path}: {last_err}")


def get_token(client_id, key_id, key_path):
    private_key = load_private_key(key_path)
    now = int(time.time())
    assertion = jwt.encode(
        {
            "iss": client_id,
            "sub": client_id,
            "aud": AUDIENCE,
            "iat": now,
            "exp": now + 3600,
            "jti": str(uuid.uuid4()),
        },
        private_key,
        algorithm="ES256",
        headers={"kid": key_id},
    )
    r = requests.post(
        TOKEN_URL,
        data={
            "grant_type": "client_credentials",
            "client_id": client_id,
            "client_assertion_type": "urn:ietf:params:oauth:client-assertion-type:jwt-bearer",
            "client_assertion": assertion,
            "scope": SCOPE,
        },
        timeout=30,
    )
    r.raise_for_status()
    return r.json()["access_token"]


class ASM:
    def __init__(self, client_id, key_id, key_path):
        self.creds = (client_id, key_id, key_path)
        self.token = None
        self.token_time = 0

    def _headers(self):
        # Tokens last ~1 hour; refresh a bit early.
        if not self.token or time.time() - self.token_time > 3300:
            self.token = get_token(*self.creds)
            self.token_time = time.time()
        return {"Authorization": f"Bearer {self.token}"}

    def get(self, path, params=None):
        for attempt in range(6):
            r = requests.get(f"{API_BASE}{path}", headers=self._headers(),
                             params=params, timeout=30)
            if r.status_code == 429:
                wait = int(r.headers.get("Retry-After", 2 ** attempt))
                time.sleep(wait)
                continue
            if r.status_code == 401 and attempt == 0:
                self.token = None  # force refresh once
                continue
            return r
        return r


def main():
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    serial_file = sys.argv[1]
    debug = "--debug" in sys.argv

    asm = ASM(os.environ["ASM_CLIENT_ID"], os.environ["ASM_KEY_ID"],
              os.environ["ASM_KEY_PATH"])

    with open(serial_file) as f:
        serials = [s.strip().upper() for s in f if s.strip()]

    out_fields = (["lookup_serial", "result", "purchase_date_best",
                   "purchase_date_source"] + FIELDS +
                  ["warranty_start", "coverage_description", "coverage_end",
                   "coverage_status"])

    with open("ipad_purchase_info.csv", "w", newline="") as out:
        # extrasaction="ignore" so new attributes Apple adds don't break the CSV
        w = csv.DictWriter(out, fieldnames=out_fields, extrasaction="ignore")
        w.writeheader()
        for i, serial in enumerate(serials, 1):
            row = {"lookup_serial": serial}
            # No sparse fieldset: request every attribute Apple returns
            r = asm.get(f"/orgDevices/{serial}")
            if r.status_code == 404:
                row["result"] = "NOT_FOUND"
            elif not r.ok:
                row["result"] = f"ERROR {r.status_code}: {r.text[:200]}"
            else:
                row["result"] = "OK"
                attrs = r.json()["data"]["attributes"]
                if debug:
                    print(json.dumps(attrs, indent=2))
                row.update(attrs)

                # AppleCare / limited warranty: the earliest coverage start
                # is normally the purchase (or ship) date.
                c = asm.get(f"/orgDevices/{serial}/appleCareCoverage")
                if c.ok and c.json().get("data"):
                    covs = [d["attributes"] for d in c.json()["data"]]
                    if debug:
                        print(json.dumps(covs, indent=2))
                    starts = [a.get("startDateTime") for a in covs if a.get("startDateTime")]
                    if starts:
                        row["warranty_start"] = min(starts)
                    latest = max(covs, key=lambda a: a.get("endDateTime") or "")
                    row["coverage_description"] = latest.get("description")
                    row["coverage_end"] = latest.get("endDateTime")
                    row["coverage_status"] = latest.get("status")

                # Best available purchase date, in order of reliability
                for src in ("orderDateTime", "warranty_start", "addedToOrgDateTime"):
                    if row.get(src):
                        row["purchase_date_best"] = str(row[src])[:10]
                        row["purchase_date_source"] = src
                        break
            w.writerow(row)
            print(f"[{i}/{len(serials)}] {serial}: {row['result']} "
                  f"{row.get('purchase_date_best') or ''} "
                  f"({row.get('purchase_date_source') or 'no date'})")

    print("Wrote ipad_purchase_info.csv")


if __name__ == "__main__":
    main()
