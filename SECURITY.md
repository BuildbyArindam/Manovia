# Security policy

## Supported versions

Manovia has no tagged release yet. Only the `main` branch is supported. A
security fix lands on `main` and is recorded in [CHANGELOG.md](CHANGELOG.md).

## How to report a vulnerability

**Please do not open a public issue, pull request or discussion for a security
problem.**

Report it privately:

1. **Preferred: GitHub private vulnerability reporting** — on the repository,
   open **Security → Report a vulnerability**. This creates a private advisory
   only the maintainers can see.
2. **Until that option is enabled on the repository**, send a private message to
   the repository owner on GitHub, [@BuildbyArindam](https://github.com/BuildbyArindam),
   and say only that you have a security report. Do not put details in the first
   message; the maintainer will arrange a private channel.

Include, if you can:

- what you found and where (file, endpoint or screen);
- the steps to reproduce it, using **synthetic** accounts and data only;
- the impact as you understand it;
- whether you have already shared it anywhere else.

**Never include a real person's messages, journal entries, mood notes, health
information, passwords, tokens or keys** in a report. If you have already
exposed one, say so and stop; the maintainer will guide the clean-up.

## What is in scope

- The API (`backend/`): authentication, consent gates, owner-only access to
  sessions, journal, mood and CBT records, encryption at rest, log redaction,
  rate limiting, deletion and export.
- The web client (`frontend/`), including the service worker and what it caches.
- The deployment configuration in this repository (`docker-compose*.yml`,
  `fly.toml`, `netlify.toml`, `docker/`, `.github/workflows/`).

## Safety defects are security-relevant

A way to make the crisis flow fail to show a crisis reply, to send a model
output that the output guard should have blocked, or to get the model to answer
a HIGH or IMMINENT message is treated as a **security** report and handled in
the same private channel, even though it is not a classic vulnerability. Report
it the same way and include the synthetic message that reproduces it.

Crisis-content accuracy problems that are not exploitable (a wrong helpline
number, a missed phrase in another language) are not security reports. Use the
"Safety concern" issue template instead, and do not paste any real person's words.

## What to expect

Manovia is maintained by a small team. We will try to acknowledge a report
promptly and keep you updated, but we do not promise a response time. Please
allow a reasonable period for a fix before public disclosure. We will credit you
in the advisory if you want us to.

## Encryption and secrets

Secrets are configured only through environment variables (see
`.env.example`). The `.env.example` values marked as dev-only, such as the
`docker-compose.yml` defaults, are not secrets and must never protect real data.
`docker-compose.prod.yml` refuses to start without real secrets. A leaked key is
a security report; please report it rather than using it.
