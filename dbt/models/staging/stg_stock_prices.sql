{{
  config(
    materialized='view',
    description='Cleaned and typed stock price records from the raw ingestion table.'
  )
}}

with source as (
    select * from {{ source('signaldeck_raw', 'stock_prices') }}
),

cleaned as (
    select
        ticker,
        cast(date as date)                          as price_date,
        cast(open  as float64)                      as open_price,
        cast(high  as float64)                      as high_price,
        cast(low   as float64)                      as low_price,
        cast(close as float64)                      as close_price,
        cast(volume as int64)                       as volume,
        source                                      as data_source,

        -- derived helpers
        round(cast(high as float64) - cast(low as float64), 4)  as daily_range,
        round(
            (cast(close as float64) - cast(open as float64))
            / nullif(cast(open as float64), 0) * 100, 4
        )                                           as intraday_return_pct

    from source
    where ticker is not null
      and date  is not null
      and close is not null
      and cast(close as float64) > 0
)

select * from cleaned
