-- A finished sale must end after it starts, and sales of one game must not overlap.
with ordered as (
    select
        sale_id, appid, started_at, ended_at,
        lead(started_at) over (partition by appid order by started_at) as next_start
    from {{ ref('fct_sale_events') }}
)
select *
from ordered
where (ended_at is not null and ended_at <= started_at)
   or (next_start is not null and ended_at is not null and next_start < ended_at)
