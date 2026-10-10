# Windows CI fixture object ownership

GitHub PR run [37179892998](https://github.com/NyanBUIDL/telegram-ai-personal-assistant/actions/runs/37179892998)
on exact HEAD `10cdbf2a190bf52630313b8f906d1d753393a2ec` passed the required
MySQL and frontend jobs. Both Windows jobs failed 24 desktop cases. The real
Windows descriptor checks rejected newly created test directories because their
NTFS owner differed from the process user's SID. The elevated runner uses its
Administrators group as the default owner for new objects.

The CI runner now normalizes only its own process token's default object owner
to the existing `TOKEN_USER` SID, checks the resulting `TOKEN_OWNER` with
`EqualSid`, and closes the token handle. It does not enable privileges, change
the user SID, change a DACL, claim an existing directory, or relax production's
foreign-owner refusal. Failure returns a sanitized error and stops the test run.
This models object creation for the per-user application target while preserving
the same security assertions.

The Windows behavior and API choice follow Microsoft's
[Owner of a New Object](https://learn.microsoft.com/en-us/windows/win32/secauthz/owner-of-a-new-object)
and [TOKEN_OWNER](https://learn.microsoft.com/en-us/windows/win32/api/winnt/ns-winnt-token_owner)
documentation. Physical standard-user installer testing remains a later gate.

Local composed desktop/auth regression with this runner: 80 passed, zero failed,
zero skipped, zero errors, exit 0; count-only evidence
`.test-temp/root-ci-owner-summary.json`. This local run also includes retained
D02 work that was not part of the historical CI HEAD. Independent D02 author
reviewed the runner helper, which they did not implement: scoped Approved, no
findings. No full-branch lint claim is made while S03/V02 remain in progress.

A new exact-HEAD GitHub run is required to verify both elevated Windows jobs.
