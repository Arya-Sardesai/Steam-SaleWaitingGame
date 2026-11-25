-- During a sale, the price must be below the regular price.
-- Warn only: Steam occasionally changes the regular price mid-sale, which can trip this.
{{ config(severity = 'warn') }}
select sale_id, min_price_cents, regular_cents
from {{ ref('fct_sale_events') }}
where min_price_cents >= regular_cents
