-- ============================================================
-- SNOWFLAKE DATA VALIDATION QUERIES
-- FabCon Global Hack 2025 - Snowflake to Fabric Migration
-- ============================================================

-- Set context
USE DATABASE ENTERPRISE_FINANCE;
USE SCHEMA FINANCE_DW;

-- ============================================================
-- 1. TABLE OVERVIEW
-- ============================================================
SELECT 
    TABLE_NAME,
    ROW_COUNT,
    ROUND(BYTES / (1024*1024), 2) as SIZE_MB,
    CREATED
FROM INFORMATION_SCHEMA.TABLES
WHERE TABLE_SCHEMA = 'FINANCE_DW'
ORDER BY ROW_COUNT DESC;


-- ============================================================
-- 2. DATA QUALITY CHECKS
-- ============================================================

-- Check for NULL values in key fields
SELECT 
    'GL_TRANSACTIONS' as TABLE_NAME,
    COUNT(*) as TOTAL_ROWS,
    SUM(CASE WHEN TRANSACTION_ID IS NULL THEN 1 ELSE 0 END) as NULL_IDS,
    SUM(CASE WHEN DEBIT_AMOUNT = 0 AND CREDIT_AMOUNT = 0 THEN 1 ELSE 0 END) as ZERO_AMOUNT_ROWS
FROM GL_TRANSACTIONS;

-- Verify date ranges
SELECT 
    MIN(TRANSACTION_DATE) as MIN_DATE,
    MAX(TRANSACTION_DATE) as MAX_DATE,
    DATEDIFF(day, MIN(TRANSACTION_DATE), MAX(TRANSACTION_DATE)) as DATE_RANGE_DAYS
FROM GL_TRANSACTIONS;


-- ============================================================
-- 3. BUSINESS LOGIC VALIDATION
-- ============================================================

-- GL Transactions: Verify debit/credit balance
SELECT 
    FISCAL_YEAR,
    FISCAL_PERIOD,
    ROUND(SUM(DEBIT_AMOUNT), 2) as TOTAL_DEBITS,
    ROUND(SUM(CREDIT_AMOUNT), 2) as TOTAL_CREDITS,
    ROUND(SUM(DEBIT_AMOUNT) - SUM(CREDIT_AMOUNT), 2) as NET_DIFFERENCE
FROM GL_TRANSACTIONS
GROUP BY FISCAL_YEAR, FISCAL_PERIOD
ORDER BY FISCAL_YEAR DESC, FISCAL_PERIOD DESC
LIMIT 12;

-- Budget Variance Distribution
SELECT 
    CASE 
        WHEN VARIANCE_PERCENT < -20 THEN 'Over Budget >20%'
        WHEN VARIANCE_PERCENT < -10 THEN 'Over Budget 10-20%'
        WHEN VARIANCE_PERCENT < 10 THEN 'On Target'
        WHEN VARIANCE_PERCENT < 20 THEN 'Under Budget 10-20%'
        ELSE 'Under Budget >20%'
    END as VARIANCE_CATEGORY,
    COUNT(*) as COUNT,
    ROUND(AVG(VARIANCE_PERCENT), 2) as AVG_VARIANCE_PCT
FROM BUDGET_ACTUAL
GROUP BY 1
ORDER BY 3 DESC;

-- Invoice Status Distribution
SELECT 
    STATUS,
    COUNT(*) as INVOICE_COUNT,
    ROUND(SUM(INVOICE_AMOUNT) / 1000000, 2) as TOTAL_AMOUNT_M,
    ROUND(SUM(OUTSTANDING_AMOUNT) / 1000000, 2) as OUTSTANDING_M,
    ROUND(AVG(INVOICE_AMOUNT), 2) as AVG_INVOICE
FROM INVOICES
GROUP BY STATUS
ORDER BY INVOICE_COUNT DESC;


-- ============================================================
-- 4. REFERENTIAL INTEGRITY CHECKS
-- ============================================================

-- Check orphaned records (invoices without valid vendors)
SELECT COUNT(*) as ORPHANED_INVOICES
FROM INVOICES i
LEFT JOIN VENDORS v ON i.VENDOR_ID = v.VENDOR_ID
WHERE v.VENDOR_ID IS NULL;

-- Check cost center references
SELECT 
    'GL_TRANSACTIONS' as SOURCE_TABLE,
    COUNT(DISTINCT g.COST_CENTER) as UNIQUE_COST_CENTERS,
    COUNT(DISTINCT c.COST_CENTER) as VALID_COST_CENTERS,
    COUNT(DISTINCT g.COST_CENTER) - COUNT(DISTINCT c.COST_CENTER) as MISSING_COST_CENTERS
FROM GL_TRANSACTIONS g
LEFT JOIN COST_CENTERS c ON g.COST_CENTER = c.COST_CENTER;


-- ============================================================
-- 5. SAMPLE ANALYTICAL QUERIES (For Performance Testing)
-- ============================================================

-- Query 1: Monthly P&L Summary
SELECT 
    t.FISCAL_YEAR,
    t.FISCAL_PERIOD,
    c.ACCOUNT_TYPE,
    COUNT(*) as TRANSACTION_COUNT,
    ROUND(SUM(t.DEBIT_AMOUNT - t.CREDIT_AMOUNT) / 1000000, 2) as NET_AMOUNT_M
FROM GL_TRANSACTIONS t
JOIN CHART_OF_ACCOUNTS c ON t.ACCOUNT_NUMBER = c.ACCOUNT_NUMBER
WHERE t.FISCAL_YEAR >= 2023
GROUP BY 1, 2, 3
ORDER BY 1 DESC, 2 DESC, 3;

-- Query 2: Top Cost Centers by Budget Variance
SELECT 
    cc.COST_CENTER,
    cc.COST_CENTER_NAME,
    cc.BUSINESS_UNIT,
    ROUND(SUM(ba.BUDGET_AMOUNT) / 1000000, 2) as TOTAL_BUDGET_M,
    ROUND(SUM(ba.ACTUAL_AMOUNT) / 1000000, 2) as TOTAL_ACTUAL_M,
    ROUND(SUM(ba.VARIANCE_AMOUNT) / 1000000, 2) as TOTAL_VARIANCE_M,
    ROUND(AVG(ba.VARIANCE_PERCENT), 2) as AVG_VARIANCE_PCT
FROM BUDGET_ACTUAL ba
JOIN COST_CENTERS cc ON ba.COST_CENTER = cc.COST_CENTER
WHERE ba.FISCAL_YEAR = 2024
GROUP BY 1, 2, 3
ORDER BY 7 DESC
LIMIT 20;

-- Query 3: Vendor Payment Analysis
SELECT 
    v.VENDOR_NAME,
    v.VENDOR_TYPE,
    v.PAYMENT_TERMS,
    COUNT(i.INVOICE_ID) as INVOICE_COUNT,
    ROUND(SUM(i.INVOICE_AMOUNT) / 1000, 2) as TOTAL_SPEND_K,
    ROUND(AVG(DATEDIFF(day, i.INVOICE_DATE, i.PAYMENT_DATE)), 1) as AVG_PAYMENT_DAYS,
    ROUND(SUM(i.OUTSTANDING_AMOUNT) / 1000, 2) as OUTSTANDING_K
FROM VENDORS v
JOIN INVOICES i ON v.VENDOR_ID = i.VENDOR_ID
WHERE i.STATUS IN ('Paid', 'Overdue')
GROUP BY 1, 2, 3
ORDER BY 5 DESC
LIMIT 25;

-- Query 4: Cash Flow Projection
SELECT 
    TO_CHAR(DUE_DATE, 'YYYY-MM') as DUE_MONTH,
    COUNT(*) as INVOICE_COUNT,
    ROUND(SUM(CASE WHEN STATUS = 'Open' THEN OUTSTANDING_AMOUNT ELSE 0 END) / 1000000, 2) as PAYABLES_DUE_M
FROM INVOICES
WHERE DUE_DATE BETWEEN CURRENT_DATE AND DATEADD(month, 6, CURRENT_DATE)
GROUP BY 1
ORDER BY 1;


-- ============================================================
-- 6. EXPORT METADATA (For Migration Documentation)
-- ============================================================

-- Table and column metadata
SELECT 
    TABLE_NAME,
    COLUMN_NAME,
    ORDINAL_POSITION,
    DATA_TYPE,
    IS_NULLABLE,
    CHARACTER_MAXIMUM_LENGTH
FROM INFORMATION_SCHEMA.COLUMNS
WHERE TABLE_SCHEMA = 'FINANCE_DW'
ORDER BY TABLE_NAME, ORDINAL_POSITION;

-- Storage and clustering information
SELECT 
    TABLE_NAME,
    ROW_COUNT,
    BYTES,
    RETENTION_TIME,
    AUTOMATIC_CLUSTERING,
    CLUSTERING_KEY
FROM INFORMATION_SCHEMA.TABLES
WHERE TABLE_SCHEMA = 'FINANCE_DW';


-- ============================================================
-- NOTES FOR JUDGES
-- ============================================================
-- 
-- These queries demonstrate:
-- 1. Data quality and completeness
-- 2. Referential integrity
-- 3. Business logic validation
-- 4. Analytical query patterns typical of enterprise finance
-- 5. Metadata required for migration planning
--
-- All queries are optimized for Snowflake and will be benchmarked
-- against equivalent queries in Microsoft Fabric post-migration.
--
-- ============================================================
