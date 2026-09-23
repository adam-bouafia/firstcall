# Security

FirstCall runs inside your cluster with a ServiceAccount, reads logs and events, and can apply
human-approved fixes. Please report vulnerabilities privately.

## Reporting

Use GitHub private vulnerability reporting: the **Security** tab of this repository, then
**Report a vulnerability**. Do not open a public issue for security problems.

Include the version (chart and image tag), how FirstCall was installed, and steps to reproduce.

## What FirstCall can access

- Default RBAC is read-only: pods, pods/log, events, services, endpoints, PVCs, nodes,
  namespaces, configmaps, workloads, metrics. No secrets, no `pods/exec`.
- The only default write is `events/create`, used to post diagnoses on workloads
  (`writeEvents=false` removes it).
- With `remediation.enabled=true`, a namespace-scoped Role allows patch/update on Deployments,
  Deployment scale and Services in the watched namespaces. Nothing is applied without a server
  dry run and a named person approving it.
- Env var values and Secret contents are never collected. Logs and events are redacted with
  regex rules before they are sent to the model. Regex redaction can miss novel secret formats:
  if logs must not leave the cluster, point FirstCall at an in-cluster model (Ollama, vLLM).

## Known limitation: no built-in authentication

The API and the web console have no login. Anyone who can reach them can read incidents and
approve fixes. The chart therefore defaults to `ClusterIP` services and no Ingress. If you
expose the console, put authentication in front of it (for example oauth2-proxy or your
ingress controller's auth), and keep `remediation.enabled=false` unless you do.
