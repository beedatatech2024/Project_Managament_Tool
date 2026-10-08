-- Read-only verification for the BeeData Project Management Tool.
-- Run in Supabase SQL Editor after migrations 001 through 004.

select table_name
from information_schema.tables
where table_schema = 'public'
  and table_name in (
    'profiles', 'projects', 'tasks', 'issues', 'notifications',
    'sprints', 'releases', 'task_dependencies', 'team_capacity',
    'workspace_settings', 'user_preferences', 'role_permissions', 'methodology_metrics', 'pmt_modules', 'pmt_module_items'
  )
order by table_name;

select table_name, column_name, data_type
from information_schema.columns
where table_schema = 'public'
  and (
    (table_name = 'tasks' and column_name in ('start_date', 'end_date', 'estimated_hours', 'progress', 'sprint_id', 'release_id'))
    or (table_name = 'issues' and column_name in ('severity', 'reporter_id', 'milestone_id', 'resolved_at', 'release_id'))
    or (table_name = 'sprints' and column_name in ('created_by', 'updated_at'))
    or (table_name = 'releases' and column_name in ('name', 'created_by', 'updated_at'))
  )
order by table_name, ordinal_position;

select 'projects' as table_name, count(*) as records from public.projects
union all select 'tasks', count(*) from public.tasks
union all select 'issues', count(*) from public.issues
union all select 'notifications', count(*) from public.notifications
union all select 'sprints', count(*) from public.sprints
union all select 'releases', count(*) from public.releases
union all select 'methodology_metrics', count(*) from public.methodology_metrics
union all select 'pmt_modules', count(*) from public.pmt_modules
union all select 'user_preferences', count(*) from public.user_preferences
union all select 'role_permissions', count(*) from public.role_permissions;
