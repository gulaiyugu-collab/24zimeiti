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
