# RyGT7 CI worker

This separate workflow builds the public `main` branch of
https://gitlab.com/rygt-team/rygt7. Each job checks out the exact resolved source
commit and the official `rygt-team/shadgt7` `main` HEAD once per run. It checks
for changes to either branch every 15 minutes, or runs manually with a `force`
input. Pull requests build and validate but cannot publish. The RyGTS workflows
and their Release/Debug packages are independent.

## Outputs

The initial RyGT7 channel is a Release client package:

- GitHub artifact: `rygt7-ci-<full source commit>-renderer-<full renderer commit>`, retained for seven days.
- GitLab project: `rygt-team/rygt7`, Generic Package Registry channel `ci`, version
  `main-<full source commit>-renderer-<full renderer commit>`.
- Files: `RyGT7-ci-<short source commit>.zip`, its `.zip.sha256`, and `CI-BUILD.json`.

No tag, release, changelog or announcement is created. There is no RyGT7 Debug
package until matching symbols and diagnostics have a validated packaging path.
Optional server/shadNet distributions and the optional NVIDIA plugin/SDK are not
included in this client build.

## Build and provenance

Linux builds and tests the converter from the selected source. Windows builds the
launcher, converter, native runtime, wheel probe, eight pinned renderer targets
and supplemental GT7 stubs. Tests include launcher/setup checks, the source
packager's synthetic test and the worker's package validation. This is compilation
and packaging validation, not an in-game or GPU performance test.

The controller resolves the official `rygt-team/shadgt7` `main` HEAD to a full
SHA once, alongside the RyGT7 `main` source SHA. Windows fetches that exact
renderer object; all build, assembly and publication checks use the same resolved
value. This CI policy overrides the renderer commit declared by the runtime's
`build-support/renderer.json`; its official repository/branch requirement is
still checked. The configured and fetched URLs in `BUILD_INFO.json` describe
the CI selection. Branch movement during a run cannot silently change its build.

Package versions and GitHub artifacts include both full SHAs, so a renderer-only
change triggers a new build without overwriting a different source/renderer pair.
The archive filename stays `RyGT7-ci-<short source commit>.zip` within that pair's
version. The exact VMA patch and clean-source provenance checks remain enforced;
an upstream patch change intentionally fails until the adapter is re-audited.

The source release packager expects a complete runtime. A SHA-256 pinned official
RyGT7 2026-10-31 package supplies libraries not rebuilt by the current build and
the FSR redistribution files. Only explicitly allowlisted files are read from
that archive. Fresh runtime/renderer/stub outputs overwrite their matching base
files. The converter and wheel probe are source-built; GCC redistribution files
come from the same UCRT64 toolchain. The package includes required notices and
the source's bundled offline patch and credits.

`BUILD_INFO.json` records exact source and renderer commits, runtime-base URL and
hash, toolchain and submodule information, per-file origins and hashes, and CI
control/run identity. The historical release's gameplay and build-provenance
claims are replaced with accurate CI notes. Game executables/data, firmware,
fonts, saves, personal settings, logs and shader caches are excluded.

## Credentials and publication

The existing GitHub repository secret `GITLAB_PACKAGE_TOKEN` is provided only to
the final publish step in a fresh Linux job. Source builds and tests never
receive it. This token must have package read/write access to the RyGT7 project;
GitLab group CI/CD variables are not automatically injected into GitHub jobs.
`GITHUB_ACTIONS_TOKEN` is a separate credential for GitHub access/dispatch.

The trusted controller validates archive identity, every file hash, safe ZIP
paths and build provenance before upload. It first uploads and verifies an
attempt envelope as `CI-PENDING.json`, then uploads and verifies the archive and
checksum; `CI-BUILD.json` is uploaded last as the completion marker. Scheduled
checks skip only when the valid pending and completed envelopes match, including
the run attempt. A failed forced rebuild therefore remains retryable rather than
reusing a stale completion marker. Publication is not an atomic registry
transaction; each upload and download must pass, including when registry files
with the same name already exist.

Local checks:

```text
python -m unittest discover -s ci/rygt7 -p test_*.py
python -m unittest discover -s ci/rygt7/tests -p test_*.py
```

Set `RENDERER_SHA` to the resolved renderer SHA for direct package validation.
Set `GT7_SOURCE_ROOT` and `GT7_RENDERER_ROOT` to clean exact source checkouts to
include the real-packager synthetic integration test. The full Windows build
also needs the hosted UCRT64, Visual Studio x64 and pinned LLVM environment.
