-- One row per game per day: what Steam charged in Canada that day.
-- Error rows are dropped here: a failed fetch is "unknown", not a price.

with source as (
    select * from {{ source('raw', 'steam_prices') }}
)

select
    appid::bigint                                as appid,
    cast(snapshot_date as date)                  as snapshot_date,
    appid::varchar || '-' || snapshot_date       as price_key,
    status,
    status = 'priced'                            as is_priced,
    currency,
    price_initial_cents::integer                 as regular_cents,
    price_final_cents::integer                   as price_cents,
    coalesce(discount_percent, 0)::integer       as cut_percent,
    coalesce(discount_percent, 0) > 0            as is_on_sale
from source
where status <> 'error'
