# Demo task

This synthetic directory is a miniature research library. Organize every file
under `inbox/` into a reproducible directory hierarchy, complete metadata where
the evidence supports it, rename by the template in `case.json`, detect the
duplicated download and the preprint/formal pair, and leave every decision
traceable.

Rules:

- Use `sidecars`, `case.json`, embedded text, then filename hints in that order.
- Never invent authors, years, journals or DOIs. Unknown stays null.
- Never overwrite or delete a file.
- Dry-run first; apply only with an explicit `--apply`.
- Write `result.json` with every key from `output_contract` and `case_id`.
