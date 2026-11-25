-- A sale's discount must be between 1% and 100%. Any row returned = test fails.
select sale_id, max_cut_percent
from {{ ref('fct_sale_events') }}
where max_cut_percent < 1 or max_cut_percent > 100
