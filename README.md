# ASM Purchase Lookup

Look up purchase information for a list of device serial numbers in Apple School Manager (ASM) and write the results to a CSV.

Give it a text file of serial numbers. For each one, it calls the Apple School Manager API to get the device record and its AppleCare/warranty coverage, then writes one row per serial with the model, order number, purchase source, and a best-available purchase date.

It was written for iPads, but it works for any device in ASM: Macs, iPads, Apple TVs.

## What you get

| Column | Meaning |
|---|---|
| `lookup_serial` | The serial number from your input file |
| `result` | `OK`, `NOT_FOUND`, or `ERROR <status>: <message>` |
| `purchase_date_best` | Best available purchase date (YYYY-MM-DD). See [Purchase dates](#purchase-dates) |
| `purchase_date_source` | Which field `purchase_date_best` came from |
| `serialNumber` | Serial number as ASM reports it |
| `deviceModel`, `productFamily`, `productType` | Model details, e.g. `iPad (10th generation)`, `iPad`, `iPad13,18` |
| `deviceCapacity`, `color`, `partNumber` | Hardware configuration |
| `orderNumber` | Apple's or the reseller's order number (not your internal PO number) |
| `orderDateTime` | Order date, when Apple has it |
| `purchaseSourceType` | `APPLE`, `RESELLER`, or `MANUALLY_ADDED` |
| `purchaseSourceId` | Identifier for the Apple account or reseller |
| `addedToOrgDateTime` | When the device was added to your ASM organization |
| `status` | Device status in ASM, e.g. `ASSIGNED`, `UNASSIGNED` |
| `warranty_start` | Earliest warranty or AppleCare coverage start date |
| `coverage_description` | Description of the coverage with the latest end date |
| `coverage_end` | End date of that coverage |
| `coverage_status` | Status of that coverage, e.g. `ACTIVE`, `INACTIVE` |

## Requirements

- Python 3.9 or later
- An Apple School Manager account with permission to create API accounts (Administrator role)
- The packages in `requirements.txt`

## Setup

### 1. Create an API account in Apple School Manager

1. Sign in to [Apple School Manager](https://school.apple.com).
2. Go to **Preferences > API** (in some versions, **Settings > API**) and create a new API account.
3. Download the private key (`.pem` file). Apple only lets you download it once, so store it somewhere safe.
4. Copy the **Client ID** (starts with `SCHOOLAPI.`) and the **Key ID**.

### 2. Install dependencies

```bash
pip3 install -r requirements.txt
```

### 3. Set environment variables

```bash
export ASM_CLIENT_ID="SCHOOLAPI.xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
export ASM_KEY_ID="xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
export ASM_KEY_PATH="/path/to/asm_private_key.pem"
```

Keep the `.pem` file out of the repository. The included `.gitignore` excludes `*.pem`.

## Usage

Create a text file with one serial number per line:

```
DMPXXXXXXXXX
F9FXXXXXXXXX
GG7XXXXXXXXX
```

Blank lines are skipped, surrounding whitespace is trimmed, and serials are converted to uppercase.

Run the script:

```bash
python3 asm_purchase_lookup.py serials.txt
```

Progress prints to the terminal:

```
[1/3] DMPXXXXXXXXX: OK 2023-07-18 (orderDateTime)
[2/3] F9FXXXXXXXXX: OK 2022-08-02 (warranty_start)
[3/3] GG7XXXXXXXXX: NOT_FOUND  (no date)
Wrote ipad_purchase_info.csv
```

Results are written to `ipad_purchase_info.csv` in the current directory. An existing file with that name is overwritten.

### Debug mode

```bash
python3 asm_purchase_lookup.py serials.txt --debug
```

Prints the raw device attributes and AppleCare coverage JSON for each serial. Use it to see exactly what Apple returns, for example to check whether `orderDateTime` is empty for a device.

## Purchase dates

Apple doesn't fill in `orderDateTime` for every device. The script picks the first date it finds from these fields, in order of reliability, and records which one it used in `purchase_date_source`:

1. **`orderDateTime`**: the order date from Apple or the reseller.
2. **`warranty_start`**: the earliest warranty or AppleCare coverage start date. Apple's limited warranty begins at purchase, so this is usually within days of the actual purchase.
3. **`addedToOrgDateTime`**: when the device was assigned to your ASM organization. Treat it as a rough fallback, since it can lag the purchase by weeks or more.

## Limitations

- **Only devices in ASM are found.** The API covers devices that came in through Apple or an authorized reseller or carrier, or that were added manually with Apple Configurator. A device bought at retail and never added to ASM returns `NOT_FOUND`.
- **No pricing.** ASM doesn't store what you paid. To get cost per device, join this CSV to a serial-level order export from your reseller or Apple, matching on `serialNumber` or `orderNumber`.
- **`orderNumber` is the vendor's order number**, not your internal purchase order number. Matching it to your purchasing system requires the vendor order number to be recorded there.
- **Two API calls per serial**, one for the device and one for coverage. Large lists take a while. The script waits and retries when Apple returns HTTP 429 (rate limited).

## Troubleshooting

**`ValueError: Could not deserialize key data ... ASN.1 parsing error`**

The key file's PEM header doesn't match its contents, which happens with some keys downloaded from ASM. The script retries with the other header label (`PRIVATE KEY` or `EC PRIVATE KEY`) automatically. If it still fails, check that `ASM_KEY_PATH` points to the right file and that the file hasn't been edited.

**`NotOpenSSLWarning: urllib3 v2 only supports OpenSSL 1.1.1+ ... LibreSSL`**

Harmless on macOS's built-in Python 3.9. To silence it, run `pip3 install 'urllib3<2'`, or use Python from Homebrew or python.org.

**HTTP 401 or 400 when requesting a token**

Check that `ASM_CLIENT_ID` and `ASM_KEY_ID` match the API account the key belongs to, and that the API account hasn't been revoked in ASM.

**Every serial returns `NOT_FOUND`**

Confirm the devices appear in the ASM web interface under **Devices**. Also check that you're using Apple School Manager credentials; Apple Business Manager uses a different API host and scope.

## How it works

1. Builds a JWT client assertion signed with your ES256 private key.
2. Exchanges it for an OAuth access token at `https://account.apple.com/auth/oauth2/token` with scope `school.api`. Tokens are refreshed before they expire (about an hour).
3. For each serial, calls:
   - `GET https://api-school.apple.com/v1/orgDevices/{serial}`
   - `GET https://api-school.apple.com/v1/orgDevices/{serial}/appleCareCoverage`
4. Writes one CSV row per serial.

## References

- [Apple School Manager API](https://developer.apple.com/documentation/appleschoolmanagerapi)
- [Get Device Information](https://developer.apple.com/documentation/appleschoolmanagerapi/get-orgdevice-information)
- [Implementing OAuth for the Apple School and Business Manager API](https://developer.apple.com/documentation/apple-school-and-business-manager-api/implementing-oauth-for-the-apple-school-and-business-manager-api)
