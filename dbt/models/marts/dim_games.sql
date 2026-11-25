-- ONE ROW PER GAME: store metadata plus a summary of its sale behaviour.

with details as (
    select * from {{ ref('stg_steam__appdetails') }}
),

sale_summary as (
    select
        appid,
        count(*)                                  as total_sales,
        min(start_date)                           as first_sale_date,
        max(start_date)                           as last_sale_date,
        max(max_cut_percent)                      as deepest_cut_ever,
        min(min_price_cents)                      as lowest_price_ever_cents,
        median(max_cut_percent)                   as typical_cut_percent,
        median(days_since_prev_sale)              as typical_days_between_sales,
        median(duration_days)                     as typical_sale_length_days,
        bool_or(is_ongoing)                       as is_on_sale_now
    from {{ ref('fct_sale_events') }}
    group by appid
),

latest_price as (
    select appid, price_cents, regular_cents, cut_percent, snapshot_date
    from {{ ref('stg_steam__prices') }}
    qualify row_number() over (partition by appid order by snapshot_date desc) = 1
)

select
    d.appid,
    d.name,
    d.type,
    d.is_free,
    d.is_coming_soon,
    d.is_adult,
    d.release_date,
    date_diff('day', d.release_date, current_date)       as age_days,
    d.main_publisher,
    d.developers,
    d.genres,
    d.categories,
    d.metacritic,
    d.review_count_proxy,
    lp.price_cents                                        as current_price_cents,
    lp.regular_cents                                      as current_regular_cents,
    lp.cut_percent                                        as current_cut_percent,
    lp.snapshot_date                                      as price_as_of,
    coalesce(s.total_sales, 0)                            as total_sales,
    s.first_sale_date,
    s.last_sale_date,
    s.deepest_cut_ever,
    s.lowest_price_ever_cents,
    s.typical_cut_percent,
    s.typical_days_between_sales,
    s.typical_sale_length_days,
    coalesce(s.is_on_sale_now, false)                     as is_on_sale_now,
    -- usable for the "wait or buy" model: real, released, for sale, not adult
    d.type = 'game' and not d.is_free and not d.is_coming_soon and not d.is_adult
        and lp.price_cents is not null                    as is_modelable
from details d
left join sale_summary s using (appid)
left join latest_price lp using (appid)
