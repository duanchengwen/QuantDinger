# A-share paper-trading downstream

[简体中文](README_CN.md)

The development fork is https://github.com/duanchengwen/QuantDinger and the upstream is https://github.com/OpenByteInc/QuantDinger. See [baseline.json](baseline.json) for the pinned starting revision. The official UI and independent paper worker are integrated; see the [web run guide](WEB_WORKSPACE.md) for scope. The following maintenance requirements do not imply that all product integration and acceptance work is complete.

## Branches and remotes

- `origin` is the user's fork; `upstream` is the official repository.
- Keep `main` at an upstream baseline and validated downstream changes on `ashare-paper`, using short-lived feature branches.
- The local default push remote is `origin`; the upstream push URL is `DISABLED`. Git configuration does not travel with clones, so repeat it in new environments.
- Development is pushed to `origin/ashare-paper`; remote branch protection has not been configured.

For a new checkout (switch to existing branches instead of overwriting them):

```bash
git clone https://github.com/duanchengwen/QuantDinger.git
cd QuantDinger
git remote add upstream https://github.com/OpenByteInc/QuantDinger.git
git remote set-url --push upstream DISABLED
git config remote.pushDefault origin
git fetch upstream
git switch -c ashare-paper 79caf19b056e4bf04338e3d87c7d70fe2b4a932d
git config branch.ashare-paper.pushRemote origin
```

## Reviewing upstream updates

1. Start with a clean worktree, fetch updates and record the full target commit. Before historical comparisons, complete a shallow clone with `git fetch --unshallow upstream`; use `git fetch upstream` for a complete clone.
2. Compare the pinned baseline with the target, reviewing licenses, dependencies, configuration, migrations, APIs, execution paths and Agent permissions. Fetching alone does not change the deployed version.
3. Branch from current `ashare-paper` into `review/upstream-DATE`. Use `cherry-pick -x` for independent fixes or a merge for interdependent releases. Do not pick migrations or interface changes without their dependencies.
4. Resolve conflicts against an isolated test database. Run relevant upstream tests, A-share acceptance checks and upgrade/recovery exercises. Merge after tests and human review, recording accepted commits and exclusions. Update the baseline for a complete upgrade; record isolated picks separately rather than claiming the entire release was adopted.
5. Separate release from merge. Preserve application versions, image digests and a restore-tested database backup. An irreversible migration cannot be rolled back by reverting application code alone. Updates never automatically promote strategies or deploy to paper accounts.

## Gates before initial paper operation

- Preserve exchange/source and receive timestamps, raw evidence and provenance for quotes. Reject fills on stale, missing, inconsistent or unexpectedly changed data.
- Use dated evidence for historical universes, names, ST/suspension status and price limits. Today's list or historical queries containing future names are not complete point-in-time state.
- Enforce cash debit/reservation, share T+1, quantity rules, price limits/suspensions, book-limited partial fills, fees, cancellation and restart idempotency at execution boundaries. Verify PostgreSQL concurrency and recovery while preserving tenancy, transactions, audit and worker ownership.
- Hard-disable real order submission across all paths. Setting both live feature flags false does not establish this guarantee. Until boundary tests pass, import no real trading credentials, connect no broker gateways and start no existing live tasks.
- AI produces research candidates only and cannot mutate running strategies. Disable Agent Gateway or grant read-only access during research. Require human review before explicitly importing paper configuration.
- Archive news provenance, publication and first-observed timestamps, and hashes. Search snippets are not an exchange announcement stream.
- Ubuntu is the initial target; Windows compatibility is a later validation target. Never mark unexecuted Windows CI, continuous-operation or quote-latency checks as passed.

## Reuse and maintenance

Retain upstream accounts, research UI, persistence, tenancy and job infrastructure. Integrate A-share rules and data evidence through explicit adapters and keep necessary core patches small. Reuse the earlier standalone project's modules and tests as migration references; do not replace this system's PostgreSQL authority with its SQLite runtime.

Upstream has a genuine CNStock paper path, but its current virtual-account algorithm does not meet complete A-share cash-broker acceptance. Backend Apache-2.0 licensing does not cover the separately licensed frontend; review its license, trademark and attribution obligations. Future real-broker integration requires separate authorization and acceptance. Shared interfaces reduce migration work but cannot make simulated and real fills identical.

Current increment: [acceptance](SECURITIES_ACCEPTANCE.md) · [personal user guide](USER_GUIDE.md) · [actual backlog](backlog.json).

Theme data: [current membership and time provenance](THEMES.md).

Latest theme archive result: [acceptance](THEMES_ACCEPTANCE.md).

Latest theme UI result: [read-only archive browser acceptance](THEME_WEB_ACCEPTANCE.md).
