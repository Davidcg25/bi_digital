-- Google Search Console (orgánico) → SQL Server. Grano mensual.
-- core = totales por mes; queries = términos orgánicos por mes.
-- queries SIN PK: la colación CI/AI de SQL Server colisiona variantes de query
-- (case/acentos/espacios) que GSC devuelve distintas; delete_existing por
-- (property_name, year_month) evita dups entre runs y el consumo agrupa.

IF OBJECT_ID('dbo.gsc_monthly_core', 'U') IS NULL
BEGIN
    CREATE TABLE dbo.gsc_monthly_core (
        property_name VARCHAR(100) NOT NULL,
        site_url      VARCHAR(200) NOT NULL,
        year_month    CHAR(6) NOT NULL,
        clicks        INT NULL,
        impressions   INT NULL,
        ctr           FLOAT NULL,
        position      FLOAT NULL,
        extracted_at  DATETIME2 NOT NULL DEFAULT SYSDATETIME(),
        CONSTRAINT pk_gsc_monthly_core PRIMARY KEY (property_name, year_month)
    );
END
GO

IF OBJECT_ID('dbo.gsc_monthly_queries', 'U') IS NULL
BEGIN
    CREATE TABLE dbo.gsc_monthly_queries (
        property_name VARCHAR(100) NOT NULL,
        year_month    CHAR(6) NOT NULL,
        query         NVARCHAR(500) NOT NULL,
        clicks        INT NULL,
        impressions   INT NULL,
        ctr           FLOAT NULL,
        position      FLOAT NULL,
        extracted_at  DATETIME2 NOT NULL DEFAULT SYSDATETIME()
    );
    CREATE INDEX ix_gsc_monthly_queries_pid_ym ON dbo.gsc_monthly_queries(property_name, year_month);
END
GO

-- Grano SEMANAL (rolling): mismo esquema pero clave week_start (lunes ISO).
-- Para monitoreo intra-mes de ranking/CTR; corre en cron semanal.
IF OBJECT_ID('dbo.gsc_weekly_core', 'U') IS NULL
BEGIN
    CREATE TABLE dbo.gsc_weekly_core (
        property_name VARCHAR(100) NOT NULL,
        site_url      VARCHAR(200) NOT NULL,
        week_start    DATE NOT NULL,
        clicks        INT NULL,
        impressions   INT NULL,
        ctr           FLOAT NULL,
        position      FLOAT NULL,
        extracted_at  DATETIME2 NOT NULL DEFAULT SYSDATETIME(),
        CONSTRAINT pk_gsc_weekly_core PRIMARY KEY (property_name, week_start)
    );
END
GO

IF OBJECT_ID('dbo.gsc_weekly_queries', 'U') IS NULL
BEGIN
    CREATE TABLE dbo.gsc_weekly_queries (
        property_name VARCHAR(100) NOT NULL,
        week_start    DATE NOT NULL,
        query         NVARCHAR(500) NOT NULL,
        clicks        INT NULL,
        impressions   INT NULL,
        ctr           FLOAT NULL,
        position      FLOAT NULL,
        extracted_at  DATETIME2 NOT NULL DEFAULT SYSDATETIME()
    );
    CREATE INDEX ix_gsc_weekly_queries_pid_wk ON dbo.gsc_weekly_queries(property_name, week_start);
END
GO
