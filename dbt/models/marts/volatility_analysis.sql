{{
  config(
    materialized='table',
    partition_by={
      "field": "price_date",
      "data_type": "date",
      "granularity": "day"
    },
    cluster_by=["ticker"],
    description="Volatility regime analysis per ticker per day — annualized vol, RSI bands, Bollinger Bands, drawdown."
  )
}}

/*
  volatility_analysis
  -------------------
  One row per ticker per trading day.

  Metrics produced
  ────────────────
  • Volatility regime  : normal / elevated / very_high (annualized 20-day)
  • Bollinger Bands    : upper, middle, lower (20-day SMA ± 2σ)
  • RSI bands          : oversold / neutral / overbought
  • Rolling drawdown   : max drawdown from 20-day rolling peak
  • Momentum regime    : strong_up / up / flat / down / strong_down
  • Sector vol rank    : relative volatility within the same sector (window = all dates)
*/

with prices as (
    select
        ticker,
        price_date,
        close_price,
        volume,
        data_source
    from {{ ref('stg_stock_prices') }}
),

features as (
    select
        ticker,
        cast(date as date)                  as feature_date,
        cast(volatility_20d  as float64)    as vol_20d,
        cast(rsi_14          as float64)    as rsi_14,
        cast(momentum_5d     as float64)    as momentum_5d_pct,
        cast(momentum_20d    as float64)    as momentum_20d_pct,
        cast(ma_20           as float64)    as ma_20
    from {{ source('signaldeck_raw', 'processed_features') }}
),

fundamentals as (
    select ticker, sector, beta
    from {{ ref('stg_fundamentals') }}
),

-- Compute rolling 20-day price stats for Bollinger Bands
price_stats as (
    select
        ticker,
        price_date,
        close_price,
        avg(close_price) over (
            partition by ticker
            order by price_date
            rows between 19 preceding and current row
        )                                   as bb_middle,
        stddev(close_price) over (
            partition by ticker
            order by price_date
            rows between 19 preceding and current row
        )                                   as bb_stddev,
        max(close_price) over (
            partition by ticker
            order by price_date
            rows between 19 preceding and current row
        )                                   as rolling_20d_high
    from prices
),

bollinger as (
    select
        ticker,
        price_date,
        close_price,
        bb_middle,
        round(bb_middle + 2 * bb_stddev, 4)     as bb_upper,
        round(bb_middle - 2 * bb_stddev, 4)     as bb_lower,
        rolling_20d_high,

        -- % from 20-day high (drawdown proxy)
        round(
            (close_price - rolling_20d_high) / nullif(rolling_20d_high, 0) * 100, 2
        )                                       as drawdown_from_20d_high_pct,

        -- Bollinger %B: position within bands (0 = lower, 1 = upper)
        round(
            (close_price - (bb_middle - 2 * bb_stddev))
            / nullif(4 * bb_stddev, 0), 4
        )                                       as bollinger_pct_b

    from price_stats
),

joined as (
    select
        b.ticker,
        b.price_date,
        b.close_price,

        -- Bollinger Bands
        b.bb_upper,
        b.bb_middle,
        b.bb_lower,
        b.bollinger_pct_b,
        b.drawdown_from_20d_high_pct,

        -- Volatility from features
        f.vol_20d                                                       as annualized_vol_20d,
        case
            when f.vol_20d < 0.20 then 'normal'
            when f.vol_20d < 0.40 then 'elevated'
            else 'very_high'
        end                                                             as vol_regime,

        -- RSI
        f.rsi_14,
        case
            when f.rsi_14 < 30 then 'oversold'
            when f.rsi_14 > 70 then 'overbought'
            else 'neutral'
        end                                                             as rsi_band,

        -- Momentum regime
        f.momentum_5d_pct,
        f.momentum_20d_pct,
        case
            when f.momentum_20d_pct >  10 then 'strong_up'
            when f.momentum_20d_pct >   2 then 'up'
            when f.momentum_20d_pct <  -10 then 'strong_down'
            when f.momentum_20d_pct <  -2 then 'down'
            else 'flat'
        end                                                             as momentum_regime,

        -- Bollinger squeeze signal (bands narrow = low vol, potential breakout)
        case
            when (b.bb_upper - b.bb_lower) / nullif(b.bb_middle, 0)
                 < 0.05 then true
            else false
        end                                                             as bollinger_squeeze,

        -- Fundamentals
        fu.sector,
        fu.beta

    from bollinger b
    left join features    f  on b.ticker = f.ticker and b.price_date = f.feature_date
    left join fundamentals fu on b.ticker = fu.ticker
),

-- Rank tickers by volatility within their sector on each date
sector_ranked as (
    select
        *,
        rank() over (
            partition by price_date, sector
            order by annualized_vol_20d desc
        )                                                               as sector_vol_rank

    from joined
)

select * from sector_ranked
