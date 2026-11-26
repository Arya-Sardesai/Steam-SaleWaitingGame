-- A gap between sales can't be negative. (Zero is allowed: a sale ending and another
-- starting the same day.)
select gap_id, duration_days
from {{ ref('fct_sale_gaps') }}
where duration_days < 0
