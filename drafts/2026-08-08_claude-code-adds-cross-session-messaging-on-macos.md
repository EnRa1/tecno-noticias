<!--
ESTADO: borrador sin revisar - NO publicar directo
Fuente original: https://www.macrumors.com/2026/08/08/claude-code-adds-cross-session-messaging/
Imagen sugerida: https://images.macrumors.com/t/miP5cemYnLJaXuUhVtW0LMDZCJQ=/1782x/article-new/2025/09/anthopic-claude.jpg
Fecha generacion: 2026-08-08T20:06:03.936554
-->

## FOCUS_KEYWORD
mensajes entre sesiones de Claude Code

## SEO_TITLE
Los mensajes entre sesiones de Claude Code ya funcionan en Mac

## SLUG
mensajes-entre-sesiones-de-claude-code

## META_DESCRIPTION
Anthropic actualiza su herramienta CLI para permitir mensajes entre sesiones de Claude Code, optimizando el flujo de trabajo sin copiar datos manualmente en la terminal.

## H1
Los mensajes entre sesiones de Claude Code ya funcionan en Mac

## ARTICULO
La evolución de las herramientas de inteligencia artificial para desarrolladores ha dado un paso significativo hacia la automatización del flujo de trabajo multitararea. Anthropic ha presentado una actualización crucial para su interfaz de línea de comandos (CLI), permitiendo que los **mensajes entre sesiones de Claude Code** fluyan de manera nativa sin que el usuario deba intervenir en procesos tediosos de copiado y pegado de información técnica.

Esta nueva funcionalidad, introducida en la versión 2.1.224, busca resolver uno de los problemas más comunes en entornos de desarrollo complejos: la pérdida de contexto cuando se trabaja con múltiples terminales abiertas. Hasta ahora, si un programador detectaba un error en una parte del sistema mientras trabajaba en otra, debía trasladar manualmente los registros o explicaciones entre ventanas. Con la implementación de los **mensajes entre sesiones de Claude Code**, la herramienta ahora puede comunicarse consigo misma en instancias separadas de macOS y Linux.

### Herramientas técnicas para la comunicación interna
El funcionamiento de esta característica se apoya en dos nuevos instrumentos integrados en el motor de la IA. Por un lado, la función `ListAgents` se encarga de rastrear e identificar qué otras sesiones están activas y disponibles en el sistema local. Por otro lado, la función `SendMessage` es la responsable de empaquetar y despachar la información hacia el destino seleccionado.

Cuando una sesión recibe uno de estos **mensajes entre sesiones de Claude Code**, el sistema lo presenta visualmente como una tarjeta etiquetada que incluye un enlace directo hacia la sesión de origen. Es importante destacar que, según la documentación técnica oficial del ecosistema de [desarrollo de Claude](https://code.claude.com/docs/en/cross-session-messaging), solo se transfiere texto plano. Esto significa que el historial completo de la conversación, los archivos adjuntos o los permisos de seguridad no se comparten entre las ventanas, manteniendo un aislamiento que prioriza la seguridad del entorno.

### Casos de uso: coordinación y eficiencia
La utilidad de estos **mensajes entre sesiones de Claude Code** brilla especialmente en proyectos que requieren trabajar con repositorios compartidos en distintos directorios o "worktrees". Según los detalles proporcionados por la compañía, un agente puede advertir a otro si un cambio reciente rompe una dependencia crítica, o simplemente pasar un resultado que otra instancia estaba esperando para continuar su ejecución.

Otro escenario clave es la supervisión de procesos de larga duración, como migraciones de bases de datos o suites de pruebas extensas. En lugar de monitorear constantemente una terminal pasiva, el desarrollador puede configurar la herramienta para que notifique el estado del proceso mediante **mensajes entre sesiones de Claude Code** hacia la ventana donde el usuario está activamente escribiendo código. Esta capacidad de respuesta asíncrona reduce drásticamente la carga cognitiva del profesional.

### Seguridad y limitaciones geográficas del sistema
Un aspecto fundamental que coinciden en señalar diversas fuentes del sector es que la comunicación se mantiene estrictamente local cuando ocurre dentro de una misma máquina. Esto garantiza que los datos sensibles no viajen a los servidores de Anthropic durante el intercambio entre terminales. Sin embargo, la dinámica cambia cuando se trata de comunicación entre diferentes dispositivos.

En situaciones donde se interactúa entre una computadora y dispositivos móviles (iOS o Android), los **mensajes entre sesiones de Claude Code** funcionan únicamente como respuesta a través de la función *Remote Control*. En estos casos, la IA no puede iniciar una conversación de forma autónoma entre máquinas distintas, sino que se limita a contestar hilos ya existentes, una medida de control para evitar ejecuciones no autorizadas de forma remota.

### Requisitos para la implementación
Para acceder a esta mejora, los usuarios deben contar con sistemas operativos basados en Unix, ya que por el momento la función no ha sido habilitada para Windows. La versión 2.1.224 no solo trajo consigo la capacidad de enviar [mensajes entre sesiones de Claude Code](https://www.macrumors.com/2026/08/08/claude-code-adds-cross-session-messaging/), sino que incluyó otras 31 modificaciones adicionales. Entre ellas, destaca el nuevo comando `claude self-hosted-runner`, diseñado específicamente para planes empresariales y de equipo que requieren un control más granular sobre sus infraestructuras de ejecución.

La integración de los **mensajes entre sesiones de Claude Code** marca un punto de inflexión en cómo las herramientas de IA dejan de ser simples asistentes de chat para convertirse en agentes coordinados que comprenden la complejidad de un entorno de trabajo real, donde el código no vive en una sola ventana, sino en un ecosistema interconectado.

Fuente: MacRumors: Mac News and Rumors - All Stories y code.claude.com
