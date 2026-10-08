-- Completion workflow extensions. Run after 003_legacy_pmt_data.sql.

alter table public.tasks add column if not exists start_date date;
alter table public.tasks add column if not exists end_date date;
alter table public.tasks add column if not exists estimated_hours numeric(8,2) not null default 0;
alter table public.tasks add column if not exists progress integer not null default 0 check (progress between 0 and 100);
alter table public.tasks add column if not exists sprint_id uuid references public.sprints(id) on delete set null;
alter table public.tasks add column if not exists release_id uuid references public.releases(id) on delete set null;

alter table public.issues add column if not exists severity text not null default 'medium' check (severity in ('low','medium','high','critical'));
alter table public.issues add column if not exists reporter_id uuid references public.profiles(id) on delete set null;
alter table public.issues add column if not exists milestone_id uuid references public.project_milestones(id) on delete set null;
alter table public.issues add column if not exists resolved_at timestamptz;
alter table public.sprints add column if not exists created_by uuid references auth.users(id) on delete set null;
alter table public.sprints add column if not exists updated_at timestamptz not null default now();
alter table public.releases add column if not exists name text not null default '';
alter table public.releases add column if not exists created_by uuid references auth.users(id) on delete set null;
alter table public.releases add column if not exists updated_at timestamptz not null default now();

alter table public.sprints drop constraint if exists sprints_status_check;
alter table public.sprints add constraint sprints_status_check check (status in ('planned','active','completed','cancelled'));

create table if not exists public.task_dependencies (
  id uuid primary key default gen_random_uuid(),
  project_id uuid not null references public.projects(id) on delete cascade,
  predecessor_task_id uuid not null references public.tasks(id) on delete cascade,
  successor_task_id uuid not null references public.tasks(id) on delete cascade,
  dependency_type text not null default 'FS' check (dependency_type in ('FS','SS','FF','SF')),
  created_at timestamptz not null default now(),
  constraint no_self_task_dependency check (predecessor_task_id <> successor_task_id),
  unique(predecessor_task_id, successor_task_id, dependency_type)
);

create table if not exists public.team_capacity (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references public.profiles(id) on delete cascade,
  week_start date not null,
  capacity_hours numeric(8,2) not null check (capacity_hours >= 0),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique(user_id, week_start)
);

create table if not exists public.workspace_settings (
  id uuid primary key default gen_random_uuid(),
  workspace_key text not null unique default 'default',
  workspace_name text not null default 'BeeData Project Management Tool',
  timezone text not null default 'Asia/Calcutta',
  date_format text not null default 'YYYY-MM-DD',
  updated_by uuid references auth.users(id) on delete set null,
  updated_at timestamptz not null default now()
);

create index if not exists tasks_sprint_idx on public.tasks(sprint_id);
create index if not exists tasks_release_idx on public.tasks(release_id);
create index if not exists task_dependencies_project_idx on public.task_dependencies(project_id);
create index if not exists team_capacity_week_idx on public.team_capacity(week_start);

alter table public.task_dependencies enable row level security;
alter table public.team_capacity enable row level security;
alter table public.workspace_settings enable row level security;
grant select, insert, update, delete on public.task_dependencies, public.team_capacity, public.workspace_settings to authenticated;

drop policy if exists "authenticated task dependencies" on public.task_dependencies;
drop policy if exists "authenticated team capacity" on public.team_capacity;
drop policy if exists "authenticated workspace settings" on public.workspace_settings;
create policy "authenticated task dependencies" on public.task_dependencies for all to authenticated using (true) with check (true);
create policy "authenticated team capacity" on public.team_capacity for all to authenticated using (true) with check (true);
create policy "authenticated workspace settings" on public.workspace_settings for all to authenticated using (true) with check (true);

insert into public.workspace_settings (workspace_key)
values ('default')
on conflict (workspace_key) do nothing;
