"""
Cost layer: turns metered token usage into money.

  app/config/pricing.py      list-price catalog (data) + user overlay
  app/cost/rate_plans.py     RatePlan seam: public / override / zero / discount table
  app/cost/pricer.py         price(record) -> CostLine
  app/cost/meter.py          executor hook: open (estimate) / close (actual)
  app/storage/usage_ledger.py SQLite ledger and rollups

Design: design/CostModel.md.  User docs: Docs/CostAccounting.md.
"""
