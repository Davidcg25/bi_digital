# Seguimiento de pedido como invitado (sin login)

**Tipo:** Mejora / Feature
**Prioridad:** Media
**Alcance:** 7 webs Solidez (Magento)

---

## Objetivo y valor

Permitir que un cliente **consulte el estado de su pedido sin crear cuenta ni iniciar sesión**, ingresando nº de orden + email.

- Ataca directo el dolor de **"¿dónde está mi pedido?"** → menos consultas al soporte (autoservicio).
- Cubre al segmento que **no quiere registrarse** (complementa el login con Google, que es para los que sí quieren cuenta/historial).

---

## Alcance funcional

- Página **"Rastrea tu pedido"**: formulario con nº de orden + email → muestra estado del pedido, items, y el **tracking del courier** (número de guía + link) si existe.
- Enlace visible en **header/footer** y en el **correo de confirmación** de compra.
- Magento 2 trae la función nativa **"Orders and Returns"** (consulta de pedido como invitado por nº de orden + email + apellido) → habilitarla/estilizarla, o hacerla a medida.

---

## Requisitos técnicos

- Validar que **nº de orden + email coincidan**; anti-enumeración: no revelar si un email existe, **rate-limit** a los intentos.
- Mostrar estado + número de guía/courier + link de tracking cuando esté disponible.
- Responsive mobile.

---

## Criterios de aceptación

1. Un invitado con nº de orden + email correcto **ve el estado y el tracking** de su pedido sin loguearse.
2. No se puede acceder a un pedido **sin el email correcto** (seguridad / anti-enumeración).
3. Enlace accesible desde **header/footer** y desde el **correo de confirmación**.
4. Funciona en las **7 webs**.

---

## Fuera de alcance

- Login con cuenta (Google / clásico) → ticket aparte.
