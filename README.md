# Manovia

A privacy-first, safety-first mental wellbeing self-help companion.

> **Not a substitute for professional care. If you are in crisis, call your local emergency number.**

[![CI](https://img.shields.io/badge/CI-placeholder-lightgrey)](#)
[![Tests](https://img.shields.io/badge/tests-placeholder-lightgrey)](#)
[![Coverage](https://img.shields.io/badge/coverage-placeholder-lightgrey)](#)

*Status badges are placeholders until CI and coverage reporting are configured.*

## Features

Planned self-help tools will be documented here as they are implemented and tested.

## Architecture

The intended monorepo stack is recorded in [ADR 0001](docs/adr/0001-monorepo-and-stack.md). The repository is currently a Day 1 scaffold; application services and UI are not implemented yet.

## Quick start

Application setup instructions will be added when the backend and frontend are implemented. No application dependencies are installed by this scaffold.

## Safety

Manovia is a self-help companion, not therapy, a diagnostic tool, or a crisis service. It will not diagnose, recommend medication doses, or promise outcomes. Crisis support must be deterministic and never delegated to an LLM. See [AGENTS.md](AGENTS.md) for the operating rules.

**If you are in crisis, call your local emergency number or contact a trusted person or local crisis service.**

## Privacy

Privacy-first defaults are a project requirement. Do not put secrets in the repository, do not log raw message text, and do not send identifiers to an LLM. See [.env.example](.env.example) for the planned environment configuration keys.

## Roadmap

- Day 1: repository scaffold, tooling placeholders, agent operating rules, and initial architecture decision.
- Future days: backend, frontend, safety systems, privacy controls, and evaluation tooling, each with tests.
