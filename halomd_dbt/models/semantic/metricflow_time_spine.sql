-- Calendario diario que MetricFlow necesita para agregar métricas por día/mes
{{ config(materialized='table') }}
{{ dbt.date_spine(
    datepart="day",
    start_date="cast('2025-01-01' as date)",
    end_date="cast('2027-12-31' as date)"
) }}
