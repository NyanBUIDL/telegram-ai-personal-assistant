# F01 — independent review acceptance

Execution/review date: 2026-10-03. Task status: Verified for the scoped implementation.

Implementation commit: `4952360e6b46ddb02292468411bf1555a05de25c`.
Review fix commit: `c31a8f63`.
Evidence: [task-F01.md](task-F01.md).

The independent task review found one Important issue: expired running history_link_delete jobs could be redispatched despite a persisted external-effect marker. The focused fix stores uncertain/requires_reconciliation before queue selection and preserves the attempt count. Scoped independent re-review accepted the fix with all findings addressed and no new Critical/Important breakage.

The original covering suite passed 118 cases in 30.61s. The focused fix suite passed 51 cases in 20.44s, including actual MySQL repeatable-read, grant lock-release and gated embedding races, plus three file-backed SQLite restart cases. RED behavior was observed before each fix; exact commands and environment distinctions are recorded in the linked evidence. Scoped Ruff, format and diff checks passed.

This acceptance covers durable epochs, stale authorization/grant/job fences, shared/owner delivery, notification durability and interrupted destructive-job recovery. Generalized leases/maintenance and fresh installed-environment integration belong to later checks. No live Telegram account, model API, clean Windows VM, public artifact or human UAT has been signed off; beta_ready remains false.
