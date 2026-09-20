# Traceability matrix (v0.1.0)

Maps normative source invariants → packaged location → acceptance evidence.

| ID | Invariant | Packaged location | Evidence |
|---|---|---|---|
| T1 | Default-on is opt-in for public package; conversation on/off/status | `CONTRACT-SNIPPET.md`, `references/mode.md`, `references/install.md` | AC1, install docs; check_package |
| T2 | Three profiles: Astra-main, Sol-main, Auto-main | `references/mode.md`, `references/profiles.md`, `SKILL.md` | AC2, AC3, AC4; policy P1/P6/P7 |
| T3 | Explicit routing overrides surface preset | `references/profiles.md` | AC2 |
| T4 | Early Astra gate scoped to Sol-main/Auto-main (not Astra-main recursion) | `references/mode.md` (Astra-main section + gate section), `SOURCE-REVISION.md` | AC2, AC4; P6 |
| T5 | Auto-main: Astra does not implement | `references/mode.md`, `SKILL.md` | AC3; P1 |
| T6 | Closing alignment + no repair-budget reset | `references/mode.md`, `references/packets.md`, `references/runbooks.md` | AC3; P2–P5 |
| T7 | Claim-before-prompt; foreign/busy refuse | `references/claims.md`, `references/mode.md` | AC5; P10–P11; `test_claims.py` |
| T8 | No expiry / force-steal; matching-owner release | `references/claims.md`, `scripts/claims.py` | AC5; unit tests |
| T9 | Receipt-last; lifecycle ≠ done | `references/mode.md`, `references/packets.md` | AC6; P13 |
| T10 | Single writer; one awaited handle | `references/mode.md` | AC2 |
| T11 | Direct-work exceptions only | `references/mode.md`, `references/runbooks.md` | AC2; P15 |
| T12 | Workers non-recursive | `CONTRACT-SNIPPET.md`, `SKILL.md` | AC2 |
| T13 | Model verification / no silent substitute | `references/mode.md`, `adapters/herdr.md` | AC4; P12 |
| T14 | Privacy: no private host leakage in package | `scripts/check_package.py` patterns | AC8 |
| T15 | Stock herdr CLI skill remains distinct | `README.md`, `SKILL.md`, `adapters/herdr.md` | AC1 |

Automated text checks alone do not establish behavioral correctness (AC7).
