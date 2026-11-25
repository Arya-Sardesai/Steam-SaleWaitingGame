-- One row per game: store metadata, cleaned.
-- is_adult follows the rule decided from the pilot: content descriptor 3 or 4.
-- (1 = "some nudity" is kept: Cyberpunk, Witcher 3 and Baldur's Gate 3 all carry it.)

with source as (
    select * from {{ source('raw', 'steam_appdetails') }}
    where status = 'ok'
)

select
    appid::bigint                                                      as appid,
    name,
    type,
    coalesce(is_free, false)                                           as is_free,
    coalesce(coming_soon, false)                                       as is_coming_soon,
    required_age,
    release_date_raw,
    -- Steam mixes formats: "18 Apr, 2011", "Apr 18, 2011", "2027", "To be announced"
    coalesce(
        try_strptime(release_date_raw, '%d %b, %Y'),
        try_strptime(release_date_raw, '%b %d, %Y'),
        try_strptime(release_date_raw, '%d %B, %Y'),
        try_strptime(release_date_raw, '%B %d, %Y')
    )::date                                                            as release_date,
    developers,
    publishers,
    publishers[1]                                                      as main_publisher,
    genres,
    categories,
    content_descriptor_ids,
    list_has_any(coalesce(content_descriptor_ids, []), [3, 4])         as is_adult,
    price_initial_cents::integer                                       as regular_cents_at_fetch,
    metacritic,
    recommendations                                                    as review_count_proxy,
    cast(fetched_at as timestamp)                                      as fetched_at
from source
