-- ONE ROW PER DAY: how many games started a sale that day.
-- Platform-wide events (Summer Sale, fests) show up as spikes, which lets us find past
-- events in the data itself instead of hand-typing ten years of sale dates.
-- Known upcoming events come from the steam_events seed.

with starts as (
    select start_date, count(*) as sales_started, avg(max_cut_percent) as avg_cut_percent
    from {{ ref('fct_sale_events') }}
    where not is_left_censored      -- true start date unknown, would fake a spike
    group by start_date
),

scored as (
    select
        *,
        -- compare each day to the typical day in the surrounding 60 days
        median(sales_started) over (order by start_date
                                    rows between 30 preceding and 30 following) as local_median
    from starts
),

-- Events can overlap by a day (e.g. one fest ends the day the next begins), so a date
-- can match two events. Combine their names into one field to keep one row per day.
events_per_day as (
    select s.start_date, string_agg(e.event_name, ' + ' order by e.start_date) as known_event
    from scored s
    join {{ ref('steam_events') }} e
        on s.start_date between e.start_date and e.end_date
    group by s.start_date
)

select
    s.start_date,
    s.sales_started,
    round(s.avg_cut_percent, 1)                                   as avg_cut_percent,
    s.local_median,
    round(s.sales_started / nullif(s.local_median, 0), 1)         as spike_ratio,
    s.sales_started >= 5 * greatest(s.local_median, 1)            as is_spike,
    -- the big platform-wide events: real seasonal sales were 100x+ the local median
    s.sales_started >= 30 * greatest(s.local_median, 1)
        and s.sales_started >= 100                                as is_major_event,
    ev.known_event
from scored s
left join events_per_day ev using (start_date)
