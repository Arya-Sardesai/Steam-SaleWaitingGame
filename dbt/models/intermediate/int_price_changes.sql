-- ONE timeline of price changes per game, from two sources stitched together:
--   1. IsThereAnyDeal history: years of past changes (up to when we fetched it)
--   2. Our own daily snapshots: everything after that
-- Rows that don't actually change the price (e.g. the first snapshot repeating the last
-- ITAD state) are dropped, so each row is a real change.

with itad as (
    select appid, changed_at, price_cents, regular_cents, cut_percent, 'itad' as source
    from {{ ref('stg_itad__price_log') }}
),

itad_last as (
    select appid, max(changed_date) as last_itad_date
    from {{ ref('stg_itad__price_log') }}
    group by appid
),

snapshots as (
    select
        p.appid,
        cast(p.snapshot_date as timestamp) as changed_at,
        p.price_cents,
        p.regular_cents,
        p.cut_percent,
        'snapshot' as source
    from {{ ref('stg_steam__prices') }} p
    left join itad_last l using (appid)
    where p.is_priced
      and p.snapshot_date > coalesce(l.last_itad_date, date '1900-01-01')
),

unioned as (
    select * from itad
    union all
    select * from snapshots
),

-- Real data has a few games with two different prices at the EXACT same timestamp.
-- That makes "which came first?" undefined and creates zero-length sales.
-- Keep one row per (game, moment): the deeper discount, since that's what buyers saw.
deduped as (
    select *
    from unioned
    qualify row_number() over (
        partition by appid, changed_at
        order by cut_percent desc, price_cents asc, source
    ) = 1
),

with_previous as (
    select
        *,
        lag(price_cents) over w as prev_price_cents,
        lag(cut_percent) over w as prev_cut_percent
    from deduped
    window w as (partition by appid order by changed_at)
)

select
    appid,
    changed_at,
    cast(changed_at as date) as changed_date,
    price_cents,
    regular_cents,
    cut_percent,
    source
from with_previous
where prev_price_cents is null
   or price_cents   is distinct from prev_price_cents
   or cut_percent   is distinct from prev_cut_percent
