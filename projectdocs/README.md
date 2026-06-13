# 📚 RepoMind V2 — Project Documentation Index

Welcome to the **RepoMind V2** documentation hub. RepoMind V2 is an autonomous **6-agent swarm + Human-in-the-Loop** system built for the **Microsoft Build AI Hackathon 2026** (Agent Swarms theme) that detects failed CI runs and opens a fix PR — while tracking every LLM call's tokens, cost, and quality grade. **RepoMind V2 pauses for human review before any merge, and is fully self-serve via `.repomind.yml`**.

---

## 📖 Documentation Map

| # | Document | File | Description |
|---|----------|------|-------------|
| 1 | **Architecture Document** | [`ARCHITECTURE.md`](./ARCHITECTURE.md) | 6-agent swarm + HITL middleware + Step 12 review handler |
| 2 | **High-Level Design (HLD)** | [`HLD.md`](./HLD.md) | System overview, major components, data flow, deployment topology |
| 3 | **Low-Level Design (LLD)** | [`LLD.md`](./LLD.md) | Class diagrams, function signatures, data models, module internals |
| 4 | **Tech Stack Document** | [`TECH_STACK.md`](./TECH_STACK.md) | Azure OpenAI + Groq + deepagents + LangGraph + Next.js |
| 5 | **Installation Guide** | [`INSTALLATION.md`](./INSTALLATION.md) | Prerequisites, Azure setup, frontend setup, Python env |
| 6 | **🆕 Onboarding Guide** | [`ONBOARDING.md`](./ONBOARDING.md) | **For repo owners** — install + `.repomind.yml` self-serve config |
| 7 | **How to Run** | [`HOW_TO_RUN.md`](./HOW_TO_RUN.md) | Running locally, running tests, deploying to AWS |
| 8 | **🆕 Testing Guide** | [`TESTING_GUIDE.md`](./TESTING_GUIDE.md) | **Comprehensive** — unit/integration/E2E/HITL commands + cheat-sheet |
| 9 | **Repository Structure** | [`REPO_STRUCTURE.md`](./REPO_STRUCTURE.md) | Complete folder/file tree with all V2 additions |
| 10 | **API Reference** | [`API_REFERENCE.md`](./API_REFERENCE.md) | REST endpoints, SQS message formats, state schemas |
| 11 | **Pipeline Workflow** | [`PIPELINE_WORKFLOW.md`](./PIPELINE_WORKFLOW.md) | Step-by-step pipeline flow (Steps 1–12) |
| 12 | **LangGraph Pipeline** | [`LANGGRAPH_PIPELINE.md`](./LANGGRAPH_PIPELINE.md) | 6-agent swarm + HITL interrupt + S3 checkpointer |
| 13 | **Configuration Guide** | [`CONFIGURATION.md`](./CONFIGURATION.md) | All env vars + `.repomind.yml` schema |
| 14 | **Testing Guide (legacy)** | [`TESTING.md`](./TESTING.md) | Test strategy overview (see `TESTING_GUIDE.md` for commands) |
| 15 | **Deployment Guide** | [`DEPLOYMENT.md`](./DEPLOYMENT.md) | AWS SAM deployment, CI/CD, production checklist |
| 16 | **Security Document** | [`SECURITY.md`](./SECURITY.md) | Secrets management, log sanitization, webhook validation |
| 17 | **Troubleshooting Guide** | [`TROUBLESHOOTING.md`](./TROUBLESHOOTING.md) | Common errors, debugging tips, log analysis, FAQ |
| 18 | **Contributing Guide** | [`CONTRIBUTING.md`](./CONTRIBUTING.md) | Code style, PR process, adding new steps |
| 19 | **Glossary** | [`GLOSSARY.md`](./GLOSSARY.md) | Key terms (HITL, Deep Agent, LLM-as-Judge, `.repomind.yml`, ...) |
| 20 | **Changelog** | [`CHANGELOG.md`](./CHANGELOG.md) | Version history (latest: **v2.0.0** — Self-Serve + HITL) |

---

## 🏁 Quick Start

**👉 Are you a repo owner who wants RepoMind to fix your CI failures?**
Install the GitHub App → wait for the welcome PR → edit `.repomind.yml`. Done. See [`ONBOARDING.md`](./ONBOARDING.md).

**👉 Are you a developer who wants to run / extend RepoMind?**

1. Read the [Installation Guide](./INSTALLATION.md) to set up Python + Node.js + Azure
2. Read [How to Run](./HOW_TO_RUN.md) to start the local dev server + frontend dashboard
3. Read the [Testing Guide](./TESTING_GUIDE.md) for unit/integration/E2E commands
4. Read [LangGraph Pipeline](./LANGGRAPH_PIPELINE.md) to understand the HITL interrupt flow

---

## 🌟 What's New in v2.0.0

- ⚙️ **Self-serve `.repomind.yml`** — each repo controls its own policy without operator handholding
- 🎚️ **Three modes** — `disabled` / `dry_run` (comment-only) / `auto_fix` (open PR)
- 👋 **Welcome PR on install** — every new repo gets a friendly intro PR with safe defaults
- 💬 **Always-on status comments** — never wonder "did RepoMind see this?" again
- 👥 **Human-in-the-Loop merge gate** — graph pauses for PR review; only merges when a human approves
- 🗄️ **S3-backed LangGraph checkpointer** — survives Lambda 15-min timeout during hours-long human waits
- 🔁 **Step 12** — new review handler that resumes paused graphs on `pull_request_review` events

### Previously in v1.3.0-alpha

- 🤖 **6-agent LangGraph swarm** with Solver → Validator retry edge (max 2 retries)
- 🧠 **Hybrid Deep Agent Solver** — Tier 1 (`deepagents` + tools + sub-agents) → Tier 2 (direct LLM fallback)
- ☁️ **Azure OpenAI primary** with Groq fallback for free-tier mode
- 💰 **Full LLM observability** — per-call tokens / latency / cost tracking
- 🛡️ **LLM-as-Judge** — independent quality auditor with hallucination detection
- 🖥️ **Next.js dashboard** — live agent visibility with RAG, LLM cost, and Judge cards

See [CHANGELOG.md](./CHANGELOG.md) for the full list.

---

> **Last Updated:** June 9, 2026

> **Project:** RepoMind V2  
> **Version:** 2.0.0 (Microsoft Build AI Hackathon Release)
