# Habilitar autocompletado del navegador en el checkout (mobile)

**Tipo:** Bug / Mejora de conversión
**Prioridad:** Alta
**Alcance:** Theme compartido → aplica a las 7 webs Solidez

---

## Contexto e impacto

El formulario de dirección del checkout **no expone los atributos `autocomplete` / `inputmode`** en ninguno de sus campos (verificado por auditoría automatizada: **0% de campos con autocomplete** en las 7 webs).

Consecuencia: el **autofill nativo del navegador no funciona de forma fiable**, sobre todo en mobile, donde el usuario termina tipeando manualmente ~12 campos.

Esto pega justo donde más se pierde la venta:

- El **83-90% del tráfico es mobile** y convierte 2-3.5x peor que desktop.
- En **New Balance mobile, ~6 de cada 10 personas que inician el checkout abandonan antes de terminar el formulario de dirección** — más de **8,000 compradores potenciales al mes, en una sola web**. En desktop la caída es mucho menor con el mismo formulario → lo que falla no es la demanda, es la **fricción del formulario en mobile**.
- Reducir esa fricción (empezando por el autofill) es una palanca directa sobre la mayor fuga del embudo.

---

## Páginas afectadas

- Paso de **dirección de envío** del checkout (one-page), todas las storeviews.
- Por ser theme compartido, **un solo cambio corrige las 7 webs**.

---

## Cambios solicitados — atributos por campo

Agregar a cada input/select del formulario de dirección:

```
Campo                       type     inputmode   autocomplete
--------------------------  -------  ----------  -------------------
Correo electronico          email    email       email
Nombres                     text     -           given-name
Apellidos                   text     -           family-name
Telefono                    tel      tel         tel
Calle / Direccion           text     -           address-line1
Numero                      text     -           address-line2
Referencia                  text     -           (libre, opcional)
Departamento (select)       -        -           address-level1
Provincia (select)          -        -           address-level2
Distrito (select)           -        -           address-level3
Tipo de documento (select)  -        -           (default = DNI, no Pasaporte)
Numero de documento         text     numeric     (sin token estandar)
```

Además: envolver los campos en un único `<form>` semántico para que el navegador agrupe correctamente el bloque de contacto y el de dirección.

---

## ⚠️ Consideración técnica clave: los 3 selects en cascada

Los tokens `address-level1/2/3` ayudan, pero **el autofill se dispara antes de que Provincia/Distrito se hayan poblado por AJAX** (dependen del select padre) → quedan vacíos.

Hay que resolver una de estas dos vías:

1. **Precargar el árbol de ubigeo completo en cliente** (sin depender del AJAX en cascada), de modo que todas las opciones existan al momento del autofill; **o**
2. **Escuchar el evento de autofill del campo Departamento** y disparar programáticamente la carga + seteo de Provincia y Distrito.

Y asegurar que los **labels/valores de las opciones coincidan con los nombres estándar** (ej. "Lima") para que el navegador pueda matchearlos.

---

## Criterios de aceptación

1. Con una dirección guardada en Chrome Android / Safari iOS, al tocar el campo de correo o nombre, el navegador **ofrece autocompletar y llena en un solo gesto** el bloque de contacto **y** la dirección (incluidos depto/prov/distrito).
2. El panel **Issues de Chrome DevTools no reporta** "Input elements should have autocomplete attributes" en el checkout.
3. La auditoría de **Lighthouse** (best-practices, audit de autocomplete en formularios) pasa en el checkout.
4. El campo **"Tipo de documento" viene por defecto en DNI** (no Pasaporte).
5. Funciona en las **7 webs** sin regresión visual del formulario.

---

## Fuera de alcance

- Rediseño del checkout o reducción del número de pasos (ticket aparte).
- "Sign in with Google" (mejora complementaria de cuenta; **no** resuelve la dirección — ticket aparte).

---

## Cómo verificamos del lado nuestro

Re-corremos el auditor on-site post-deploy: el campo `autocomplete_pct` debe pasar de **0% a ≥80%**, y validamos el llenado en un dispositivo real.
