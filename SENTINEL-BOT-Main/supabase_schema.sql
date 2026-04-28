-- =============================================================
-- SP4RK Bot — Supabase schema
-- Run this once in the Supabase SQL Editor (Database → SQL Editor → New query)
-- Safe to re-run: every CREATE uses IF NOT EXISTS.
-- =============================================================

-- ------------- Existing tables (already used by the bot) -----
create table if not exists sentinel_config (
    id          int primary key,
    data        jsonb not null default '{}'::jsonb,
    updated_at  timestamptz default now()
);

create table if not exists sentinel_users (
    discord_id   text primary key,
    username     text,
    avatar_url   text,
    email        text,
    created_at   timestamptz default now(),
    last_login   timestamptz
);

create table if not exists sentinel_warnings (
    id          bigserial primary key,
    guild_id    text not null,
    member_id   text not null,
    username    text,
    count       int  default 1,
    reason      text,
    timestamp   timestamptz default now()
);
create index if not exists idx_sentinel_warnings_guild_member
    on sentinel_warnings (guild_id, member_id);

create table if not exists sentinel_mod_logs (
    id          bigserial primary key,
    guild_id    text not null,
    action      text,
    target      text,
    moderator   text,
    reason      text,
    timestamp   timestamptz default now()
);

create table if not exists sentinel_notes (
    id          bigserial primary key,
    guild_id    text not null,
    member_id   text not null,
    username    text,
    note        text,
    moderator   text,
    timestamp   timestamptz default now()
);

create table if not exists sentinel_xp (
    guild_id    text not null,
    user_id     text not null,
    username    text,
    xp          int default 0,
    level       int default 0,
    messages    int default 0,
    primary key (guild_id, user_id)
);

create table if not exists sentinel_reaction_roles (
    id          bigserial primary key,
    guild_id    text not null,
    channel_id  text not null,
    message_id  text not null,
    emoji       text not null,
    role_id     text not null,
    role_name   text,
    unique (message_id, emoji)
);

-- ------------- NEW: giveaways ------------------------------------
create table if not exists sentinel_giveaways (
    message_id    text primary key,
    channel_id    text not null,
    guild_id      text not null,
    prize         text not null,
    winners       int  default 1,
    host_id       text,
    host_name     text,
    ends_at       timestamptz,
    ended         boolean default false,
    created_at    timestamptz default now(),
    ended_at      timestamptz,
    winner_names  text,
    forced_winner boolean default false
);
create index if not exists idx_sentinel_giveaways_active
    on sentinel_giveaways (ended, ends_at);

-- ------------- NEW: tickets --------------------------------------
create table if not exists sentinel_tickets (
    id          bigserial primary key,
    ticket_num  int  not null,
    channel_id  text not null,
    guild_id    text not null,
    user_id     text not null,
    user_name   text,
    reason      text,
    status      text default 'open',
    created_at  timestamptz default now(),
    closed_at   timestamptz,
    closed_by   text,
    unique (guild_id, ticket_num)
);
create index if not exists idx_sentinel_tickets_status
    on sentinel_tickets (status);

-- ------------- NEW: autoresponder --------------------------------
create table if not exists sentinel_autoresponder (
    id          bigserial primary key,
    trigger     text unique not null,
    response    text not null,
    match_type  text default 'contains'
);

-- ------------- NEW: User feedback (star ratings + comments) ---------
create table if not exists sentinel_feedback (
    id           bigserial primary key,
    discord_id   text,
    user_name    text,
    rating       int  not null check (rating between 1 and 5),
    comment      text default '',
    source       text default 'main',  -- 'main' | 'server_official' | …
    created_at   timestamptz default now()
);
create index if not exists idx_sentinel_feedback_created
    on sentinel_feedback (created_at desc);

-- ------------- NEW: Server Official full state (single-row JSONB) ----
-- The Server Official bot persists ALL of its state (configs, goals,
-- announcements, polls, reminders, custom commands, analytics, logs,
-- backups, …) into one JSONB column. Mirrors server_official_state.json
-- so the bot survives container restarts and ephemeral disks.
create table if not exists server_official_state (
    id          int primary key,
    data        jsonb not null default '{}'::jsonb,
    updated_at  timestamptz default now()
);
