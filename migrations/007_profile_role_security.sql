-- Tighten profile updates after 006_settings_administration.sql.
-- Existing profile data is preserved. Admin role changes remain supported.

drop policy if exists "authenticated profiles" on public.profiles;
drop policy if exists "authenticated profiles read" on public.profiles;
drop policy if exists "users update own profile" on public.profiles;

create policy "authenticated profiles read" on public.profiles
  for select to authenticated using (true);

create policy "users update own profile" on public.profiles
  for update to authenticated
  using (id = auth.uid() or public.is_workspace_admin())
  with check (id = auth.uid() or public.is_workspace_admin());

create or replace function public.protect_profile_role()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
begin
  if old.role is distinct from new.role and not public.is_workspace_admin() then
    raise exception 'Only workspace administrators can change profile roles';
  end if;
  return new;
end;
$$;

drop trigger if exists protect_profile_role on public.profiles;
create trigger protect_profile_role
before update on public.profiles
for each row execute procedure public.protect_profile_role();
