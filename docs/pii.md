<div align="center">

<img src="assets/logo.png" alt="flycanon" width="380" />

### **PII guardrail**

</div>

---

flycanon ingests text from heterogeneous sources -- contracts,
support tickets, internal wikis. Any of those can carry personal
information that shouldn't enter the canonical knowledge layer
unredacted (emails, SSNs, phone numbers, credit cards, IBANs).

The intake pipeline runs a configurable PII scan against every
inbound payload **and every replacement payload**, then applies a
policy:

| Policy   | Behaviour |
|----------|-----------|
| `disabled` | Skip the scan entirely. Useful in dev / fixture replay. |
| `warn`    | Log findings on the row's `metadata.pii_findings` array but ingest as-is. |
| `redact`  | Rewrite the content with `[REDACTED-<kind>]` markers in place; record findings on metadata. |
| `reject`  | Raise `PiiPolicyViolation` -- the intake call returns `422 pii_violation` and nothing lands. |

## Configuration

`CanonSettings`:

* `pii_scanner` -- `regex` (default) or `disabled`. The regex scanner
  ships with patterns for email, US SSN, US phone, credit card
  (Luhn-checked), and IBAN. Adding a pluggable LLM scanner is on the
  roadmap.
* `pii_policy` -- one of the four values above.

Both are environment-overrideable: `FLYCANON_PII_SCANNER=regex`,
`FLYCANON_PII_POLICY=redact`.

## Where it runs

The scan happens in `IntakeService` once the merged-artifact text is
ready -- a contaminated child artefact in an archive triggers the
same policy as a top-level hit.

Both code paths are covered:

* `POST /api/v1/sources` (and `:bulk`, `:async`) -- initial intake.
* `PUT /api/v1/sources/{id}` -- re-ingest path. A clean v1 of a file
  can be replaced with a v2 that introduces emails / SSNs / etc.; the
  policy must catch that the same way as a fresh upload.

## Findings shape

Every finding lands on the source row's `metadata.pii_findings`:

```json
{
  "pii_findings": [
    { "kind": "email", "start": 1234, "end": 1257 },
    { "kind": "ssn",   "start": 4500, "end": 4511 }
  ]
}
```

`start` / `end` are character offsets into the merged (post-archive-
expansion) text view, so the inbox UI can highlight the hit even when
the original payload was a binary.

## Failure shape (`reject` policy)

The intake controller maps `PiiPolicyViolation` to RFC 7807:

```json
{
  "type": "about:blank",
  "title": "PII detected",
  "status": 422,
  "detail": "ingest rejected: 2 personal-data finding(s) (kinds: email, ssn)",
  "code": "pii_violation",
  "findings": [
    { "kind": "email", "start": 1234, "end": 1257 },
    { "kind": "ssn",   "start": 4500, "end": 4511 }
  ]
}
```

Callers can use `findings` to surface a precise diagnostic in their
own UI, or to short-circuit a bulk job.
