# Task packet (synthetic)

- **job_id:** `demo-exec-0001`
- **role:** ordinary implementer — no redelegation
- **outcome:** Implement `--dry-run` on the sample CLI; tests green.
- **writable:** `src/sample_cli/**`, `tests/test_main.py`
- **criteria:**
  - `M1` (mandatory): `--dry-run` prints the planned action and writes nothing
  - `M2` (mandatory): default invoke without the flag unchanged
  - `O1` (optional): help text mentions the flag
- **verify:** `python3 -m unittest discover -s tests -t .`
- **repair_budget:** used=0 remaining=1
- **receipt_last:** `artifacts/demo-exec-0001.receipt.json`
