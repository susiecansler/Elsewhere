-- Elsewhere — accounts and community verification.
--
-- Run this once in the Supabase SQL editor. It is written to be re-runnable:
-- every statement is guarded, so applying it twice is harmless.
--
-- The shape is lifted from pipeline/elsewhere/store.py, which has held CLI
-- review judgments since before there were accounts. Two properties carried
-- over deliberately:
--
--   * One row per (person, place, target city, candidate). Re-judging
--     replaces your own answer and never touches anyone else's.
--   * Disagreement is preserved rather than resolved. Two locals splitting on
--     whether Metro is the Chicago Cat's Cradle is not noise to be averaged
--     away — it is the signal that a role is contested.
--
-- Everything is protected by row-level security. Supabase's anon key is
-- published in the page on purpose; these policies, not the key, are what
-- keep one person from editing another's data.

-- ─── Profiles ──────────────────────────────────────────────────────────────
-- One row per account. Created automatically on sign-up by the trigger below,
-- so the app never has to remember to do it.

create table if not exists public.profiles (
  id          uuid primary key references auth.users on delete cascade,
  handle      text unique,
  display_name text,
  created_at  timestamptz not null default now()
);

alter table public.profiles enable row level security;

drop policy if exists "profiles are public" on public.profiles;
create policy "profiles are public"
  on public.profiles for select using (true);

drop policy if exists "you may edit your own profile" on public.profiles;
create policy "you may edit your own profile"
  on public.profiles for update using (auth.uid() = id);

create or replace function public.handle_new_user()
returns trigger language plpgsql security definer set search_path = public as $$
begin
  insert into public.profiles (id, display_name)
  values (new.id, split_part(new.email, '@', 1))
  on conflict (id) do nothing;
  return new;
end;
$$;

drop trigger if exists on_auth_user_created on auth.users;
create trigger on_auth_user_created
  after insert on auth.users
  for each row execute function public.handle_new_user();

-- ─── Judgments ─────────────────────────────────────────────────────────────
-- The point of the whole feature. `elsewhere eval` refuses to publish an
-- accuracy number until it has judgments written by someone other than the
-- model family that generated the matches. These are those judgments.

create table if not exists public.judgments (
  id           bigint generated always as identity primary key,
  user_id      uuid not null references auth.users on delete cascade,
  source_city  text not null,
  source_name  text not null,
  target_city  text not null,
  candidate    text not null,
  verdict      text not null check (verdict in ('yes', 'no')),
  note         text check (note is null or length(note) <= 400),
  created_at   timestamptz not null default now(),
  unique (user_id, source_city, source_name, target_city, candidate)
);

create index if not exists judgments_place_idx
  on public.judgments (source_city, source_name, target_city, candidate);
create index if not exists judgments_user_idx on public.judgments (user_id);

alter table public.judgments enable row level security;

-- Readable by everyone, including signed-out visitors: the counts on a card
-- are the product, and hiding them behind a login would defeat the point.
drop policy if exists "judgments are public" on public.judgments;
create policy "judgments are public"
  on public.judgments for select using (true);

-- Writable only as yourself. `with check` is what stops a signed-in person
-- from inserting a row attributed to somebody else.
drop policy if exists "you may add your own judgments" on public.judgments;
create policy "you may add your own judgments"
  on public.judgments for insert to authenticated
  with check (auth.uid() = user_id);

drop policy if exists "you may change your own judgments" on public.judgments;
create policy "you may change your own judgments"
  on public.judgments for update to authenticated
  using (auth.uid() = user_id) with check (auth.uid() = user_id);

drop policy if exists "you may withdraw your own judgments" on public.judgments;
create policy "you may withdraw your own judgments"
  on public.judgments for delete to authenticated
  using (auth.uid() = user_id);

-- ─── Tallies ───────────────────────────────────────────────────────────────
-- Aggregated so a card can show "8 locals agree" with one cheap read instead
-- of pulling every row and counting in the browser.

create or replace view public.judgment_tallies as
  select source_city, source_name, target_city, candidate,
         count(*) filter (where verdict = 'yes') as yes_count,
         count(*) filter (where verdict = 'no')  as no_count
  from public.judgments
  group by source_city, source_name, target_city, candidate;

-- ─── Reputation ────────────────────────────────────────────────────────────
-- Deliberately just a count, not a score. A weighted reputation invites
-- gaming and implies a precision nobody has measured — the same reason match
-- strength is shown in words rather than as a percentage.

create or replace view public.contributors as
  select p.id, p.display_name, p.handle, count(j.id) as judgments
  from public.profiles p
  left join public.judgments j on j.user_id = p.id
  group by p.id, p.display_name, p.handle;
