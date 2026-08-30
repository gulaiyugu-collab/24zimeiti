-- Project024 cloud MVP schema for Supabase Postgres.
-- Run this only in a new Supabase project SQL editor.

create extension if not exists pgcrypto;

create table if not exists public.cloud_tasks (
  id text primary key,
  owner_id uuid not null references auth.users(id) on delete cascade,
  idempotency_key text not null,
  status text not null check (status in ('queued', 'processing', 'retryable', 'completed', 'failed')),
  payload jsonb not null default '{}'::jsonb,
  result jsonb,
  error jsonb,
  worker_id text,
  lease_until timestamptz,
  retry_count integer not null default 0 check (retry_count >= 0),
  max_retries integer not null default 1 check (max_retries between 0 and 3),
  created_at timestamptz not null default timezone('utc', now()),
  updated_at timestamptz not null default timezone('utc', now()),
  completed_at timestamptz,
  unique (owner_id, idempotency_key)
);

create index if not exists cloud_tasks_claim_idx
  on public.cloud_tasks (status, lease_until, created_at);

create table if not exists public.usage_events (
  id uuid primary key default gen_random_uuid(),
  owner_id uuid not null references auth.users(id) on delete cascade,
  task_id text references public.cloud_tasks(id) on delete set null,
  provider text not null,
  model text,
  prompt_tokens integer,
  completion_tokens integer,
  total_tokens integer,
  request_count integer not null default 1,
  retry_count integer not null default 0,
  elapsed_ms integer,
  created_at timestamptz not null default timezone('utc', now())
);

create index if not exists usage_events_owner_created_idx
  on public.usage_events (owner_id, created_at desc);

create table if not exists public.invites (
  code_hash text primary key,
  max_uses integer not null default 1 check (max_uses between 1 and 100),
  used_count integer not null default 0 check (used_count >= 0),
  expires_at timestamptz,
  disabled boolean not null default false,
  created_at timestamptz not null default timezone('utc', now())
);

create or replace function public.set_updated_at()
returns trigger
language plpgsql
as $$
begin
  new.updated_at = timezone('utc', now());
  return new;
end;
$$;

drop trigger if exists cloud_tasks_set_updated_at on public.cloud_tasks;
create trigger cloud_tasks_set_updated_at
before update on public.cloud_tasks
for each row execute function public.set_updated_at();

-- Worker state changes run as locked server-side RPCs so two computers cannot
-- claim the same task. These functions are callable only with the server key.
create or replace function public.claim_cloud_task(
  p_worker_id text,
  p_lease_seconds integer default 120
)
returns setof public.cloud_tasks
language plpgsql
security definer
set search_path = public
as $$
begin
  return query
  update public.cloud_tasks as task
  set status = 'processing',
      worker_id = p_worker_id,
      lease_until = timezone('utc', now()) + make_interval(secs => p_lease_seconds),
      updated_at = timezone('utc', now())
  where task.id = (
    select candidate.id
    from public.cloud_tasks as candidate
    where candidate.status in ('queued', 'retryable')
       or (candidate.status = 'processing'
           and candidate.lease_until is not null
           and candidate.lease_until <= timezone('utc', now()))
    order by candidate.created_at asc
    for update skip locked
    limit 1
  )
  returning task.*;
end;
$$;

create or replace function public.heartbeat_cloud_task(
  p_task_id text,
  p_worker_id text,
  p_lease_seconds integer default 120
)
returns setof public.cloud_tasks
language sql
security definer
set search_path = public
as $$
  update public.cloud_tasks
  set lease_until = timezone('utc', now()) + make_interval(secs => p_lease_seconds),
      updated_at = timezone('utc', now())
  where id = p_task_id and status = 'processing' and worker_id = p_worker_id
  returning *;
$$;

create or replace function public.complete_cloud_task(
  p_task_id text,
  p_worker_id text,
  p_result jsonb
)
returns setof public.cloud_tasks
language plpgsql
security definer
set search_path = public
as $$
begin
  if exists (select 1 from public.cloud_tasks where id = p_task_id and status = 'completed') then
    return query select * from public.cloud_tasks where id = p_task_id;
    return;
  end if;
  return query
  update public.cloud_tasks
  set status = 'completed', result = p_result, worker_id = null,
      lease_until = null, updated_at = timezone('utc', now()),
      completed_at = timezone('utc', now())
  where id = p_task_id and status = 'processing' and worker_id = p_worker_id
  returning *;
end;
$$;

create or replace function public.fail_cloud_task(
  p_task_id text,
  p_worker_id text,
  p_error jsonb,
  p_retryable boolean default true
)
returns setof public.cloud_tasks
language plpgsql
security definer
set search_path = public
as $$
declare
  next_status text;
begin
  if exists (select 1 from public.cloud_tasks where id = p_task_id and status = 'completed') then
    return query select * from public.cloud_tasks where id = p_task_id;
    return;
  end if;
  select case when p_retryable and retry_count + 1 <= max_retries then 'retryable' else 'failed' end
    into next_status
  from public.cloud_tasks
  where id = p_task_id and status = 'processing' and worker_id = p_worker_id;
  if next_status is null then
    return;
  end if;
  return query
  update public.cloud_tasks
  set status = next_status, error = p_error, retry_count = retry_count + 1,
      worker_id = null, lease_until = null, updated_at = timezone('utc', now())
  where id = p_task_id and status = 'processing' and worker_id = p_worker_id
  returning *;
end;
$$;

revoke all on function public.claim_cloud_task(text, integer) from public, anon, authenticated;
revoke all on function public.heartbeat_cloud_task(text, text, integer) from public, anon, authenticated;
revoke all on function public.complete_cloud_task(text, text, jsonb) from public, anon, authenticated;
revoke all on function public.fail_cloud_task(text, text, jsonb, boolean) from public, anon, authenticated;
grant execute on function public.claim_cloud_task(text, integer) to service_role;
grant execute on function public.heartbeat_cloud_task(text, text, integer) to service_role;
grant execute on function public.complete_cloud_task(text, text, jsonb) to service_role;
grant execute on function public.fail_cloud_task(text, text, jsonb, boolean) to service_role;

alter table public.cloud_tasks enable row level security;
alter table public.usage_events enable row level security;
alter table public.invites enable row level security;

drop policy if exists cloud_tasks_owner_select on public.cloud_tasks;
create policy cloud_tasks_owner_select on public.cloud_tasks
for select using (auth.uid() = owner_id);

drop policy if exists cloud_tasks_owner_insert on public.cloud_tasks;
create policy cloud_tasks_owner_insert on public.cloud_tasks
for insert with check (auth.uid() = owner_id);

drop policy if exists cloud_tasks_owner_update on public.cloud_tasks;
create policy cloud_tasks_owner_update on public.cloud_tasks
for update using (auth.uid() = owner_id)
with check (auth.uid() = owner_id);

drop policy if exists usage_events_owner_select on public.usage_events;
create policy usage_events_owner_select on public.usage_events
for select using (auth.uid() = owner_id);

-- The invites table intentionally has no client policy. Invite redemption must
-- happen through the server using Supabase service-role credentials.
