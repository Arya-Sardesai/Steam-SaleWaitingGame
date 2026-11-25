-- ONE ROW PER SALE. This is the table the model learns from.
--
-- A "sale" is an unbroken stretch where the discount is above 0%. If the discount
-- changes mid-sale (say 50% then 60%), it's still one sale; we keep the deepest cut.
-- Technique: "gaps and islands" - flag each row as sale / not sale, start a new island
-- whenever that flag flips, then collapse each island into one row.

with changes as (
    select *, cut_percent > 0 as is_sale
    from {{ ref('int_price_changes') }}
),

flagged as (
    select
        *,
        case when is_sale is distinct from lag(is_sale) over w then 1 else 0 end as is_new_island
    from changes
    window w as (partition by appid order by changed_at)
),

islands as (
    select
        *,
        sum(is_new_island) over (partition by appid order by changed_at
                                 rows between unbounded preceding and current row) as island_id,
        min(changed_at) over (partition by appid) as first_seen_at
    from flagged
),

collapsed as (
    select
        appid,
        island_id,
        bool_or(is_sale)          as is_sale,
        min(changed_at)           as started_at,
        max(cut_percent)          as max_cut_percent,
        min(price_cents)          as min_price_cents,
        max(regular_cents)        as regular_cents,
        count(*)                  as price_changes_in_island,
        min(source)               as source,
        any_value(first_seen_at)  as first_seen_at
    from islands
    group by appid, island_id
),

with_end as (
    select
        *,
        lead(started_at) over (partition by appid order by started_at) as ended_at
    from collapsed
),

sales as (
    select * from with_end where is_sale
)

select
    -- appid + sale number, not appid + date: real data has games where a sale ends and a
    -- new one starts on the same day, which made date-based IDs collide.
    appid::varchar || '-' || row_number() over (partition by appid order by started_at)
                                                                                as sale_id,
    appid,
    row_number() over (partition by appid order by started_at)                 as sale_number,
    started_at,
    cast(started_at as date)                                                    as start_date,
    ended_at,
    cast(ended_at as date)                                                      as end_date,
    ended_at is null                                                            as is_ongoing,
    date_diff('day', cast(started_at as date), cast(ended_at as date))          as duration_days,
    max_cut_percent,
    min_price_cents,
    regular_cents,
    regular_cents - min_price_cents                                             as max_saving_cents,
    -- gap since the previous sale ended: the "cooldown" feature
    date_diff('day',
              cast(lag(ended_at) over (partition by appid order by started_at) as date),
              cast(started_at as date))                                         as days_since_prev_sale,
    price_changes_in_island,
    -- LEFT-CENSORED: the game was already on sale the first time we ever saw it, so the
    -- real start date is unknown (it's just "when our data begins"). Without this flag,
    -- every game on sale on our first snapshot day looked like a sale starting that day.
    started_at = first_seen_at                                                  as is_left_censored,
    source
from sales
