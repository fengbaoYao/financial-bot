# Third-party attribution — local research adapters

The four adapters in this package were written for Financial Bot after a
read-only review of [Vibe-Research](https://github.com/simonlin1212/Vibe-Research)
at commit `db3a7c60afcb643abdf36933294765d1cbfc2b9c` (MIT, © 2026 simonlin1212).
The endpoint shapes, field semantics and failure rules were informed by that
project's data-access documentation. No Vibe-Research source file is vendored
wholesale here; the implementation is a small independent adapter limited to
CNINFO announcements, THS consensus EPS, CSI index constituents and Sina
financial statements.

Vibe-Research's data layer records that parts of its own provider code were
adapted from:

- [a-stock-data](https://github.com/simonlin1212/a-stock-data) — Apache License 2.0
- [global-stock-data](https://github.com/simonlin1212/global-stock-data) — Apache License 2.0
- [investment-news](https://github.com/simonlin1212/investment-news) — MIT

Those upstream repositories and their data providers retain their own copyright
and terms. The Financial Bot repository-level MIT license does **not** grant rights
to third-party data; these adapters are local/internal research use only and are
force-disabled in hosted authenticated mode. See
`docs/vibe-research-data-sources.md` for the source-by-source assessment.
