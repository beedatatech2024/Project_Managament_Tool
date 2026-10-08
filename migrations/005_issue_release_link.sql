-- Required for release issue assignment.
-- Run after 004_completion_workflows.sql. This is additive and preserves existing data.

alter table public.issues
  add column if not exists release_id uuid references public.releases(id) on delete set null;

create index if not exists issues_release_idx on public.issues(release_id);
