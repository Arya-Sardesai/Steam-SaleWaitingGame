-- ONE ROW PER GAP BETWEEN SALES: the survival-analysis dataset.
--
-- Same idea as the CPSC 330 churn example: there, "how long until a customer churns?";
-- here, "after a sale ends, how long until the NEXT sale starts?"
--   duration_days   = days from the end of one sale to the start of the next
--   event_observed  = true if the next sale happened; false if it hasn't happened YET
--                     (right-censored: we only know the gap is at least this long)
-- Each gap starts when a sale ENDS, so the start date is always known (no left-censoring).

with sales as (
    select * from {{ ref('fct_sale_events') }}
),

observation_end as (
    -- the last day we know this game's price: latest snapshot, or latest ITAD change
    select appid, max(changed_date) as observed_until
    from {{ ref('int_price_changes') }}
    group by appid
),

latest_snapshot as (
    select max(snapshot_date) as d from {{ ref('stg_steam__prices') }}
),

gaps as (
    select
        s.appid,
        s.sale_number                                       as after_sale_number,
        s.end_date                                          as gap_start,
        lead(s.start_date) over (partition by s.appid order by s.started_at) as next_sale_start,
        s.max_cut_percent                                   as prev_sale_cut,
        s.duration_days                                     as prev_sale_length_days,
        s.regular_cents                                     as regular_cents,
        s.days_since_prev_sale                              as prev_gap_days
    from sales s
    where not s.is_ongoing
),

major_events as (
    -- past: platform-wide sale days found in the data (Summer/Winter/Spring/Autumn sales)
    select start_date from {{ ref('fct_platform_sale_days') }} where is_major_event
    union
    -- future: announced seasonal sales from the seed (e.g. Winter Sale 2026), which haven't
    -- happened yet so can't be found in the data
    select start_date from {{ ref('steam_events') }} where event_type = 'seasonal'
)

select
    g.appid::varchar || '-' || g.after_sale_number                         as gap_id,
    g.appid,
    g.after_sale_number,
    g.gap_start,
    g.next_sale_start,
    g.next_sale_start is not null                                          as event_observed,
    date_diff('day', g.gap_start,
              coalesce(g.next_sale_start,
                       greatest(o.observed_until, (select d from latest_snapshot)))) as duration_days,
    -- features known AT THE START of the gap (no peeking into the future)
    g.prev_sale_cut,
    g.prev_sale_length_days,
    g.prev_gap_days,
    g.regular_cents,
    month(g.gap_start)                                                     as gap_start_month,
    year(g.gap_start)                                                      as gap_start_year,
    -- Steam announces its sale calendar months ahead, so this is known in advance too
    (select min(e.start_date) from major_events e where e.start_date > g.gap_start)
                                                                           as next_major_event,
    date_diff('day', g.gap_start,
              (select min(e.start_date) from major_events e where e.start_date > g.gap_start))
                                                                           as days_to_next_major_event
from gaps g
left join observation_end o using (appid)
