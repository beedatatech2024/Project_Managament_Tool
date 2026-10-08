create table if not exists public.automation_rules (
  id uuid primary key default gen_random_uuid(),
  created_by uuid not null references auth.users(id) on delete cascade,
  name text not null,
  event text not null check (event in ('task_created','task_completed','task_overdue','project_completed')),
  action text not null check (action in ('notify_assignee','move_to_review','create_task','notify_manager')),
  enabled boolean not null default true,
  created_at timestamptz not null default now()
);

create table if not exists public.integrations (
  id uuid primary key default gen_random_uuid(),
  connected_by uuid not null references auth.users(id) on delete cascade,
  provider text not null unique check (provider in ('slack','google_calendar','github','email')),
  label text not null,
  status text not null default 'connected',
  created_at timestamptz not null default now()
);

alter table public.automation_rules enable row level security;
alter table public.integrations enable row level security;

grant usage on schema public to authenticated;
grant select, insert, update, delete on public.automation_rules, public.integrations to authenticated;

drop policy if exists "authenticated automation rules" on public.automation_rules;
drop policy if exists "authenticated integrations" on public.integrations;
create policy "authenticated automation rules" on public.automation_rules for all to authenticated using (true) with check (true);
create policy "authenticated integrations" on public.integrations for all to authenticated using (true) with check (true);
