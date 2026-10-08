-- Additive settings, role, and permission support.
-- Run after 005_issue_release_link.sql. Existing rows are preserved.

create table if not exists public.user_preferences (
  user_id uuid primary key references auth.users(id) on delete cascade,
  email_notifications boolean not null default true,
  in_app_notifications boolean not null default true,
  digest_frequency text not null default 'daily' check (digest_frequency in ('never','instant','daily','weekly')),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table if not exists public.role_permissions (
  role text not null check (role in ('admin','manager','member')),
  permission text not null,
  enabled boolean not null default true,
  updated_at timestamptz not null default now(),
  primary key (role, permission)
);

insert into public.role_permissions (role, permission, enabled)
values
  ('admin', 'workspace.manage', true),
  ('admin', 'users.manage', true),
  ('admin', 'permissions.manage', true),
  ('admin', 'security.manage', true),
  ('manager', 'projects.manage', true),
  ('manager', 'tasks.manage', true),
  ('manager', 'issues.manage', true),
  ('manager', 'reports.view', true),
  ('member', 'projects.view', true),
  ('member', 'tasks.manage', true),
  ('member', 'issues.manage', true),
  ('member', 'reports.view', true)
on conflict (role, permission) do nothing;

create or replace function public.is_workspace_admin()
returns boolean
language sql
stable
security definer
set search_path = public
as $$
  select exists (select 1 from public.profiles where id = auth.uid() and role = 'admin');
$$;

alter table public.user_preferences enable row level security;
alter table public.role_permissions enable row level security;

grant select, insert, update, delete on public.user_preferences, public.role_permissions to authenticated;

drop policy if exists "users own preferences" on public.user_preferences;
create policy "users own preferences" on public.user_preferences
  for all to authenticated using (user_id = auth.uid()) with check (user_id = auth.uid());

drop policy if exists "authenticated role permissions read" on public.role_permissions;
create policy "authenticated role permissions read" on public.role_permissions
  for select to authenticated using (true);

drop policy if exists "admins manage role permissions" on public.role_permissions;
create policy "admins manage role permissions" on public.role_permissions
  for update to authenticated using (public.is_workspace_admin()) with check (public.is_workspace_admin());

drop policy if exists "admins insert role permissions" on public.role_permissions;
create policy "admins insert role permissions" on public.role_permissions
  for insert to authenticated with check (public.is_workspace_admin());

drop policy if exists "admins delete role permissions" on public.role_permissions;
create policy "admins delete role permissions" on public.role_permissions
  for delete to authenticated using (public.is_workspace_admin());
