"""Managed job runner (design/job-runner.md).

A job is one process tree that outlives a tool call: started under the
shell tool's exact command and write policy, run by ``ziya-jobwrap`` in
its own session, journaled in the shadow record format, waited on with
the shared ``journal_wait`` conditions, killed as a group, and recorded
in the evidence ledger on exit.

  policy.py   all-or-nothing pre-check (same code as the shell tool)
  wrap.py     the stdlib-only wrapper the job runs under
  manager.py  launch / re-attach / wait / kill / ledger
"""
