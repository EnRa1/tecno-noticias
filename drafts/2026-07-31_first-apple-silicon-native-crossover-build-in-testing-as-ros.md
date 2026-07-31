<!--
ESTADO: borrador sin revisar - NO publicar directo
Fuente original: https://appleinsider.com/articles/26/07/31/first-apple-silicon-native-crossover-build-in-testing-as-rosettas-end-nears?utm_source=rss
Imagen sugerida: 
Fecha generacion: 2026-07-31T20:05:57.889597
-->

## FOCUS_KEYWORD
soporte nativo de CrossOver para Apple Silicon

## SEO_TITLE
CodeWeavers prueba el soporte nativo de CrossOver para Apple Silicon

## SLUG
soporte-nativo-crossover-apple-silicon

## META_DESCRIPTION
CodeWeavers inicia las pruebas del soporte nativo de CrossOver para Apple Silicon. La herramienta se prepara para el fin de Rosetta 2 en los sistemas macOS.

## H1
Llega el soporte nativo de CrossOver para Apple Silicon

## ARTICULO
La era de la traducción de software en macOS está llegando a su fin y los desarrolladores más importantes del ecosistema ya están tomando medidas drásticas. CodeWeavers ha anunciado oficialmente el lanzamiento de una versión preliminar que introduce el **soporte nativo de CrossOver para Apple Silicon**, un hito técnico que busca garantizar la supervivencia de esta herramienta de compatibilidad ante la inminente jubilación de Rosetta 2 por parte de Apple.

Desde la transición a los procesadores de la serie M, CrossOver ha dependido de la capa de traducción de Apple para ejecutar aplicaciones diseñadas originalmente para Intel. Sin embargo, en junio de 2025, la compañía de Cupertino advirtió que el software deberá funcionar sin mediadores antes de la gran actualización de macOS prevista para 2027. Ante este ultimátum, el equipo de desarrollo ha acelerado un proyecto que llevaba años en fase de planificación interna.

Esta nueva compilación no es una simple actualización menor, sino un cambio estructural profundo. La integración del **soporte nativo de CrossOver para Apple Silicon** permite que la aplicación aproveche directamente la arquitectura ARM64 de los chips M1, M2 y M3, eliminando la necesidad de que el sistema operativo traduzca las instrucciones en tiempo real. Este avance es fundamental para mantener el rendimiento en juegos y software profesional de Windows que se ejecutan sobre Mac.

## Claves del soporte nativo de CrossOver para Apple Silicon

El camino para llegar a este punto ha sido complejo y ha requerido hitos significativos en el desarrollo de Wine, el motor que impulsa esta herramienta. Según detalla la [publicación oficial de CodeWeavers](https://www.codeweavers.com/blog/mjohnson/2026/7/31/crossover-preview-the-right-to-bear-arm64-on-mac), el proceso comenzó en 2023 con Wine 8.0 y la implementación de conversiones PE. Posteriormente, Wine 10.0 en 2025 añadió soporte completo para ARM64EC, permitiendo la ejecución de código x86-64 emulado dentro de entornos nativos.

Para lograr que el **soporte nativo de CrossOver para Apple Silicon** fuera una realidad en macOS, el equipo tuvo que adaptar una versión personalizada de FEX. Esta tecnología es la encargada de manejar la emulación necesaria para que los binarios de Windows funcionen sobre el hardware de Apple sin depender de las librerías propietarias que Apple planea retirar. Es una solución de ingeniería robusta que asegura la independencia total del software frente a las decisiones de infraestructura de la compañía dirigida por Tim Cook.

Actualmente, el **soporte nativo de CrossOver para Apple Silicon** se encuentra disponible a través del Preview Center para usuarios registrados. Las notas de la versión aclaran que estas compilaciones son "universales", lo que significa que incluyen tanto el motor para Intel como el nuevo motor para ARM. Sin embargo, para activar la ejecución nativa y dejar de usar Rosetta, los usuarios deben contar con macOS 12.5 o una versión superior; de lo contrario, el software volverá automáticamente al modo de compatibilidad antiguo.

## Desafíos técnicos y limitaciones actuales

A pesar del entusiasmo, esta fase de pruebas inicial presenta algunas restricciones importantes que los entusiastas deben considerar. Por ejemplo, el **soporte nativo de CrossOver para Apple Silicon** todavía no incluye D3DMetal, lo que limita temporalmente el uso de DirectX 12. Tampoco es posible convertir "botellas" (entornos configurados) existentes de versiones anteriores; los usuarios deben crear configuraciones nuevas desde cero para testear la arquitectura ARM64.

Otro punto crítico reportado es la incompatibilidad temporal con varios lanzadores de juegos populares, que todavía no logran ejecutarse correctamente bajo este nuevo esquema. No obstante, CodeWeavers ha incluido DXMT, una opción gráfica optimizada que mejora los resultados en las pruebas de rendimiento actuales. La meta de la empresa es pulir todas estas asperezas antes del lanzamiento definitivo de CrossOver 27, programado para principios del próximo año.

La transición hacia el **soporte nativo de CrossOver para Apple Silicon** es un movimiento preventivo esencial. Si los desarrolladores esperaran hasta el lanzamiento de macOS 18 en 2027 para realizar este cambio, el software quedaría inutilizado de la noche a la mañana. Con esta fase de pruebas, la comunidad tiene casi un año para identificar errores y optimizar la emulación de instrucciones x86 bajo el nuevo motor FEX.

Para los usuarios finales, esto significa que la posibilidad de jugar títulos de Windows en Mac seguirá vigente incluso cuando Apple desconecte definitivamente los cables de la arquitectura Intel. El [soporte nativo de CrossOver para Apple Silicon](https://appleinsider.com/articles/26/07/31/first-apple-silicon-native-crossover-build-in-testing-as-rosettas-end-nears?utm_source=rss) representa la madurez de una plataforma que ha sabido adaptarse a los cambios de hardware más desafiantes de la última década, garantizando que la interoperabilidad no sea una víctima del progreso tecnológico.

Fuente: AppleInsider News y www.codeweavers.com

## ALT_TEXT
Interfaz de CrossOver ejecutándose de forma nativa en una Mac con procesador Apple Silicon para emular aplicaciones de Windows.