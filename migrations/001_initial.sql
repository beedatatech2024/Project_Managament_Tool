create extension if not exists "pgcrypto";

create table if not exists public.profiles (
  id uuid primary key references auth.users(id) on delete cascade,
  email text,
  full_name text not null default 'New teammate',
  role text not null default 'member' check (role in ('admin','manager','member')),
  avatar_color text not null default '#5eead4',
  created_at timestamptz not null default now()
);

create table if not exists public.projects (
  id uuid primary key default gen_random_uuid(),
  owner_id uuid not null references auth.users(id) on delete cascade,
  name text not null,
  description text default '',
  status text not null default 'active' check (status in ('active','planning','on_hold','completed')),
  start_date date not null default current_date,
  due_date date,
  created_at timestamptz not null default now()
);

create table if not exists public.tasks (
  id uuid primary key default gen_random_uuid(),
  project_id uuid references public.projects(id) on delete cascade,
  assignee_id uuid references public.profiles(id) on delete set null,
  created_by uuid not null references auth.users(id) on delete cascade,
  title text not null,
  description text default '',
  status text not null default 'todo' check (status in ('todo','in_progress','review','done')),
  priority text not null default 'medium' check (priority in ('low','medium','high','urgent')),
  due_date date,
  created_at timestamptz not null default now()
);

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

alter table public.profiles enable row level security;
alter table public.projects enable row level security;
alter table public.tasks enable row level security;
alter table public.automation_rules enable row level security;
alter table public.integrations enable row level security;

grant usage on schema public to authenticated;
grant select, insert, update, delete on public.profiles, public.projects, public.tasks, public.automation_rules, public.integrations to authenticated;

drop policy if exists "authenticated profiles" on public.profiles;
drop policy if exists "authenticated projects" on public.projects;
drop policy if exists "authenticated tasks" on public.tasks;
drop policy if exists "authenticated automation rules" on public.automation_rules;
drop policy if exists "authenticated integrations" on public.integrations;
create policy "authenticated profiles" on public.profiles for all to authenticated using (true) with check (true);
create policy "authenticated projects" on public.projects for all to authenticated using (true) with check (true);
create policy "authenticated tasks" on public.tasks for all to authenticated using (true) with check (true);
create policy "authenticated automation rules" on public.automation_rules for all to authenticated using (true) with check (true);
create policy "authenticated integrations" on public.integrations for all to authenticated using (true) with check (true);

create or replace function public.handle_new_user() returns trigger language plpgsql security definer set search_path = public as $$
begin insert into public.profiles (id,email,full_name) values (new.id,new.email,coalesce(new.raw_user_meta_data->>'full_name', split_part(new.email,'@',1))); return new; end; $$;

drop trigger if exists on_auth_user_created on auth.users;
create trigger on_auth_user_created after insert on auth.users for each row execute procedure public.handle_new_user();

