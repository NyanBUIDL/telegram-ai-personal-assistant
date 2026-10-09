# O04 CI account launch block

Recorded 2026-10-10 (Asia/Saigon). This records a launch rejection, not a failed product test or passing gate. O04 remains InReview; 18/26 tasks are Verified; U03/U04 remain Planned and the application is not beta-ready.

## Confirmed cause

Candidate `be901eaaee71b4871c963b65efe87638f2d59cbd` has [pull-request run 37935424475](https://github.com/NyanBUIDL/telegram-ai-personal-assistant/actions/runs/37935424475) and [push run 37935409811](https://github.com/NyanBUIDL/telegram-ai-personal-assistant/actions/runs/37935409811), both failed. The four pull-request jobs have runner_id 0 and no steps; no tests ran.

Root retrieved the actual annotations for Windows Python 3.12 check `113836138500`, frontend check `113836138790` and Required CI check `113836154927`. All three contain the same failure:

> The job was not started because recent account payments have failed or your spending limit needs to be increased. Please check the 'Billing & plans' section in your settings

This establishes GitHub account billing/spending admission as the launch blocker. It does not distinguish a failed payment from an exhausted budget or allowance. The Ubuntu 26 migration notice on the Linux jobs is informational and is not the rejection. No credential value was saved, printed, committed or provided to a reviewer.

Claude CLI completed a bounded read-only review, and an independent agent checked the workflow/history with the installed YAML parser. No configuration error explaining this rejection was found. The workflow from the runner-backed `3f4913a8` to `35a78bfb` changed only the Windows job cap and comment, from 40 to 50 minutes; `be901eaa` changed no workflow fields.

## Repository mitigation

The default branch is `master`, confirmed by GitHub repository metadata and local origin/HEAD. Restrict push-triggered CI to `master`, retaining every pull request and manual dispatch. Previously each update to the open PR's feature branch triggered two full matrices with different concurrency groups. The filter removes that duplicate automatic feature-branch push run. Feature branches without a PR now use manual dispatch or open a PR for CI.

All jobs, action pins, permissions, Python 3.12/3.13 matrix, timeouts, assertions, scale checks, native/browser/art checks and Required CI remain unchanged. Local parsing/structural assertions check this scope. This optimization saves avoidable future runs; it cannot unblock billing or establish passing hosted CI.

## Required account action and verification

1. The owner opens [GitHub billing](https://github.com/settings/billing), checks Actions usage and budgets, and determines which condition GitHub reported. If payment failed, the owner resolves it. If a budget/allowance is exhausted, the owner decides whether to authorize paid usage with a bounded budget or wait for renewal. No budget increase, payment update, purchase or repository visibility change has been performed.
2. After the account block is resolved, run the required matrix once on the latest draft-PR SHA. Do not repeatedly rerun while the same account restriction remains.
3. Inspect actual Windows Python 3.12/3.13, Node/frontend and Required CI results and their artifacts. O04 is accepted only after all exact-candidate requirements pass; local or earlier-SHA results cannot substitute.

GitHub documents both account blocking after included usage and budget restrictions in [Actions billing](https://docs.github.com/en/billing/concepts/product-billing/github-actions), and remediation in [workflow troubleshooting](https://docs.github.com/en/actions/how-tos/troubleshoot-workflows#reviewing-billing-errors). Changing a spending budget can permit charges and therefore requires an explicit owner decision.
