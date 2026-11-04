# RyGT CI workers

GitLab rygt-team/RyGTS is the authoritative source. GitHub Actions builds, tests and packages development into separate Release (`ci`) and optimized Debug/Diagnostics (`debug-ci`) ZIPs, then uploads them and their checksums to the matching GitLab Generic Package Registry channels. Debug includes verified symbols and tracing tools. No tagged releases or changelogs are created. See the source repository docs/dev/CI.md.

RyGT7 has a separate workflow, [RyGT7 CI package](.github/workflows/rygt7-ci.yml),
using [rygt-team/rygt7](https://gitlab.com/rygt-team/rygt7) `main` as its source.
It has independent concurrency, artifact names, source pins and package identity.
See [the RyGT7 worker notes](ci/rygt7/README.md) for the build and publication contract.

The RyGTS controller reads `renderer/source.json` from the exact resolved runtime
commit. `commit: "HEAD"` tracks the official ShadGT `ryty-gpu` branch; explicit
40-character pins remain supported. Branch HEAD is resolved once and passed as
`RENDERER_SHA` through both Release and Debug builds and publication. Package
versions are `development-<runtime SHA>-renderer-<renderer SHA>`, so renderer-only
updates trigger a new pair without overwriting another renderer build. Both
archives must record that same renderer revision. Runtime overlay and diagnostic
patch verification remains fail-closed; upstream incompatibilities require review.
