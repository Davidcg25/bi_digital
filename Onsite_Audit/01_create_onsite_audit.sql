-- Auditoria on-site del flujo de checkout (scraper Playwright, emulacion mobile).
-- Llena el hueco que GA4 (dice DONDE se cae: begin_checkout->shipping en mobile)
-- y Clarity (no captura el checkout SPA de Magento) dejan: el POR QUE del formulario.
-- 1 fila por web x corrida x device. Idempotente.

IF OBJECT_ID('dbo.onsite_audit_checkout', 'U') IS NULL
BEGIN
    CREATE TABLE dbo.onsite_audit_checkout (
        run_id            INT            NOT NULL,
        property_name     VARCHAR(100)   NOT NULL,
        dominio           VARCHAR(150)   NULL,
        device            VARCHAR(20)    NOT NULL,   -- mobile / desktop
        fecha             DATE           NOT NULL,
        -- recorrido
        pdp_url           NVARCHAR(1000) NULL,
        reached_pdp       BIT            NULL,
        reached_cart      BIT            NULL,
        reached_checkout  BIT            NULL,
        -- hipotesis #1: muro de login
        guest_checkout    BIT            NULL,   -- 1=permite comprar sin cuenta
        login_wall        BIT            NULL,   -- 1=exige login antes de la direccion
        -- formulario de direccion
        form_fields       INT            NULL,   -- nº de inputs/selects visibles
        form_required     INT            NULL,   -- nº de campos obligatorios
        autocomplete_pct  DECIMAL(5,1)   NULL,   -- % de campos con atributo autocomplete
        cascading_selects BIT            NULL,   -- depto/provincia/distrito como selects
        -- envio / pago (el leak universal shipping->payment)
        shipping_cost_shown BIT          NULL,   -- costo de envio visible antes de pagar
        shipping_eta_shown  BIT          NULL,   -- fecha/plazo de entrega visible
        free_ship_threshold BIT          NULL,   -- mensaje de envio gratis desde S/X
        payment_methods   INT            NULL,
        -- salud tecnica del flujo (engancha con bug JS Caterpillar/Coliseum)
        console_errors    INT            NULL,
        checkout_lcp_ms   INT            NULL,
        -- veredicto
        rubric_score      INT            NULL,   -- 0-100 compuesto
        flags             NVARCHAR(2000) NULL,   -- lista de problemas detectados
        status            VARCHAR(30)    NOT NULL,-- ok / partial / error
        error_message     NVARCHAR(2000) NULL,
        extracted_at      DATETIME2      NOT NULL DEFAULT SYSDATETIME(),
        CONSTRAINT pk_onsite_audit_checkout PRIMARY KEY (run_id, property_name, device)
    );
    CREATE INDEX ix_onsite_audit_web_fecha ON dbo.onsite_audit_checkout(property_name, fecha);
END
GO
