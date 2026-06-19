-- Amplia el auditor: texto del costo/ETA de envio (que aparece RECIEN tras
-- completar el triple cascada depto/prov/distrito) + tabla de errores JS con detalle.
-- Idempotente.

IF COL_LENGTH('dbo.onsite_audit_checkout', 'shipping_cost_text') IS NULL
    ALTER TABLE dbo.onsite_audit_checkout ADD shipping_cost_text NVARCHAR(300) NULL;
GO
IF COL_LENGTH('dbo.onsite_audit_checkout', 'shipping_eta_text') IS NULL
    ALTER TABLE dbo.onsite_audit_checkout ADD shipping_eta_text NVARCHAR(300) NULL;
GO
IF COL_LENGTH('dbo.onsite_audit_checkout', 'address_filled') IS NULL
    ALTER TABLE dbo.onsite_audit_checkout ADD address_filled BIT NULL;
GO
IF COL_LENGTH('dbo.onsite_audit_checkout', 'cost_gated_behind_cascade') IS NULL
    ALTER TABLE dbo.onsite_audit_checkout ADD cost_gated_behind_cascade BIT NULL;
GO
IF COL_LENGTH('dbo.onsite_audit_checkout', 'shots') IS NULL
    ALTER TABLE dbo.onsite_audit_checkout ADD shots INT NULL;
GO

-- Detalle de errores JS capturados durante el recorrido (1 fila por error).
IF OBJECT_ID('dbo.onsite_audit_console', 'U') IS NULL
BEGIN
    CREATE TABLE dbo.onsite_audit_console (
        run_id        INT            NOT NULL,
        property_name VARCHAR(100)   NOT NULL,
        device        VARCHAR(20)    NOT NULL,
        seq           INT            NOT NULL,
        err_type      VARCHAR(20)    NULL,   -- pageerror / console
        err_text      NVARCHAR(1000) NULL,
        err_location  NVARCHAR(600)  NULL,
        stage         VARCHAR(40)    NULL,   -- en que paso del viaje aparecio
        extracted_at  DATETIME2      NOT NULL DEFAULT SYSDATETIME(),
        CONSTRAINT pk_onsite_audit_console PRIMARY KEY (run_id, property_name, device, seq)
    );
    CREATE INDEX ix_onsite_console_web ON dbo.onsite_audit_console(property_name, err_type);
END
GO
