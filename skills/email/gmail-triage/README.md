# Gmail Triage

![A human-controlled system sorts a crowded inbox while protecting important mail](assets/hero.webp)

## Turn inbox chaos into a reviewable plan—not a deletion spree.

Gmail Triage helps an agent review new mail, surface high-volume senders, unsubscribe safely, create future-only filters, and explain where messages went.

You choose the senders. You choose the action. The tooling checks the plan, protects important mail, verifies the result, and keeps an undoable record.

## What you get

- **Fast inbox clarity:** rank noisy senders and see exact counts, recency, unread volume, and unsubscribe options.
- **Selected-only cleanup:** a recommendation is never treated as authorization.
- **Protected mail:** important senders and account-critical messages stay out of bulk actions.
- **Safer unsubscribe:** HTTPS, SSRF protection, bounded responses, TLS verification, and no redirects.
- **Receipts and recovery:** verify filters, resume partial work, undo recorded filters, and diagnose missing mail.

## How it works

1. **Review:** connect through Composio, verify the exact Gmail account, and build a bounded sender list.
2. **Choose:** approve exact addresses and one action for each—unsubscribe, archive and label, or trash future mail.
3. **Execute and prove:** create only the approved plan, read Gmail state back, and store an account-scoped receipt.

The agent supplies judgment. Composio supplies Gmail access. The Python helpers make the risky parts deterministic.

## Prove it offline first

No Gmail account is needed for the bundled self-test:

```bash
cd skills/email/gmail-triage
python3 scripts/triage_core.py selftest
python3 -m unittest discover -s tests -t tests
```

Then follow [Start here](references/quickstart.md) to connect Gmail, verify the account, save preferences, and run the first review.

## What it will not do

- “Review” or “triage” does not authorize sending, deleting, filtering, or unsubscribing.
- Cleanup affects only the exact senders and actions you approve.
- Future-only filters do not silently clean old messages.
- Nothing runs on a schedule unless you deliberately add external scheduling.

Mailbox-derived data and SQLite state stay in a private directory outside the repository. The committed fixtures are synthetic.
