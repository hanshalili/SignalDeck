{{
  config(
    materialized='table',
    partition_by={
      "field": "event_date",
      "data_type": "date",
      "granularity": "day"
    },
    cluster_by=["ticker", "event_type"],
    description="Company catalyst events derived from news, sentiment shifts, volume spikes, and price reactions."
  )
}}

/*
  catalyst_tracking
  -----------------
  One row per catalyst event — news article that triggered a measurable
  price or sentiment response, or a volume/price spike day.

  Event types
  ───────────
  • news_sentiment_spike  : article with |vader_compound| ≥ 0.5
  • volume_spike          : day volume > 2× 20-day average
  • price_gap             : intraday return > 3% or < -3%
  • rsi_extreme           : RSI crossed oversold (< 30) or overbought (> 70)
  • ma_crossover          : MA-5 crossed MA-20 (golden / death cross)

  Metrics produced per event
  ──────────────────────────
  • Headline and sentiment label for news events
  • Price move on event day vs prior day
  • Volume spike multiplier
  • Forward 3-day price return (leading indicator of catalyst strength)
*/

with news as (
    select
        ticker,
        article_id,
        headline,
        article_source,
        published_at,
        cast(date(published_at) as date)    as event_date,
        final_sentiment_label,
        vader_compound,
        provider_sentiment_score
    from {{ ref('stg_news_articles') }}
    where vader_compound is not null
),

prices as (
    select
        ticker,
        price_date,
        close_price,
        volume,
        intraday_return_pct,
        daily_range,
        data_source
    from {{ ref('stg_stock_prices') }}
),

features as (
    select
        ticker,
        cast(date as date)                  as feature_date,
        cast(vol_avg_20    as float64)      as vol_avg_20,
        cast(rsi_14        as float64)      as rsi_14,
        cast(ma_5          as float64)      as ma_5,
        cast(ma_20         as float64)      as ma_20
    from {{ source('signaldeck_raw', 'processed_features') }}
),

-- Forward 3-day return: close 3 days after the event vs event close
price_with_forward as (
    select
        ticker,
        price_date,
        close_price,
        volume,
        intraday_return_pct,
        lead(close_price, 3) over (
            partition by ticker order by price_date
        )                                   as close_3d_forward,
        lag(close_price, 1) over (
            partition by ticker order by price_date
        )                                   as prior_close
    from prices
),

-- ── News-driven catalyst events ───────────────────────────────────────────────
news_events as (
    select
        n.ticker,
        n.event_date,
        'news_sentiment_spike'              as event_type,
        n.headline                          as event_description,
        n.final_sentiment_label             as sentiment_direction,
        n.vader_compound                    as sentiment_magnitude,
        n.article_source                    as event_source,
        n.article_id                        as source_id,

        p.close_price,
        p.prior_close,
        p.intraday_return_pct               as price_move_pct,
        p.volume,
        f.vol_avg_20,
        round(p.volume / nullif(f.vol_avg_20, 0), 2)   as volume_spike_ratio,

        round(
            (p.close_3d_forward - p.close_price)
            / nullif(p.close_price, 0) * 100, 2
        )                                   as forward_3d_return_pct

    from news n
    left join price_with_forward p on n.ticker = p.ticker and n.event_date = p.price_date
    left join features           f on n.ticker = f.ticker and n.event_date = f.feature_date
    where abs(n.vader_compound) >= 0.5
),

-- ── Volume spike events ────────────────────────────────────────────────────────
volume_events as (
    select
        p.ticker,
        p.price_date                        as event_date,
        'volume_spike'                      as event_type,
        concat('Volume spike: ',
               cast(round(p.volume / nullif(f.vol_avg_20, 0), 1) as string),
               'x avg')                     as event_description,
        null                                as sentiment_direction,
        null                                as sentiment_magnitude,
        p.data_source                       as event_source,
        null                                as source_id,

        p.close_price,
        p.prior_close,
        p.intraday_return_pct               as price_move_pct,
        p.volume,
        f.vol_avg_20,
        round(p.volume / nullif(f.vol_avg_20, 0), 2)   as volume_spike_ratio,

        round(
            (p.close_3d_forward - p.close_price)
            / nullif(p.close_price, 0) * 100, 2
        )                                   as forward_3d_return_pct

    from price_with_forward p
    left join features f on p.ticker = f.ticker and p.price_date = f.feature_date
    where p.volume > 2 * f.vol_avg_20
      and f.vol_avg_20 > 0
),

-- ── Price gap events ─────────────────────────────────────────────────────────
price_gap_events as (
    select
        p.ticker,
        p.price_date                        as event_date,
        'price_gap'                         as event_type,
        concat('Price gap: ',
               cast(round(p.intraday_return_pct, 2) as string), '%') as event_description,
        case
            when p.intraday_return_pct > 0 then 'positive'
            else 'negative'
        end                                 as sentiment_direction,
        abs(p.intraday_return_pct) / 100    as sentiment_magnitude,
        p.data_source                       as event_source,
        null                                as source_id,

        p.close_price,
        p.prior_close,
        p.intraday_return_pct               as price_move_pct,
        p.volume,
        f.vol_avg_20,
        round(p.volume / nullif(f.vol_avg_20, 0), 2)   as volume_spike_ratio,

        round(
            (p.close_3d_forward - p.close_price)
            / nullif(p.close_price, 0) * 100, 2
        )                                   as forward_3d_return_pct

    from price_with_forward p
    left join features f on p.ticker = f.ticker and p.price_date = f.feature_date
    where abs(p.intraday_return_pct) >= 3.0
),

-- ── RSI extreme events ───────────────────────────────────────────────────────
rsi_events as (
    select
        f.ticker,
        f.feature_date                      as event_date,
        'rsi_extreme'                       as event_type,
        concat('RSI extreme: ', cast(round(f.rsi_14, 1) as string)) as event_description,
        case
            when f.rsi_14 < 30 then 'negative'
            else 'positive'
        end                                 as sentiment_direction,
        abs(f.rsi_14 - 50) / 100           as sentiment_magnitude,
        'technical'                         as event_source,
        null                                as source_id,

        p.close_price,
        p.prior_close,
        p.intraday_return_pct               as price_move_pct,
        p.volume,
        f.vol_avg_20,
        round(p.volume / nullif(f.vol_avg_20, 0), 2)   as volume_spike_ratio,

        round(
            (p.close_3d_forward - p.close_price)
            / nullif(p.close_price, 0) * 100, 2
        )                                   as forward_3d_return_pct

    from features f
    left join price_with_forward p on f.ticker = p.ticker and f.feature_date = p.price_date
    where f.rsi_14 < 30 or f.rsi_14 > 70
),

all_events as (
    select * from news_events
    union all
    select * from volume_events
    union all
    select * from price_gap_events
    union all
    select * from rsi_events
)

select
    ticker,
    event_date,
    event_type,
    event_description,
    sentiment_direction,
    round(cast(sentiment_magnitude as float64), 4)  as sentiment_magnitude,
    event_source,
    source_id,
    close_price,
    prior_close,
    round(cast(price_move_pct as float64), 2)       as price_move_pct,
    volume,
    vol_avg_20,
    volume_spike_ratio,
    forward_3d_return_pct

from all_events
where event_date is not null
order by event_date desc, ticker
