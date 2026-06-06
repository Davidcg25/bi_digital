USE [Digital_Impact_Reportes];
GO

IF OBJECT_ID('dbo.clarity_live_insights', 'U') IS NULL
BEGIN
    CREATE TABLE dbo.clarity_live_insights (
        id BIGINT IDENTITY(1,1) NOT NULL PRIMARY KEY,
        run_id INT NOT NULL,
        project_name NVARCHAR(150) NOT NULL,
        token_name NVARCHAR(150) NULL,
        report_name NVARCHAR(100) NOT NULL,
        num_of_days TINYINT NOT NULL,
        metric_name NVARCHAR(150) NOT NULL,
        dimension1_name NVARCHAR(100) NULL,
        dimension1_value NVARCHAR(500) NULL,
        dimension2_name NVARCHAR(100) NULL,
        dimension2_value NVARCHAR(500) NULL,
        dimension3_name NVARCHAR(100) NULL,
        dimension3_value NVARCHAR(500) NULL,
        measures_json NVARCHAR(MAX) NULL,
        row_json NVARCHAR(MAX) NULL,
        row_hash CHAR(64) NOT NULL,
        extraction_date_utc DATE NOT NULL,
        extracted_at DATETIME2 NOT NULL DEFAULT SYSDATETIME(),
        window_start_utc DATETIME2 NULL,
        window_end_utc DATETIME2 NULL
    );

    CREATE UNIQUE INDEX ux_clarity_live_insights_row
        ON dbo.clarity_live_insights(project_name, report_name, num_of_days, metric_name, row_hash, extraction_date_utc);

    CREATE INDEX ix_clarity_live_insights_run_project
        ON dbo.clarity_live_insights(run_id, project_name);

    CREATE INDEX ix_clarity_live_insights_metric
        ON dbo.clarity_live_insights(metric_name, report_name);
END
GO

IF OBJECT_ID('dbo.clarity_projects', 'U') IS NULL
BEGIN
    CREATE TABLE dbo.clarity_projects (
        project_name NVARCHAR(150) NOT NULL PRIMARY KEY,
        token_name NVARCHAR(150) NULL,
        is_active BIT NOT NULL DEFAULT 1,
        updated_at DATETIME2 NOT NULL DEFAULT SYSDATETIME()
    );
END
GO

IF OBJECT_ID('dbo.clarity_etl_runs', 'U') IS NULL
BEGIN
    CREATE TABLE dbo.clarity_etl_runs (
        run_id INT IDENTITY(1,1) NOT NULL PRIMARY KEY,
        started_at DATETIME2 NOT NULL,
        finished_at DATETIME2 NULL,
        num_of_days TINYINT NOT NULL,
        status NVARCHAR(30) NOT NULL,
        total_projects INT NOT NULL,
        successful_projects INT NOT NULL DEFAULT 0,
        failed_projects INT NOT NULL DEFAULT 0,
        error_message NVARCHAR(MAX) NULL
    );
END
GO

IF OBJECT_ID('dbo.clarity_etl_report_loads', 'U') IS NULL
BEGIN
    CREATE TABLE dbo.clarity_etl_report_loads (
        id INT IDENTITY(1,1) NOT NULL PRIMARY KEY,
        run_id INT NOT NULL,
        project_name NVARCHAR(150) NOT NULL,
        report_name NVARCHAR(100) NOT NULL,
        rows_loaded INT NOT NULL,
        status NVARCHAR(30) NOT NULL,
        error_message NVARCHAR(MAX) NULL,
        loaded_at DATETIME2 NOT NULL DEFAULT SYSDATETIME()
    );
END
GO
