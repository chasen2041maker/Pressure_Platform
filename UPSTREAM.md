# Upstream projects and modifications

Pressure Platform is a maintained derivative of TestHub with a k6-based performance-testing workflow. It is not a wholly original implementation.

- **TestHub**: https://github.com/chenjigang4167/testhub_platform — source baseline `272ab153c6381b8eb726847dee3b2d819f2574ca`. The original README and GPL-3.0 license are retained under `testhub/`; the license is also provided at the repository root. Existing copyright notices remain in their source files.
- **Grafana k6**: https://github.com/grafana/k6 — the `vendor/k6` submodule pins the engine source required by `deployment/runtime/bounded-sse/runtime-lock.json`. k6 retains its own license and copyright notices in that submodule.
- Python, JavaScript and Go dependencies retain their individual licenses. Dependency lock files describe the included dependency graph; installed packages and build outputs are not vendored here.

## Modifications published on 2026-09-22

This version adds and maintains performance projects, OpenAPI import, persistent environments and account pools, reusable prepared requests, k6 execution, bounded HTTP/WebSocket/SSE workloads, reports, execution auditing and recovery support. It also replaces private API specifications with generated test fixtures and uses generic example configuration in the public distribution.

The initial public commit is a clean source snapshot. Private Git history, company deployment automation, credentials, accounts, databases and real execution data are not part of this repository. The example fixtures describe synthetic data, not any real service's API or capacity.
