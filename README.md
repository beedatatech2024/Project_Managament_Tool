# BeeData Project Management Tool

A Python-only project management workspace inspired by the supplied PMT login page, with a cleaner responsive interface and Supabase persistence.

## Included

- Supabase email/password authentication
- Overview dashboard with project, task, overdue, and team metrics
- Project creation, editing, and deletion
- Task creation, assignment, editing, deletion, priority, due dates, and status updates
- Issues, notifications, sprints, releases, capacity, reports, settings, and dependency-aware Gantt workflows
- Supabase migrations with tables, RLS, seed records reconstructed from the supplied screenshots, and automatic profile creation

## Run locally

1. Create a Supabase project at https://supabase.com.
2. Open Supabase **SQL Editor** and run these files in order: [`migrations/001_initial.sql`](migrations/001_initial.sql), [`migrations/002_workflows.sql`](migrations/002_workflows.sql), [`migrations/003_legacy_pmt_data.sql`](migrations/003_legacy_pmt_data.sql), [`migrations/004_completion_workflows.sql`](migrations/004_completion_workflows.sql), [`migrations/005_issue_release_link.sql`](migrations/005_issue_release_link.sql), [`migrations/006_settings_administration.sql`](migrations/006_settings_administration.sql), and [`migrations/007_profile_role_security.sql`](migrations/007_profile_role_security.sql). Migration 003 contains screenshot-reconstructed seed rows; it is not an export of the old application's original database.
3. In **Authentication â†’ Providers**, enable Email. In **Authentication â†’ Users**, create at least one user with email and password. For local testing, either enable email auto-confirmation or confirm the user manually.
4. Copy `.env.example` to `.env` and set `SUPABASE_URL`, `SUPABASE_ANON_KEY`, and a long random `FLASK_SECRET_KEY`. Use the project URL and the public anon/publishable key from **Project Settings â†’ API**. Never use or expose the service-role key.
5. Install dependencies and start Flask:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python app.py
```

Open `http://127.0.0.1:5000`.

The migration creates profiles automatically whenever a Supabase Auth user is created. All application records are read from and written to Supabase through the Flask server. The integration buttons currently record connection state; provider OAuth/API delivery and scheduled automation execution still need provider credentials and background workers before they are production functionality.

For production, run behind HTTPS with a production WSGI server, set `FLASK_DEBUG=false`, add CSRF protection, and rotate any credentials that have been shared in chat or committed to a file.

