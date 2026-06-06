USE [Digital_Impact_Reportes];
GO
/* =========================================================
   dim_tenant — crosswalk web (GA4 property) <-> Clarity project
   ---------------------------------------------------------
   GRANO: 1 fila por SITIO WEB (no por marca).
   - Coliseum es multimarca: contiene las 8 marcas del grupo.
   - Las webs propias (newbalance.com.pe, etc.) son tenants aparte.
   - NUNCA sumar una marca cruzando webs (doble conteo): una marca
     vive a la vez dentro de Coliseum y en su sitio propio.
   - Unico arreglo de etiqueta: Clarity 'UmbroPE' = GA4 'Umbro'.
   La normalizacion de URL NO depende de 'dominio' (se hace generica);
   'dominio' queda solo como referencia.
   ========================================================= */
IF OBJECT_ID('dbo.dim_tenant','U') IS NOT NULL DROP TABLE dbo.dim_tenant;
GO
CREATE TABLE dbo.dim_tenant (
    property_id      VARCHAR(30)   NOT NULL PRIMARY KEY,  -- ga4_*.property_id
    property_name    VARCHAR(100)  NOT NULL,              -- ga4_*.property_name
    clarity_project  NVARCHAR(150) NULL,                  -- clarity_live_insights.project_name
    marca            VARCHAR(100)  NOT NULL,
    dominio          VARCHAR(150)  NULL,                  -- referencia
    is_multimarca    BIT NOT NULL DEFAULT 0,
    is_active        BIT NOT NULL DEFAULT 1,
    notas            NVARCHAR(300) NULL
);
GO
INSERT INTO dbo.dim_tenant
 (property_id, property_name, clarity_project, marca, dominio, is_multimarca, is_active, notas) VALUES
 ('338208380','Caterpillar','Caterpillar','Caterpillar','catlifestyle.pe',0,1,NULL),
 ('287142051','Coliseum','Coliseum','(multimarca)',NULL,1,1,'Tienda multimarca: contiene las 8 marcas del grupo'),
 ('407838284','Converse','Converse','Converse','converse.com.pe',0,1,NULL),
 ('304627263','Merrell','Merrell','Merrell',NULL,0,1,'dominio por confirmar'),
 ('427321367','New Balance','New Balance','New Balance','newbalance.com.pe',0,1,NULL),
 ('293692998','Steve Madden','Steve Madden','Steve Madden',NULL,0,1,'dominio por confirmar'),
 ('495902890','Umbro','UmbroPE','Umbro','umbro.com.pe',0,1,'Clarity usa UmbroPE; GA4 usa Umbro'),
 ('513757079','Fila',NULL,'Fila',NULL,0,0,'Inactiva: GA4 sin trafico relevante, sin proyecto Clarity');
GO
