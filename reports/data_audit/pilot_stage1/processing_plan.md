# Draft processing plan

Automatic suggestions only; human review and full-corpus evidence are pending.

No proposed rule is enabled. Do not use this file as a validated bulk-cleaning policy.

| Disposition | Records | Next step |
| --- | --- | --- |
| needs_cleaning | 2435 | Review noise candidates; build before/after fixtures. |
| needs_reextraction | 48 | Compare stored blocks/HTML with text offline. |
| needs_review | 74 | Resolve source/version/quality ambiguity. |
| unusable_currently | 1712 | Retain reasons; exclude from current content index without recrawling. |
| usable_as_is | 1771 | Verify sampled main content and create evidence versions. |

Each confirmed issue needs domain/type scope, affected counts, positive and negative examples, raw dependency,
CPU/RAM estimate, acceptance fixtures, and rollback conditions. Fill these after reviewing the sample packet.

For short-block candidates, repeated medical advice is a required counterexample to automatic template removal.

## Draft issues

- `audit_html_in_text`: 1 / 6040 records; see example IDs in proposed_rules.yaml.
- `audit_html_entities`: 6 / 6040 records; see example IDs in proposed_rules.yaml.
- `audit_repeated_lines`: 1727 / 6040 records; see example IDs in proposed_rules.yaml.
- `audit_possible_ui_text`: 1749 / 6040 records; see example IDs in proposed_rules.yaml.
- `audit_missing_title`: 1790 / 6040 records; see example IDs in proposed_rules.yaml.
