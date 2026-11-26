-- One row per past Steam price change (from IsThereAnyDeal), in CAD.
-- "cut" is the discount percent at that moment; 0 means back to regular price.

with source as (
    select * from {{ source('raw', 'itad_price_log') }}
)

select
    appid::bigint                              as appid,
    itad_id,
    cast(changed_at as timestamp)              as changed_at,   -- UTC (profile sets TimeZone)
    cast(changed_at as date)                   as changed_date,
    price_cents::integer                       as price_cents,
    regular_cents::integer                     as regular_cents,
    coalesce(cut_percent, 0)::integer          as cut_percent,
    currency
from source
where price_cents is not null
  -- Impossible records seen in the oldest (2016) history: a "120% off" sale, and "sales"
  -- priced ABOVE the regular price. The discount and the prices contradict each other, so
  -- we can't tell which number is wrong. Drop them rather than guess.
  and coalesce(cut_percent, 0) between 0 and 100
  and not (coalesce(cut_percent, 0) > 0 and price_cents >= regular_cents)
