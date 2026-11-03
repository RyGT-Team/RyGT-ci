# RyGT CI workers

GitLab rygt-team/RyGTS is the authoritative source. GitHub Actions builds, tests and packages development into separate Release (`ci`) and optimized Debug/Diagnostics (`debug-ci`) ZIPs, then uploads them and their checksums to the matching GitLab Generic Package Registry channels. Debug includes verified symbols and tracing tools. No tagged releases or changelogs are created. See the source repository docs/dev/CI.md.

RyGT7 has a separate workflow, [RyGT7 CI package](.github/workflows/rygt7-ci.yml),
using [rygt-team/rygt7](https://gitlab.com/rygt-team/rygt7) `main` as its source.
It has independent concurrency, artifact names, source pins and package identity.
See [the RyGT7 worker notes](ci/rygt7/README.md) for the build and publication contract.
