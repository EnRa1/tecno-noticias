<!--
ESTADO: borrador sin revisar - NO publicar directo
Fuente original: https://www.computerworld.com/article/4209164/it-took-58-to-break-microsofts-sccm-but-a-patch-made-it-harder-2.html
Imagen sugerida: https://www.computerworld.com/wp-content/uploads/2026/08/4209164-0-08336600-1786623598-shutterstock_2662877349.jpg?quality=50&strip=all&w=1024
Fecha generacion: 2026-08-13T17:04:30.398796
-->

## FOCUS_KEYWORD
falla de seguridad en Microsoft SCCM

## SEO_TITLE
Falla de seguridad en Microsoft SCCM: ataque de solo 58 dólares

## SLUG
falla-de-seguridad-en-microsoft-sccm

## META_DESCRIPTION
Descubre la falla de seguridad en Microsoft SCCM que permite tomar control de servidores corporativos por solo 58 dólares. Detalles del parche y mitigación.

## H1
Un ataque de 58 dólares explota la falla de seguridad en Microsoft SCCM

## ARTICULO
El reciente reporte sobre una **falla de seguridad en Microsoft SCCM** ha encendido las alarmas en los departamentos de IT a nivel global. Investigadores de la firma XM Cyber demostraron que un usuario estándar, sin ningún tipo de privilegios administrativos, puede encadenar una serie de vulnerabilidades para ejecutar código de forma remota en los servidores principales de una organización. Lo más impactante del hallazgo no es solo el alcance del compromiso, sino el bajísimo costo económico necesario para vulnerar un sistema de gestión tan robusto.

A través de esta **falla de seguridad en Microsoft SCCM**, los atacantes pueden escalar privilegios hasta obtener el control como "NT AUTHORITY\SYSTEM". Según los expertos, una vez que el servidor del sitio primario se ve comprometido, todos los clientes gestionados por esa infraestructura quedan expuestos. En términos prácticos, esto significa que un actor malintencionado podría tomar el control total de los activos tecnológicos de una compañía entera, desde estaciones de trabajo hasta servidores críticos de datos.

### El impacto de la falla de seguridad en Microsoft SCCM en redes empresariales

La cadena de ataque descubierta combina cuatro debilidades específicas. La primera de ellas es una autorización defectuosa en la función de carga de archivos de AdminService. Los investigadores notaron que, si bien el punto de carga estándar verifica los permisos, su contraparte de carga "fragmentada" (chunked-upload) no lo hace. Esto abre la puerta para que cualquier usuario autenticado en el Active Directory suba archivos maliciosos sin ser detectado inicialmente.

A esto se suma una vulnerabilidad de recorrido de directorios (path-traversal) denominada "CabSlip". Gracias a ella, los archivos pueden escapar de las carpetas temporales y escribirse en cualquier parte del sistema de archivos del servidor. Mientras el ecosistema tecnológico global debate sobre grandes lanzamientos, como los que se mencionan en [portales de noticias internacionales](https://www.nieuwskoerier.nl/artikel/0493b255cdbe-onze-volkswagen-golf-gti-edition-30-zit-wel-erg-hoog-boven-de-fabrieks/), esta **falla de seguridad en Microsoft SCCM** demuestra que los problemas estructurales en herramientas de gestión interna siguen siendo el eslabón más débil de la cadena defensiva.

### Un certificado de bajo costo como llave maestra

Uno de los puntos más críticos revelados por XM Cyber es que la validación de firmas de SCCM es insuficiente. El sistema no verifica si el certificado de firma pertenece a Microsoft o a la organización propietaria; solo comprueba que sea estructuralmente válido y que no haya expirado. Esto permitió a los investigadores utilizar un certificado comercial de código abierto que cuesta apenas 58 dólares para engañar al servidor y cargar una librería DLL maliciosa.

Al no existir una validación estricta de la cadena de confianza, cualquier atacante con una pequeña inversión puede saltarse las fronteras de seguridad. Microsoft ya ha comenzado a trabajar en soluciones, identificando la vulnerabilidad inicial como CVE-2026-47301. Aunque se lanzó un parche en julio, este solo bloquea el acceso a usuarios de dominio estándar. Aquellos con roles de "Administrador de Operaciones" o permisos personalizados aún podrían, teóricamente, activar la secuencia de compromiso hasta que se lance la actualización integral planeada para octubre.

### Medidas de mitigación y defensa proactiva

Para las empresas que dependen de esta herramienta, la recomendación inmediata es restringir el acceso de red a la API de AdminService y auditar exhaustivamente las asignaciones de roles internos. Los equipos de defensa deben monitorear los registros en busca de excepciones específicas de "Directorio no encontrado" seguidas de errores HTTP 500, ya que este patrón suele indicar que se ha intentado explotar el recorrido de archivos.

Es fundamental que los administradores de sistemas se mantengan informados sobre cada nueva actualización relacionada con la [falla de seguridad en Microsoft SCCM](https://www.computerworld.com/article/4209164/it-took-58-to-break-microsofts-sccm-but-a-patch-made-it-harder-2.html) para proteger sus activos. La vigilancia sobre modificaciones inesperadas en archivos críticos, como la librería "adsource.dll", puede ser la diferencia entre detectar un ataque en curso o sufrir una intrusión masiva que comprometa toda la estructura de la organización.


## Qué significa para Argentina

Esta vulnerabilidad es de suma relevancia para Argentina, ya que Microsoft SCCM es el estándar de gestión en la mayoría de los bancos, organismos públicos y grandes empresas con flotas de miles de computadoras en el país. Los departamentos de ciberseguridad locales deben priorizar la aplicación del parche CVE-2026-47301, dado que el uso de certificados comerciales para ataques de bajo costo es una técnica común en la región. Se recomienda a las firmas locales auditar los roles de "Administrador de Operaciones" para prevenir escaladas de privilegios internas.

Fuente: Computerworld y www.nieuwskoerier.nl
