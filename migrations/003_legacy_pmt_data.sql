-- Legacy PMT-compatible schema and screenshot seed data.
-- Run after 001_initial.sql and 002_workflows.sql.
-- The rows below are explicitly marked as screenshot_seed because they are
-- reconstructed from the supplied legacy screenshots, not exported records.

alter table public.projects add column if not exists project_key text;
alter table public.projects add column if not exists project_type text;
alter table public.projects add column if not exists budget numeric(12,2);
alter table public.projects add column if not exists methodology text check (methodology in ('waterfall','agile','devops','rapid_application'));

create table if not exists public.methodology_metrics (
  id uuid primary key default gen_random_uuid(),
  name text not null unique,
  display_metric text not null,
  metric_label text not null,
  note text not null default '',
  source text not null default 'screenshot_seed',
  created_at timestamptz not null default now()
);

create table if not exists public.project_milestones (
  id uuid primary key default gen_random_uuid(),
  project_id uuid not null references public.projects(id) on delete cascade,
  name text not null,
  status text not null default 'planned' check (status in ('planned','in_progress','completed','blocked')),
  due_date date,
  created_at timestamptz not null default now()
);

create table if not exists public.issues (
  id uuid primary key default gen_random_uuid(),
  project_id uuid references public.projects(id) on delete cascade,
  assignee_id uuid references public.profiles(id) on delete set null,
  created_by uuid not null references auth.users(id) on delete cascade,
  title text not null,
  description text not null default '',
  issue_type text not null default 'bug' check (issue_type in ('bug','task','requirement','blocker')),
  priority text not null default 'medium' check (priority in ('low','medium','high','critical')),
  status text not null default 'open' check (status in ('open','in_progress','resolved','closed')),
  due_date date,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table if not exists public.sprints (
  id uuid primary key default gen_random_uuid(),
  project_id uuid not null references public.projects(id) on delete cascade,
  name text not null,
  goal text not null default '',
  status text not null default 'planned' check (status in ('planned','active','completed')),
  start_date date,
  end_date date,
  created_at timestamptz not null default now()
);

create table if not exists public.releases (
  id uuid primary key default gen_random_uuid(),
  project_id uuid references public.projects(id) on delete cascade,
  version text not null,
  description text not null default '',
  status text not null default 'unreleased' check (status in ('unreleased','released')),
  start_date date,
  release_date date,
  created_at timestamptz not null default now()
);

create table if not exists public.notifications (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users(id) on delete cascade,
  title text not null,
  description text not null default '',
  category text not null default 'workspace',
  read_at timestamptz,
  created_at timestamptz not null default now()
);

create table if not exists public.activity_logs (
  id uuid primary key default gen_random_uuid(),
  actor_id uuid references auth.users(id) on delete set null,
  project_id uuid references public.projects(id) on delete cascade,
  action text not null,
  entity_type text not null,
  entity_id uuid,
  details jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now()
);

create index if not exists project_milestones_project_idx on public.project_milestones(project_id);
create index if not exists issues_project_idx on public.issues(project_id);
create index if not exists issues_status_idx on public.issues(status);
create index if not exists sprints_project_idx on public.sprints(project_id);
create index if not exists releases_project_idx on public.releases(project_id);
create index if not exists notifications_user_read_idx on public.notifications(user_id, read_at);
create index if not exists activity_logs_project_created_idx on public.activity_logs(project_id, created_at desc);

alter table public.methodology_metrics enable row level security;
alter table public.project_milestones enable row level security;
alter table public.issues enable row level security;
alter table public.sprints enable row level security;
alter table public.releases enable row level security;
alter table public.notifications enable row level security;
alter table public.activity_logs enable row level security;

grant select, insert, update, delete on public.methodology_metrics, public.project_milestones, public.issues, public.sprints, public.releases, public.notifications, public.activity_logs to authenticated;

drop policy if exists "authenticated methodology metrics" on public.methodology_metrics;
drop policy if exists "authenticated milestones" on public.project_milestones;
drop policy if exists "authenticated issues" on public.issues;
drop policy if exists "authenticated sprints" on public.sprints;
drop policy if exists "authenticated releases" on public.releases;
drop policy if exists "users own notifications" on public.notifications;
drop policy if exists "authenticated activity logs" on public.activity_logs;

create policy "authenticated methodology metrics" on public.methodology_metrics for all to authenticated using (true) with check (true);
create policy "authenticated milestones" on public.project_milestones for all to authenticated using (true) with check (true);
create policy "authenticated issues" on public.issues for all to authenticated using (true) with check (true);
create policy "authenticated sprints" on public.sprints for all to authenticated using (true) with check (true);
create policy "authenticated releases" on public.releases for all to authenticated using (true) with check (true);
create policy "users own notifications" on public.notifications for all to authenticated using (user_id = auth.uid()) with check (user_id = auth.uid());
create policy "authenticated activity logs" on public.activity_logs for all to authenticated using (true) with check (true);

-- Screenshot-derived dashboard cards. These are seed records, not verified old DB records.
insert into public.methodology_metrics (name, display_metric, metric_label, note)
values
  ('Waterfall Methodology', '49/50', 'Water Fall Methodology', 'Get More Space...'),
  ('Agile Methodology', '$34,245', 'Agile Methodology', 'Last 24 Hours'),
  ('DevOps Methodology', '75', 'DevOps Methodology', 'Tracked from Github'),
  ('Rapid Application', '+245', 'Rapid Application Methodology', 'Just Updated')
on conflict (name) do update set display_metric = excluded.display_metric, metric_label = excluded.metric_label, note = excluded.note;

-- Screenshot-derived project rows. They are inserted only when at least one Auth user exists.
with owner as (select id from auth.users order by created_at limit 1), seed(name, project_key, project_type, budget, status, methodology) as (
  values
    ('E2E Tracking', 'E2E-001', 'HR', 36738, 'active', 'waterfall'),
    ('PMT', 'PMT-002', 'Sales', 23789, 'active', 'agile'),
    ('Smart Email', 'SEM-003', 'IT', 56142, 'planning', 'devops'),
    ('Clinical Data', 'CLD-004', 'Requirement', 38735, 'planning', 'rapid_application')
)
insert into public.projects (owner_id, name, project_key, project_type, budget, status, methodology, description, start_date, due_date)
select owner.id, seed.name, seed.project_key, seed.project_type, seed.budget, seed.status, seed.methodology,
       'Imported from the supplied legacy PMT screenshot.', current_date, current_date + interval '30 days'
from owner cross join seed
where not exists (select 1 from public.projects p where p.name = seed.name);

with creator as (select id from auth.users order by created_at limit 1), project_ref as (select id from public.projects where name = 'E2E Tracking' limit 1)
insert into public.tasks (project_id, created_by, title, description, status, priority)
select project_ref.id, creator.id, seed.title, 'Imported from the supplied legacy PMT screenshot.', seed.status, seed.priority
from creator cross join project_ref cross join (values
  ('Sign contract for "What are conference organizers afraid of?"', 'done', 'high'),
  ('Lines From Great Russian Literature? Or E-mails From My Boss?', 'todo', 'medium'),
  ('Flooded: One year later, assessing what was lost and what was found when a ravaging rain swept through metro Detroit', 'in_progress', 'high'),
  ('Create 4 Invisible User Experiences you Never Knew About', 'done', 'medium')
) as seed(title, status, priority)
where not exists (select 1 from public.tasks t where t.project_id = project_ref.id and t.title = seed.title);

create table if not exists public.pmt_modules (
  id uuid primary key default gen_random_uuid(),
  slug text not null unique,
  title text not null,
  description text not null default '',
  delivery_mode text not null default 'Sequential',
  source text not null default 'screenshot_seed',
  created_at timestamptz not null default now()
);

create table if not exists public.pmt_module_items (
  id uuid primary key default gen_random_uuid(),
  module_id uuid not null references public.pmt_modules(id) on delete cascade,
  position integer not null,
  label text not null,
  status text not null default 'planned' check (status in ('planned','active','completed')),
  created_at timestamptz not null default now(),
  unique(module_id, position)
);

alter table public.pmt_modules enable row level security;
alter table public.pmt_module_items enable row level security;
grant select, insert, update, delete on public.pmt_modules, public.pmt_module_items to authenticated;
drop policy if exists "authenticated pmt modules" on public.pmt_modules;
drop policy if exists "authenticated pmt module items" on public.pmt_module_items;
create policy "authenticated pmt modules" on public.pmt_modules for all to authenticated using (true) with check (true);
create policy "authenticated pmt module items" on public.pmt_module_items for all to authenticated using (true) with check (true);

insert into public.pmt_modules (slug, title, description, delivery_mode)
values
  ('waterfall', 'Waterfall Methodology', 'A staged delivery plan with clear approvals between discovery, design, build, test, and launch.', 'Sequential'),
  ('agile', 'Agile Methodology', 'Short feedback loops, visible priorities, and a team rhythm built around increments.', 'Iterative'),
  ('devops', 'DevOps Methodology', 'Connect development and operations with automated delivery and reliable feedback.', 'Iterative'),
  ('rapid', 'Rapid Application', 'Move from idea to validated release with focused prototypes and quick stakeholder feedback.', 'Iterative'),
  ('issues', 'Issues', 'Track bugs, blockers, and delivery risks from one queue.', 'Operational'),
  ('addons', 'Add-Ons', 'Optional workspace modules for the way your team works.', 'Configurable'),
  ('notifications', 'Notifications', 'Keep important workspace activity visible.', 'Operational'),
  ('capacity', 'Capacity Tracker', 'Compare assigned work with available team capacity.', 'Operational'),
  ('support', 'TL Support', 'Connect with the Beedata Technology support team.', 'Support')
on conflict (slug) do update set title = excluded.title, description = excluded.description, delivery_mode = excluded.delivery_mode;

insert into public.pmt_module_items (module_id, position, label, status)
select m.id, seed.position, seed.label, seed.status
from public.pmt_modules m
join (values
  ('waterfall', 1, 'Requirements', 'active'), ('waterfall', 2, 'Design & planning', 'active'), ('waterfall', 3, 'Build', 'planned'), ('waterfall', 4, 'Quality assurance', 'planned'), ('waterfall', 5, 'Release', 'planned'),
  ('agile', 1, 'Backlog', 'active'), ('agile', 2, 'Sprint planning', 'active'), ('agile', 3, 'Daily sync', 'planned'), ('agile', 4, 'Review', 'planned'), ('agile', 5, 'Retrospective', 'planned'),
  ('devops', 1, 'Plan', 'active'), ('devops', 2, 'Code', 'active'), ('devops', 3, 'Build', 'planned'), ('devops', 4, 'Test', 'planned'), ('devops', 5, 'Release', 'planned'), ('devops', 6, 'Deploy', 'planned'), ('devops', 7, 'Operate', 'planned'),
  ('rapid', 1, 'Idea', 'active'), ('rapid', 2, 'Prototype', 'active'), ('rapid', 3, 'Validate', 'planned'), ('rapid', 4, 'Iterate', 'planned'), ('rapid', 5, 'Launch', 'planned'),
  ('issues', 1, 'Bugs', 'active'), ('issues', 2, 'Website', 'planned'), ('issues', 3, 'Server', 'planned'), ('issues', 4, 'Requirement', 'planned'),
  ('addons', 1, 'Reports', 'planned'), ('addons', 2, 'Time tracking', 'planned'), ('addons', 3, 'Custom fields', 'planned'), ('addons', 4, 'Approval flows', 'planned'),
  ('notifications', 1, 'Mike John responded to your email', 'active'), ('notifications', 2, 'You have 5 new tasks', 'active'), ('notifications', 3, 'You are now connected with Andrew', 'planned'), ('notifications', 4, 'Project status changed', 'planned'),
  ('capacity', 1, 'Engineering', 'active'), ('capacity', 2, 'Design', 'planned'), ('capacity', 3, 'QA', 'planned'), ('capacity', 4, 'Project management', 'planned'),
  ('support', 1, 'Technical support', 'planned'), ('support', 2, 'Implementation help', 'planned'), ('support', 3, 'Account assistance', 'planned')
) as seed(slug, position, label, status) on seed.slug = m.slug
where not exists (select 1 from public.pmt_module_items i where i.module_id = m.id and i.position = seed.position);
