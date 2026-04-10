{{
  config(
    materialized='view',
    description='Cleaned fundamental data snapshot per ticker — latest record only.'
  )
}}

with source as (
    select * from {{ source('signaldeck_raw', 'fundamentals') }}
),

ranked as (
    select
        *,
        row_number() over (
            partition by ticker
            order by cast(ingested_at as timestamp) desc
        ) as rn
    from source
    where ticker is not null
),

latest as (
    select
        ticker,
        cast(date as date)                          as snapshot_date,
        cast(pe_ratio      as float64)              as pe_ratio,
        cast(forward_pe    as float64)              as forward_pe,
        cast(eps           as float64)              as eps,
        cast(revenue       as float64)              as revenue,
        cast(profit_margin as float64)              as profit_margin_pct,
        cast(debt_to_equity as float64)             as debt_to_equity,
        cast(roe           as float64)              as return_on_equity,
        cast(beta          as float64)              as beta,
        cast(dividend_yield as float64)             as dividend_yield_pct,
        cast(analyst_target as float64)             as analyst_target_price,
        cast(market_cap    as float64)              as market_cap,
        sector,
        industry,
        cast(`52w_high`    as float64)              as week52_high,
        cast(`52w_low`     as float64)              as week52_low,
        cast(ingested_at   as timestamp)            as ingested_at

    from ranked
    where rn = 1
)

select * from latest
