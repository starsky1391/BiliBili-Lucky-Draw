


CREATE TABLE IF NOT EXISTS t_draw_dynamic
(
    dyn_url     varchar(255) not null
        primary key,
    dynamic_id  varchar(64)                    null,
    up_id       varchar(50)                    null,
    publish_time datetime                       null,
    insert_time datetime                    null,
    lottery_time datetime                   null,
    lottery_source varchar(30)                null,
    source      varchar(255) null,
    note        varchar(255)                null,
    status      varchar(20)                 null
);

CREATE TABLE IF NOT EXISTS t_account_tasks (
    account_key TEXT PRIMARY KEY,
    collect INTEGER NOT NULL DEFAULT 1,
    backfill_personal INTEGER NOT NULL DEFAULT 0,
    cleanup INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS t_scan_cache
(
    scan_key    varchar(64)   not null
        primary key,
    scan_type   varchar(50)   not null,
    scan_url    varchar(1024) not null,
    source      varchar(255)  null,
    note        varchar(255)  null,
    status      varchar(20)   null,
    link_count  int           null,
    last_error  varchar(500)  null,
    insert_time datetime      null,
    update_time datetime      null
);

CREATE TABLE IF NOT EXISTS t_followdups
(
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    up_id       varchar(255) null,
    up_url      varchar(255) null,
    status      int          null,
    update_time datetime     null,
    user_id     varchar(50)  null
);

CREATE TABLE IF NOT EXISTS t_prize
(
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    status    int          null,
    prize_url varchar(255) null,
    user_id   varchar(50)  null
);

CREATE TABLE IF NOT EXISTS t_share_info
(
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    share_url    varchar(255) null,
    status       int          not null,
    upId         varchar(50)  null,
    upUrl        varchar(255) null,
    machine_ip   varchar(20)  null,
    share_time   datetime     null,
    share_status int          null,
    user_id      varchar(50)  null
);

CREATE TABLE IF NOT EXISTS t_shared_urls
(
    dyn_url     varchar(255) not null,
    insert_time datetime     null,
    update_time datetime     null,
    status      varchar(50)  null,
    user_id     varchar(20)  not null,
    primary key (user_id, dyn_url)
);

CREATE TABLE IF NOT EXISTS t_statistics
(
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    content     varchar(255) null,
    insert_time datetime                      null,
    note        varchar(500) null,
    user_id     varchar(50)                   null
);

create table if not exists t_account
(
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    account_key varchar(100) not null unique,
    bili_uid    varchar(50) null,
    username    varchar(255) null,
    remark      varchar(255) null,
    enabled     tinyint not null default 1,
    config_file varchar(255) null,
    insert_time datetime null,
    update_time datetime null
);

create table if not exists t_up_info
(
    up_id       varchar(50) not null primary key,
    is_managed  tinyint not null default 1,
    insert_time datetime null,
    update_time datetime null
);

create table if not exists t_account_dynamic
(
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    dynamic_id       varchar(64) not null,
    account_key      varchar(100) not null,
    share_status     tinyint not null default 0,
    share_time       datetime null,
    own_dynamic_id   varchar(64) null,
    own_dynamic_url  varchar(255) null,
    cleanup_status   tinyint not null default 0,
    error_message    varchar(500) null,
    insert_time      datetime null,
    update_time      datetime null,
    UNIQUE (dynamic_id, account_key)
);

create table if not exists t_pending_unfollow
(
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    account_key       varchar(100) not null,
    up_id             varchar(50) not null,
    source_dynamic_id varchar(64) null,
    status            tinyint not null default 2,
    error_message     varchar(500) null,
    retry_time        datetime null,
    insert_time       datetime not null,
    update_time       datetime not null,
    UNIQUE (account_key, up_id)
);

create table if not exists t_auth_session
(
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    account_key       varchar(100) not null unique,
    status            varchar(30) not null default 'UNKNOWN',
    uid               varchar(50) null,
    last_verified_at  datetime null,
    cookie_saved_at   datetime null,
    last_error        varchar(500) null,
    update_time       datetime not null
);
