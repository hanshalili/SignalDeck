{{
  config(
    materialized='table',
    partition_by={
      "field": "price_date",
      "data_type": "date",
      "granularity": "day"
    },
    cluster_by=["ticker"],
    description="Daily company performance metrics combining price, features, LLM signal, and fundamentals."
  )
}}

/*
  company_performance
  -------------------
  One row per ticker per trading day.

  Metrics produced
  ────────────────
  • Price action      : open, high, low, close, volume, intraday range, daily return %
  • Moving averages   : MA-5, MA-20, MA-50 (from processed_features)
  • Momentum          : 5-day and 20-day momentum %
  • Signal            : LLM recommendation + agent action + confidence
  • Valuation         : P/E, forward P/E, EPS, market cap, analyst target vs close spread
*/

with prices as (
    select * from {{ ref('stg_stock_prices') }}
),

features as (
    select
        ticker,
        cast(date as date)              as feature_date,
        cast(close as float64)          as feat_close,
        cast(daily_return as float64)   as daily_return_pct,
        cast(momentum_5d  as float64)   as momentum_5d_pct,
        cast(momentum_20d as float64)   as momentum_20d_pct,
        cast(ma_5  as float64)          as ma_5,
        cast(ma_20 as float64)          as ma_20,
        cast(ma_50 as float64)          as ma_50,
        cast(rsi_14 as float64)         as rsi_14,
        cast(volatility_20d as float64) as volatility_20d
    from {{ source('signaldeck_raw', 'processed_features') }}
),

signals as (
    select
        ticker,
        cast(date as date)              as signal_date,
        recommendation,
        confidence,
        cast(price_target as float64)   as price_target,
        model_used
    from {{ source('signaldeck_raw', 'llm_analysis') }}
    qualify row_number() over (partition by ticker, cast(date as date) order by analyzed_at desc) = 1
),

agent as (
    select
        ticker,
        cast(date as date)              as agent_date,
        action                          as agent_action,
        cast(confidence_score as float64) as agent_confidence,
        time_horizon
    from {{ source('signaldeck_raw', 'agent_recommendations') }}
    qualify row_number() over (partition by ticker, cast(date as date) order by recommended_at desc) = 1
),

fundamentals as (
    select * from {{ ref('stg_fundamentals') }}
),

joined as (
    select
        p.ticker,
        p.price_date,

        -- Price action
        p.open_price,
        p.high_price,
        p.low_price,
        p.close_price,
        p.volume,
        p.daily_range,
        p.intraday_return_pct,
        p.data_source,

        -- Engineered features
        f.daily_return_pct,
        f.momentum_5d_pct,
        f.momentum_20d_pct,
        f.ma_5,
        f.ma_20,
        f.ma_50,
        f.rsi_14,
        f.volatility_20d,

        -- Moving average crossover flags
        case when f.ma_5 > f.ma_20 then true else false end   as ma5_above_ma20,
        case when f.ma_20 > f.ma_50 then true else false end  as ma20_above_ma50,

        -- RSI regime
        case
            when f.rsi_14 < 30 then 'oversold'
            when f.rsi_14 > 70 then 'overbought'
            else 'neutral'
        end                                                   as rsi_regime,

        -- LLM signal
        s.recommendation    as llm_recommendation,
        s.confidence        as llm_confidence,
        s.price_target      as llm_price_target,
        s.model_used        as llm_model,

        -- Analyst target spread
        round(
            (s.price_target - p.close_price) / nullif(p.close_price, 0) * 100, 2
        )                                                     as analyst_upside_pct,

        -- Agent signal
        a.agent_action,
        a.agent_confidence,
        a.time_horizon,

        -- Fundamentals
        fu.pe_ratio,
        fu.forward_pe,
        fu.eps,
        fu.market_cap,
        fu.profit_margin_pct,
        fu.beta,
        fu.dividend_yield_pct,
        fu.sector,
        fu.industry

    from prices p
    left join features   f  on p.ticker = f.ticker and p.price_date = f.feature_date
    left join signals    s  on p.ticker = s.ticker and p.price_date = s.signal_date
    left join agent      a  on p.ticker = a.ticker and p.price_date = a.agent_date
    left join fundamentals fu on p.ticker = fu.ticker
)

select * from joined
