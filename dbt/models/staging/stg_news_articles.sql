{{
  config(
    materialized='view',
    description='Cleaned news articles joined with their sentiment scores.'
  )
}}

with articles as (
    select * from {{ source('signaldeck_raw', 'news_articles') }}
),

scores as (
    select
        article_id,
        compound    as vader_compound,
        positive    as vader_positive,
        negative    as vader_negative,
        neutral     as vader_neutral,
        label       as sentiment_label,
        method      as scoring_method
    from {{ source('signaldeck_raw', 'sentiment_scores') }}
),

joined as (
    select
        a.article_id,
        a.ticker,
        a.headline,
        a.summary,
        a.url,
        a.source                                    as article_source,
        a.sentiment_label                           as raw_sentiment_label,
        cast(a.sentiment_score as float64)          as provider_sentiment_score,
        cast(a.published_at as timestamp)           as published_at,
        cast(a.ingested_at  as timestamp)           as ingested_at,

        -- enriched VADER fields (null when scoring not run)
        s.vader_compound,
        s.vader_positive,
        s.vader_negative,
        s.vader_neutral,
        coalesce(s.sentiment_label, a.sentiment_label)  as final_sentiment_label,
        s.scoring_method

    from articles a
    left join scores s using (article_id)
    where a.ticker   is not null
      and a.headline is not null
)

select * from joined
