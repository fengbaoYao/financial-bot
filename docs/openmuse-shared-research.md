# OpenMuse shared research

The OpenMuse composer option is a shared research path for general and
financial queries. When the Financial Bot server identifies a financial query,
it adds the server-owned task input `research_scope=financial` and
`financial_bridge_required=true`; clients cannot set or override those fields.
OpenMuse must obtain Financial Bot evidence through its financial data tool
before completing financial research. The loopback-only
`POST /api/financial-research/query` endpoint authenticates with the shared
server-side `FINANCIAL_RESEARCH_SHARED_TOKEN`, tries iFinD first, then the
existing Financial Bot stock analysis fallback. It returns the actual provider,
known data time, attempted sources, and any data gaps. If no source returns
verifiable data, the task must surface that gap and must not fill it with web
search or model memory.

The integration is disabled unless `OPENMUSE_ENABLED=true` and
`OPENMUSE_SHARED_RESEARCH_ENABLED=true` are set on the Financial Bot server.
The server also needs `OPENMUSE_BASE_URL` and `OPENMUSE_ACCESS_KEY`. OpenMuse
must independently enable `GENERAL_RESEARCH_PROFILE_ENABLED=true` and
`SHARED_RESEARCH_WORKSPACE_ENABLED=true`, use its Docker computer provider,
and have a configured model and provider key. Set `TASKS_ONLY=true` to run the
task API without CopilotKit Intelligence; the full OpenMuse mode still requires
`CPK_INTELLIGENCE_API_KEY`.
Financial Bot task API calls use a 60-second read timeout by default to cover
workspace cold starts. Override it with `OPENMUSE_TASK_TIMEOUT_SECONDS` (up to
120 seconds); `OPENMUSE_TIMEOUT_SECONDS` continues to control the short health
probe and remains a fallback for task timeout when explicitly configured.
Financial queries additionally require a cryptographically random
`FINANCIAL_RESEARCH_SHARED_TOKEN` of at least 32 bytes, configured only in the
Financial Bot and local OpenMuse server environments.

The shared computer belongs to OpenMuse's authenticated single owner. It does
not provide tenant isolation between multiple Financial Bot users. Do not
expose this integration or its API through a public host or an unprotected
reverse proxy. The loopback check is defense in depth for direct local access;
it does not authenticate the original caller behind a proxy. Remote access
requires a separately verified user authentication and protected network
boundary before enabling the feature.

OpenMuse inspects the owner's actual Docker workspace before creating a held
task, and re-inspects it before resume and model execution. A missing model or
failed isolation inspection blocks the task. The Financial Bot reads status
and result from the authenticated OpenMuse task API; the OpenMuse project key
is required in full mode, but not when `TASKS_ONLY=true`.
