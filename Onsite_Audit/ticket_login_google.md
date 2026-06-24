# Login con Google (Sign in with Google) — acceso a cuenta, estado de pedido e historial

**Tipo:** Mejora / Feature
**Prioridad:** Media
**Alcance:** Cuenta de cliente (theme + backend) → 7 webs Solidez (Magento)

---

## Objetivo y valor

Permitir que el cliente **cree cuenta e inicie sesión con su cuenta de Google en un toque**, con el foco en:

- Acceder fácilmente al **estado de su pedido y al historial de compras** en "Mi cuenta", **sin tener que recordar una contraseña**.
- Bajar la barrera de registro → más clientes registrados, más recompra, y **menos consultas de "¿dónde está mi pedido?"** al soporte (autoservicio).
- Capturar identidad/contacto verificado del cliente para CRM/postventa.

> Nota de alcance: este ticket **no** resuelve el autofill de la dirección en el checkout (ese es un ticket separado). Google login da identidad y acceso a la cuenta, no la dirección de envío peruana.

---

## Alcance funcional

- Botón **"Continuar con Google"** en: página de login/registro, "Mi cuenta", y el flujo de **seguimiento de pedido**. (Opcional también en el checkout.)
- Tras autenticar, el usuario aterriza en su cuenta con **"Mis pedidos" / historial y estado** visibles.
- El login clásico (email + contraseña) **sigue funcionando** en paralelo.

---

## Requisitos técnicos

- Usar **Google Identity Services (GIS)** — la librería vigente. (La antigua `gapi.auth2` está deprecada, no usarla.) Botón estándar; One Tap opcional.
- **Vinculación de cuenta por email verificado de Google:**
  - Si ya existe un cliente con ese email → **vincular** esa cuenta (no crear duplicado).
  - Si no existe → **crear** cuenta con el email y nombre de Google.
  - Si el email ya tiene cuenta con contraseña → vincular de forma segura (sin duplicar ni exponer).
- Guardar lo mínimo: email, nombre y el `sub` (ID de Google).
- Configurar **OAuth Client ID** en Google Cloud Console por dominio (7 webs), con orígenes y redirect URIs autorizados. HTTPS obligatorio (ya lo es).
- Implementación vía extensión de social login de Magento 2 o integración GIS a medida — a criterio de la agencia, pero con la vinculación por email como requisito.

---

## Consideraciones

- **Privacidad/consentimiento:** informar y guardar el mínimo de datos; cumplir la política de datos.
- **Manejo de errores:** popup bloqueado, email no verificado por Google, usuario que cancela.
- No debe romper el checkout ni el login clásico.

---

## Criterios de aceptación

1. Un usuario nuevo puede registrarse con Google en **≤3 toques** y queda logueado en su cuenta.
2. Un usuario con email **ya registrado** que entra con Google **aterriza en la misma cuenta** (sin duplicado) y ve su historial de pedidos.
3. Desde el login con Google, el cliente accede a **estado de pedido e historial** en "Mi cuenta".
4. Funciona en las **7 webs**; el login con email + contraseña sigue operativo.

---

## Fuera de alcance (de este ticket)

- Autofill de dirección en el checkout → ticket aparte.
- Seguimiento de pedido como invitado (sin cuenta) → ticket aparte.
- **Login con Apple / Facebook → sí es de interés**, va como extensión en una fase/ticket posterior (este ticket arranca con Google, pero la arquitectura de vinculación por email debería contemplar sumar proveedores después).
