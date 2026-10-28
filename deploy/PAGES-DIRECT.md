# Pages direct upload

Production keeps the existing `vps-869hr` Pages project and custom domain.
GitHub Actions tests, fetches, audits AFF links, commits generated artifacts,
then uploads only `site/` using pinned Wrangler 4.154.0. Public verification
still compares HTML, deals JSON, updates JSON and sitemap before reporting success.

`CLOUDFLARE_API_TOKEN` is an Actions Secret in both the main and scheduler
repositories. It is exposed only to the upload step, not npm installation,
tests, scraping or PR workflows. The token has Pages edit permission scoped to
the production account; Cloudflare grants this at account, not project level.
Never place it in source, CLI arguments, workflow variables, artifacts or logs.
Repository write access remains trusted and must be restricted: contributors
able to change main workflows could misuse repository secrets.

Only the two named repositories on main may run the deployment job. The uploader
uses fixed account/project identifiers, an isolated temporary upload directory,
rejects symlinks and hidden files (except an empty `.nojekyll` marker), scans for
the deployment token in upload content, and checks remote main before every attempt. Raw CLI output is
withheld. Recognized transient network failures retry after 60, 180 and 300
seconds, at most three retries. Authentication/config errors and ambiguous
process timeouts stop immediately; no unlimited retry or success-on-upload-only.

After the first successful direct upload and public verification, disable Git
automatic production and preview deployments in the Pages project settings.
Do not delete the project, domain or GitHub integration. To roll back this
migration, re-enable Git deployment and restore the workflow before removing
the secret. A failed upload retains the previous live deployment; a successful
upload with failed verification requires inspection rather than blind rollback.

Reference: https://developers.cloudflare.com/pages/configuration/git-integration/

## Migration acceptance (2026-10-28)

- Main repository run `40673159092`: 334 tests passed; direct upload accepted on
  its first attempt; all four public artifacts verified for revision `a7dd985`.
- Cloudflare production and preview Git automatic deployments disabled after
  the successful upload. Existing project, custom domain and AFF rules retained.
- Scheduler probe `40673182991` passed after Git deployments were disabled,
  including direct upload, public verification and continuation. Its one next
  daily run is `40674196112`; the old queued pipeline `40661029842` was cancelled.
- Secrets stored in both repositories using GitHub encrypted Actions Secrets;
  the temporary local credential file was removed. No credential is committed.
- The account-wide Pages edit scope cannot be narrowed to one project in this
  token configuration. The uploader is project-pinned, but protect main write
  access and rotate/revoke the token if a repository maintainer is compromised.
