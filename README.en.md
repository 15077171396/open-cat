# AI Cat · Admin Console

**A content back office with button-level RBAC, full operation auditing, UGC moderation and AI writing.**
Use it to run content and users; use its API to render your own site.

> The primary documentation is in Chinese. This file is a condensed English overview —
> for anything beyond getting started, read the [Chinese README](README.md) and [`docs/`](docs/).

> **Positioning**: this is not another CRUD scaffold, and not a low-code platform.
> It solves one concrete problem — **when several people operate one content site together,
> permissions must be fine-grained, actions must leave a trace, and writing must be pleasant.**

<p align="center">
  <img src="docs/assets/screenshot-dashboard.png" width="880" alt="Dashboard: today's metrics, 7-day trend, signup sources and content assets">
  <br>
  <sub>Dashboard — today's metrics / 7-day trend / signup sources / content assets</sub>
</p>

<p align="center">
  <img src="docs/assets/screenshot-editor.png" width="880" alt="Editor: Quill 2 toolbar, body text and multi-level chapter tree">
  <br>
  <sub>Editor — Quill 2 rich text (tables / code blocks / captions) + multi-level chapter tree</sub>
</p>

<p align="center">
  <img src="docs/assets/screenshot-audit.png" width="880" alt="Audit log filtered by module, actor, time and result">
  <br>
  <sub>Audit — every write leaves a record, searchable by module / actor / time / result</sub>
</p>

---

## Why it is not just another admin template

| Common pain | What this project does |
|---|---|
| Menu entry exists, page returns 403 | Permission codes are declared **once** and derive menu / route / API / button — inconsistency is physically impossible |
| A new endpoint ships without a permission check | Route rule table is **fail-closed**: unregistered requests get 403 |
| Nobody can tell who changed what | Write operations **must** be audited; a missing audit record turns tests red |
| Shared accounts, unclear accountability | 80 permission codes, 3 built-in roles, customizable down to the button |
| Third-party keys stored in plain text | Keys are **encrypted with Fernet** in the DB, only a masked value is returned |
| The editor is dumb, images are found by hand | Quill 2 + AI rewrite/continue/illustrate + image library search |

---

## Quick start

```bash
git clone <repo-url> && cd <repo>
cp .env.example .env          # only SECRET_KEY / DB password / Redis password are required
docker compose up -d
```

Open `http://localhost/admin`. On first visit you get the **setup wizard**:
create an admin → choose storage → done. No manual env editing, no third-party keys needed.

**Default account**: set by yourself in the wizard. Without the wizard, it is controlled by
`ADMIN_PASSWORD`; if unset, a random password is generated and printed **once** in the startup log.

Want to see it with data?

```bash
docker compose exec app python seed_demo.py
```

Seeds 5 tutorials (with 15 chapters), 8 wiki entries, sample resources/tools/extensions, comments,
feedback, announcements and points history — and generates cover images and avatars **into the real
storage driver**, so images actually load instead of showing 404 placeholders.

The script is idempotent (titles are prefixed with `【示例】`); it refuses to run on a database that
already has real content unless you pass `--yes`.

> Local development without Docker: see [development doc §2](docs/07-开源文档/05-开发文档.md) (Chinese).

---

## Features

| Module | Capability |
|------|------|
| **Content (sample domain)** | Tutorials (multi-level chapter tree) / categories / media library — a **copyable reference implementation**: list, chapter tree, editor, review, recycle bin |
| **Editing** | Quill 2 rich text (tables, code blocks, captions, TOC, Markdown paste), AI rewrite/continue/illustrate, image library search, draft preview |
| **Moderation** | Machine-review trail, user reports, comment governance (deletable and restorable, never editable) |
| **Users** | CRUD, ban, soft delete and restore, points adjustment, role assignment, real-name review |
| **Permissions** | Roles / permission tree / departments, 64 permission codes, button-level |
| **Audit** | Every write is recorded; searchable by module / actor / time / result |
| **Operations** | Announcements (scheduled publish/unpublish), system notifications, recharge plans and orders (read-only), search-term analytics |
| **System** | 26 groups / 137 settings, login methods, payment channels, mail, AI models and pricing, scheduled jobs, legal documents, **deployment readiness**, list export |
| **AI** | Multiple providers (OpenAI-compatible / DeepSeek / Qwen), per-model pricing, global cost guard, per-user quota |

---

## Tech stack

**Backend** FastAPI 0.138 · SQLAlchemy 2 (async) · MySQL 8 · Redis 7 · Alembic · APScheduler · Pydantic 2
**Frontend** React 18 · TypeScript · Vite 5 · Ant Design 6 · Zustand · Quill 2
**Deploy** Docker Compose · nginx · optional Sentry

```
backend/         FastAPI app (237 operations, 122 admin; content API at /api/v1/content/docs)
admin-frontend/  Admin SPA (base=/admin/)
docs/            Documentation (Chinese)
```

---

## Engineering discipline

The thing worth reviewing in an open-source project is discipline, not feature count.

| Capability | This project | Typical open-source admin |
|------|------|-------------------|
| Backend tests | **145 files / 1774 cases**, fully offline (no MySQL/Redis needed) | 0 – dozens |
| Architecture guards | Permission coverage / audit coverage / menu consistency / task registry, all covered by tests | Rare |
| Secret storage | Fernet-encrypted in DB + mask sentinel | Plain text in DB or config |
| Audit | Mandatory for writes; missing record fails tests | Logs exist, no coverage guarantee |
| Rate limiting | Sensitive endpoints **fail-closed** when Redis is down | Fail-open on error |
| Permissions | Codes / menu / routes / guards **single-sourced** | Hand-aligned in many places, drifts |
| Migrations | 79 versions, CI verifies upgrade → downgrade → upgrade | Often mixed with `create_all` |

---

## Known limitations

- **MySQL 8 only** (depends on `ngram` Chinese full-text index)
- **Single-instance deployment**: the scheduler runs in-process; horizontal scaling requires splitting it out
- **Simplified Chinese UI only** (i18n is on the roadmap)
- No multi-tenancy, no low-code form engine, no in-app money movement (refunds go through the payment provider)

---

## Docs

| Doc | Content |
|------|------|
| [Features](docs/07-开源文档/01-功能文档.md) | What it does, rules, boundaries |
| [Requirements](docs/07-开源文档/02-需求文档.md) | Target users, user stories, acceptance criteria |
| [Technical](docs/07-开源文档/03-技术文档.md) | Choices, key decisions, integrations, limits |
| [Architecture](docs/07-开源文档/04-架构文档.md) | Layers, auth chain, data model, extension points |
| [Development](docs/07-开源文档/05-开发文档.md) | Boot, adding a module, tests, PR |
| [Deployment](docs/04-运维/部署说明书.md) | Production deploy, backup, monitoring, troubleshooting |

---

## Contributing

Issues and PRs are welcome. Read [CONTRIBUTING.md](CONTRIBUTING.md) first — three hard rules:

1. New write operations **must** be audited, otherwise CI is red;
2. New admin endpoints **must** be registered in the route rule table, otherwise 403;
3. New permission codes **must** be synced to menu items, otherwise tests fail.

---

## License

[MIT](LICENSE).

---

## Credits

FastAPI · SQLAlchemy · Ant Design · Quill · and every open-source project that makes this run.
