-- Summary queries for the forecast report and dashboard. Each block starts with "-- name:".
-- Tables: opps (opportunities with segment), history (stage history), users.
-- Dates are stored as 'YYYY-MM-DD' text, so substr() and date() work on them directly.

-- name: bookings_by_quarter
-- Won dollars and win rate per quarter, against the quota of the AEs employed at quarter end.
WITH q AS (
  SELECT opportunity_id, amount, is_won,
         substr(close_date, 1, 4) || '-Q' || ((CAST(substr(close_date, 6, 2) AS INT) + 2) / 3) AS quarter
  FROM opps WHERE is_closed = 1
)
SELECT quarter,
       COUNT(*) AS closed_deals,
       SUM(is_won) AS won_deals,
       ROUND(1.0 * SUM(is_won) / COUNT(*), 3) AS win_rate,
       SUM(CASE WHEN is_won = 1 THEN amount ELSE 0 END) AS won_amount
FROM q
GROUP BY quarter
ORDER BY quarter;

-- name: win_rate_by_segment
SELECT segment, type,
       COUNT(*) AS closed_deals,
       ROUND(1.0 * SUM(is_won) / COUNT(*), 3) AS win_rate,
       ROUND(AVG(CASE WHEN is_won = 1 THEN julianday(close_date) - julianday(created_date) END), 0) AS avg_days_to_win
FROM opps
WHERE is_closed = 1
GROUP BY segment, type
ORDER BY segment, type;

-- name: stage_funnel
-- Of closed deals that reached each stage, how many reached the next one and how many won.
WITH reached AS (
  SELECT DISTINCT h.opportunity_id, h.stage, o.is_won
  FROM history h JOIN opps o ON o.opportunity_id = h.opportunity_id
  WHERE o.is_closed = 1
),
counts AS (
  SELECT stage, COUNT(*) AS deals, SUM(is_won) AS won
  FROM reached GROUP BY stage
)
SELECT stage, deals, won, ROUND(1.0 * won / deals, 3) AS win_rate_from_here
FROM counts
WHERE stage NOT IN ('Closed Won', 'Closed Lost')
ORDER BY CASE stage WHEN 'Prospecting' THEN 1 WHEN 'Discovery' THEN 2 WHEN 'Proposal Sent' THEN 3
                    WHEN 'Negotiation' THEN 4 ELSE 5 END;

-- name: slippage
-- Deals whose close date moved past the first forecast, and how they turned out.
SELECT CASE WHEN close_date > original_close_date THEN 'Pushed' ELSE 'On time' END AS timing,
       COUNT(*) AS closed_deals,
       ROUND(1.0 * SUM(is_won) / COUNT(*), 3) AS win_rate,
       ROUND(AVG(CASE WHEN close_date > original_close_date
                      THEN julianday(close_date) - julianday(original_close_date) END), 0) AS avg_days_pushed
FROM opps
WHERE is_closed = 1
GROUP BY timing;

-- name: created_by_month
-- New pipeline created each month, by segment.
SELECT substr(created_date, 1, 7) AS month, segment, COUNT(*) AS deals, SUM(amount) AS amount
FROM opps
GROUP BY month, segment
ORDER BY month, segment;

-- name: open_by_stage
-- Today's open pipeline by stage, split into dated this quarter, later, and already past due.
SELECT stage,
       COUNT(*) AS deals,
       SUM(amount) AS amount,
       SUM(CASE WHEN close_date < :q_start THEN amount ELSE 0 END) AS past_due,
       SUM(CASE WHEN close_date BETWEEN :q_start AND :q_end THEN amount ELSE 0 END) AS this_quarter,
       SUM(CASE WHEN close_date > :q_end THEN amount ELSE 0 END) AS later
FROM opps
WHERE is_closed = 0
GROUP BY stage
ORDER BY CASE stage WHEN 'Prospecting' THEN 1 WHEN 'Discovery' THEN 2 WHEN 'Proposal Sent' THEN 3
                    WHEN 'Negotiation' THEN 4 ELSE 5 END;

-- name: lead_conversion
-- Leads created each quarter and the share that converted.
SELECT substr(created_date, 1, 4) || '-Q' || ((CAST(substr(created_date, 6, 2) AS INT) + 2) / 3) AS quarter,
       COUNT(*) AS leads,
       SUM(CASE WHEN status = 'Converted' THEN 1 ELSE 0 END) AS converted,
       ROUND(1.0 * SUM(CASE WHEN status = 'Converted' THEN 1 ELSE 0 END) / COUNT(*), 3) AS conversion
FROM leads
GROUP BY quarter
ORDER BY quarter;

-- name: pipeline_conversion
-- Pipeline created each quarter and what has become of it so far.
-- Recent quarters are still mostly open, so only closed dollars give a fair win rate.
SELECT substr(created_date, 1, 4) || '-Q' || ((CAST(substr(created_date, 6, 2) AS INT) + 2) / 3) AS quarter,
       SUM(amount) AS created,
       SUM(CASE WHEN is_won = 1 THEN amount ELSE 0 END) AS won,
       SUM(CASE WHEN is_closed = 1 AND is_won = 0 THEN amount ELSE 0 END) AS lost,
       SUM(CASE WHEN is_closed = 0 THEN amount ELSE 0 END) AS still_open,
       ROUND(1.0 * SUM(CASE WHEN is_won = 1 THEN amount ELSE 0 END)
             / NULLIF(SUM(CASE WHEN is_closed = 1 THEN amount ELSE 0 END), 0), 3) AS dollar_win_rate
FROM opps
GROUP BY quarter
ORDER BY quarter;

-- name: win_rate_by_segment_quarter
SELECT substr(close_date, 1, 4) || '-Q' || ((CAST(substr(close_date, 6, 2) AS INT) + 2) / 3) AS quarter,
       segment,
       COUNT(*) AS closed_deals,
       ROUND(1.0 * SUM(is_won) / COUNT(*), 3) AS win_rate
FROM opps
WHERE is_closed = 1
GROUP BY quarter, segment
ORDER BY quarter, segment;

-- name: rep_scorecard
-- Each AE over the last four full quarters: deals closed, win rate, bookings and deal size.
SELECT o.owner_id,
       u.name,
       u.segment,
       u.is_active,
       COUNT(*) AS closed_deals,
       ROUND(1.0 * SUM(o.is_won) / COUNT(*), 3) AS win_rate,
       SUM(CASE WHEN o.is_won = 1 THEN o.amount ELSE 0 END) AS bookings,
       ROUND(AVG(CASE WHEN o.is_won = 1 THEN o.amount END), 0) AS avg_won_deal,
       ROUND(AVG(CASE WHEN o.is_won = 1 THEN julianday(o.close_date) - julianday(o.created_date) END), 0) AS avg_days_to_win
FROM opps o
JOIN users u ON u.user_id = o.owner_id
WHERE o.is_closed = 1 AND o.close_date >= :year_start AND o.close_date < :q_start
  AND u.role = 'Account Executive'
GROUP BY o.owner_id
ORDER BY win_rate DESC;
