<!--
ESTADO: borrador sin revisar - NO publicar directo
Fuente original: https://www.infoworld.com/article/4203879/jetbrains-says-a-crafted-http-request-could-break-teamcity-2.html
Imagen sugerida: https://www.infoworld.com/wp-content/uploads/2026/07/4203879-0-70488200-1785495016-shutterstock_2580529625.jpg?quality=50&strip=all&w=1024
Fecha generacion: 2026-07-31T17:08:07.681093
-->

## FOCUS_KEYWORD
vulnerabilidad crítica en TeamCity On-Premises

## SEO_TITLE
Vulnerabilidad crítica en TeamCity On-Premises expone servidores

## SLUG
vulnerabilidad-critica-en-teamcity-on-premises

## META_DESCRIPTION
JetBrains advierte sobre una vulnerabilidad crítica en TeamCity On-Premises con riesgo de ejecución de comandos. Solucioná el fallo con estos parches.

## H1
JetBrains corrige una vulnerabilidad crítica en TeamCity On-Premises de nivel 9.8

## ARTICULO
La seguridad en los entornos de desarrollo ha vuelto a encender las alarmas tras el reporte de una **vulnerabilidad crítica en TeamCity On-Premises** que afecta a organizaciones que gestionan sus propios servidores de CI/CD. JetBrains, la empresa detrás de esta plataforma, confirmó que un atacante sin credenciales podría tomar el control total de las instancias vulnerables a través de peticiones HTTP específicamente diseñadas.

Este fallo de seguridad ha sido catalogado con un puntaje de 9.8 sobre 10 en la escala CVSS, lo que indica un nivel de peligrosidad extremo. La principal razón de esta calificación es que el ataque no requiere interacción del usuario ni privilegios previos, dejando a cualquier servidor expuesto a internet en una situación de riesgo inminente ante la posible ejecución de comandos arbitrarios.

El problema técnico, identificado formalmente como CVE-2026-63077, se origina en el protocolo que utiliza la plataforma para la comunicación con sus agentes. Según detallan los informes técnicos, se trata de una falla de deserialización de datos no estructurados (CWE-502). Este error permite que un actor malintencionado envíe información manipulada para saltarse las barreras de autenticación y ejecutar código con los mismos permisos que posee el proceso del servidor.

Es fundamental entender el impacto que una **vulnerabilidad crítica en TeamCity On-Premises** puede tener en la cadena de suministro de software. Al comprometer el servidor, un atacante no solo accede a los datos internos, sino que podría inyectar código malicioso en las compilaciones, robar credenciales almacenadas o alterar el estado de los despliegues hacia producción. 

La integración con sistemas de control de versiones es uno de los puntos más sensibles. Como se detalla en la [documentación técnica oficial](https://www.jetbrains.com/help/teamcity/git.html), TeamCity gestiona de forma nativa repositorios Git y configuraciones de acceso de alta sensibilidad. Un acceso no autorizado mediante esta brecha permitiría a un atacante manipular estas conexiones, afectando la integridad de todo el ciclo de vida del desarrollo.

La detección de este fallo fue posible gracias al trabajo del investigador Antoni Tremblay, quien informó la situación de manera privada el pasado 10 de julio. Aunque hasta el momento no se han detectado casos de explotación activa en el ecosistema, los especialistas de [medios especializados en ciberseguridad](https://www.csoonline.com/article/4203872/jetbrains-says-a-crafted-http-request-could-break-teamcity.html) recuerdan que los grupos de amenazas suelen actuar con gran rapidez una vez que estas brechas se hacen públicas.

## Cómo mitigar la vulnerabilidad crítica en TeamCity On-Premises

Para resolver esta situación, JetBrains ha publicado actualizaciones de emergencia. La recomendación oficial para todos los administradores de sistemas es actualizar sus instalaciones a las versiones 2025.11.7 o 2026.1.3 de forma inmediata. Estas versiones contienen el parche definitivo que sella el agujero de seguridad en el protocolo de comunicación de agentes.

En aquellos casos donde una actualización completa no sea viable de forma inmediata debido a ciclos de mantenimiento internos, la empresa ha puesto a disposición un plugin de parche de seguridad. Esta solución alternativa es compatible con versiones de la plataforma desde la 2017.1 en adelante. Es importante notar que, para versiones muy antiguas (anteriores a 2018.1), la instalación del plugin requiere un reinicio del servicio para ser efectiva.

Mientras se aplican estas correcciones, una medida de precaución recomendada es restringir el acceso a los servidores TeamCity únicamente a redes confiables o mediante el uso de VPN. Limitar la exposición pública de la infraestructura de CI/CD reduce drásticamente la superficie de ataque frente a la **vulnerabilidad crítica en TeamCity On-Premises**.

Otro aspecto clave de seguridad mencionado por el fabricante es operar el proceso del servidor con los mínimos privilegios necesarios en el sistema operativo. De esta manera, incluso si un atacante lograra explotar un fallo, el daño potencial se vería limitado por las restricciones del entorno donde corre la aplicación.

Por otro lado, los usuarios de la modalidad Cloud pueden estar tranquilos, ya que JetBrains confirmó que esta infraestructura ya ha sido protegida y no requiere ninguna acción por parte de los clientes. La preocupación central se mantiene en las implementaciones locales, donde el control de las actualizaciones depende exclusivamente del equipo de IT de cada empresa.

Dada la naturaleza "pre-autenticación" de este error, la prioridad para aplicar los parches debe ser máxima. La historia reciente demuestra que este tipo de plataformas son objetivos de alto valor para ataques de espionaje corporativo y secuestro de datos. No demores la protección de tu infraestructura ante esta [vulnerabilidad crítica en TeamCity On-Premises](https://www.infoworld.com/article/4203879/jetbrains-says-a-crafted-http-request-could-break-teamcity-2.html).

Fuente: InfoWorld y www.csoonline.com, www.jetbrains.com

## ALT_TEXT
Interfaz de servidor JetBrains TeamCity mostrando alertas de seguridad y procesos de compilación de software.