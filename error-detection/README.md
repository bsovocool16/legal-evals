# Error detection: public records against the registries that decide them

Three mechanical checks that compare what a public legal document says, or what a set of public filings shows, with the public record that decides the legal question. Each is a screen, not a judgment: it produces candidates with their evidence for a person to review. This folder holds code and tests only. No findings are included, and no company is named.

All three checks favour precision over recall. A checker that reports false positives is worse than one that misses errors, because every false positive costs a reviewer's time and erodes trust in the rest of the list. Each rule below exists because a naive version produced a false positive, and each has negative test cases (inputs that look like a hit and must not be reported).

```sh
pip install requests pytest
python -m pytest -q                       # 43 tests, no network
python -m error_detection --help
```

Network commands need `SEC_USER_AGENT` (the SEC's fair-access policy asks for a name and an email address, for example `export SEC_USER_AGENT="Jane Doe jane@example.com"`) and, for USPTO lookups, `USPTO_API_KEY` (a USPTO Open Data Portal key).

## 1. Patents cited in SEC filings, against USPTO status

`error_detection/joins/patents.py`, `error_detection/sources/uspto.py`; command `patents-join`.

A US patent expires early if its maintenance fees are not paid (35 U.S.C. 41(b)). An annual report that describes a patent as part of the company's protection after the USPTO shows it expired for non-payment may overstate what the company owns. The check:

1. Extracts US patent numbers from filings (downloaded with `edgar-fetch`), with the surrounding text, so that each flag can be read in context. It reads single citations and lists ("U.S. Patent Nos. 10,234,567, 10,345,678 and 11,456,789"), reissue and design patents, and plain digits.
2. Skips citations that are probably foreign (a country or office name such as "European" or "Japanese" just before "Patent No." with no "U.S." prefix), application numbers ("Application No. 16/123,456"), and numbers outside the range of recent US utility patents.
3. Looks each number up in the USPTO Open Data Portal (cached on disk) and reports the status text, grant and status dates, expiry events, and the recorded assignment history (latest assignee).

What it does not decide: whether the filing actually claims the patent as in force (a list of expired patents is not an error), whether the patent belongs to the filer (assignment records lag), or whether the lapse was reversed by a petition to accept late payment (35 U.S.C. 41(c)). Those are left to review.

## 2. Mining claims cited in SEC filings, against BLM records

`error_detection/sources/blm.py`; command `blm-check`.

Unpatented mining claims on federal land need a maintenance fee for each claim on or before September 1 of each year (30 U.S.C. 28f(a)), and failure to pay "shall conclusively constitute a forfeiture" of the claim by operation of law (30 U.S.C. 28i). Mining issuers often list their claims by serial number in technical reports and annual reports. The check extracts the serials (legacy state serials such as `NMC1234567`, and current MLRS serials such as `NV101234567`), looks them up in the BLM's public GIS layers, reads each claim's public case record (status and next payment due date), and classifies each claim as paid, no payment recorded, closed in the record, too early to tell, or unknown.

Precision rules, from a first trial in which every apparent lapse turned out to be a relocation of the same ground:

1. A serial that is not found in the GIS layers is "unknown", never "closed": the Closed layer covers only recently modified records.
2. Nothing is judged until the payment deadline plus a posting delay (15 days by default) has passed, because mailed payments post late.
3. "No payment recorded" is a candidate, not a finding. Before calling a lapse, a reviewer must look for relocations of the same ground by the claimant, its optionees or affiliates, and for a small-miner waiver, which claimants holding no more than ten claims may file instead of paying (30 U.S.C. 28f(d)). Neither check is automated.

This module was ported for this repository from earlier research scripts, with tests added. The classification and extraction rules are tested; the network clients (GIS queries and the MLRS case-record endpoint) are not, and the MLRS endpoint depends on a public framework build id that changes when the site is updated.

## 3. Section 16(b) short-swing screen over insider-transaction data

`error_detection/screens/short_swing.py`; command `short-swing`.

Section 16(b) of the Securities Exchange Act (15 U.S.C. 78p(b)) makes a director, an officer or a holder of more than 10 percent of a class of equity securities liable to the issuer for any profit from a purchase and a sale, or a sale and a purchase, within any period of less than six months. Liability arises "irrespective of any intention" of the insider (id.), and courts compute the profit by matching the lowest-priced purchases with the highest-priced sales inside the window, in either order (Smolowe v. Delendo Corp., 136 F.2d 231 (2d Cir. 1943); Gratz v. Claughton, 187 F.2d 46 (2d Cir. 1951)). The screen reads the SEC's quarterly insider-transaction data sets (Forms 3, 4 and 5), groups trades by issuer and reporting person, and computes the maximum matched profit within the statutory window.

Legal rules built into the matching:

- **The window.** "Less than six months" ends at midnight two days before the corresponding date in the sixth month (Stella v. Graham-Paige Motors Corp., 232 F.2d 299 (2d Cir. 1956), as applied in Morales v. Reading & Bates Offshore Drilling Co., 392 F. Supp. 41, 45 (N.D. Okla. 1975): a sale on April 17 matches a purchase through October 15 but not October 16). Where the corresponding date does not exist (August 31 to February), the last day of the month is used; that choice is untested in the case law and is marked as such in the code.
- **Matching.** Pairs are taken in order of price spread, highest first, and shares are allocated greedily, in both orders (purchase then sale, or sale then purchase).
- **Limitations.** Suit must be brought within two years after the profit was realized, so pairs whose later trade is more than two years before the as-of date are dropped.
- **Exempt transactions.** Only open-market purchases and sales (codes P and S) are screened. Grants, option exercises, tax withholding and dispositions to the issuer (codes A, M, F and D) are usually exempt under Rule 16b-3 and are skipped. Derivative transactions are not screened yet.
- **Ten-percent holders** are reported separately, because the purchase that makes a holder a 10 percent owner does not count (Foremost-McKesson, Inc. v. Provident Securities Co., 423 U.S. 232 (1976)) and the data sets do not show holdings at both ends reliably.
- **Foreign private issuers** are exempt from Section 16 (Rule 3a12-3(b)); the data sets do not mark them, so the command drops issuers whose latest annual report is a Form 20-F or 40-F.

Precision rules against data errors:

- A purchase and a sale match only if they are the same security, after normalizing titles ("Ordinary Shares" and "Common Stock, par value $0.001" both read as common stock).
- A pair whose prices differ by more than a factor of two is held out: an unadjusted stock split between the two trades produces exactly that pattern. A price far from the median for the same security (a total entered as a per-share price) is flagged the same way.
- Trades whose footnotes mention a non-US currency, an issuer offering or plan (a possible Rule 16b-3 exemption), or a disgorgement already agreed are held out.
- Joint filings (one report for several owners) are grouped into one family so the same trade is counted once, and a Form 4/A replaces the trade it amends, even if it corrects the price.
- A same-day sale and purchase of the same shares at the same price in one filing is treated as a transfer between the insider's own accounts.
- Rows whose own footnotes contradict their code (a "purchase" of shares that "were sold"; a "sale" that was tax withholding retained by the issuer; an acquired-or-disposed flag that disagrees with the code) are held out as coding errors.

A candidate is a matched profit above a threshold (by default $50,000), with the pairs and links to the filings behind them. It is not a finding of liability: exemptions the data sets cannot show (for example, transactions approved under Rule 16b-3(d)), the person's status on each trade date, and beneficial ownership through entities all need review.

## Layout

- `error_detection/sources/`: clients for SEC EDGAR full-text search and downloads (`edgar.py`), the USPTO Open Data Portal (`uspto.py`) and the BLM records (`blm.py`).
- `error_detection/joins/patents.py`, `error_detection/screens/short_swing.py`: the checks.
- `error_detection/text.py`: HTML to plain text for filings, dropping struck-through text (amended documents filed with deletions shown as strikethrough would otherwise read as "fiveseven").
- `error_detection/cli.py`: the commands.
- `tests/`: unit tests for each check, with negative cases, using synthetic records only. None needs network access.
