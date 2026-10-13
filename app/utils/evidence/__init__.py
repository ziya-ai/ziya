"""Evidence ledger: where a reported number came from (design/evidence-ledger.md).

``store`` holds observations, claims and verdicts as signed, append-only JSONL
under ``<project>/.ziya/ledger/``; ``verify`` re-checks claims; ``refs``
resolves a citation such as ``s1.per_unit.distribution.median`` to a value.

The server is the only writer: ``.ziya/ledger/`` is in the write policy's
``SERVER_ONLY_PATHS``, so ``file_write`` and the shell cannot append records.
"""
from .store import Ledger, LedgerError, ledger_for  # noqa: F401
