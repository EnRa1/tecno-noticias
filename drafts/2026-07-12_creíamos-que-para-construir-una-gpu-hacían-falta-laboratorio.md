<!--
ESTADO: borrador sin revisar - NO publicar directo
Fuente original: https://www.xataka.com/componentes/creiamos-que-para-construir-gpu-hacian-falta-laboratorios-millones-maker-esta-montando-casa
Imagen sugerida: https://i.blogs.es/2fe20e/gpu-5/840_560.jpeg
Fecha generacion: 2026-07-12T20:54:10.595262
-->

## FOCUS_KEYWORD
construcción de GPU casera con RISC-V

## SEO_TITLE
Un maker redefine la construcción de GPU casera con RISC-V

## SLUG
construccion-de-gpu-casera-con-risc-v

## META_DESCRIPTION
Descubre cómo Matthias Balwierz desafía las convenciones con la construcción de GPU casera con RISC-V usando miles de microcontroladores. Un hito en ingeniería doméstica.

## H1
Bitluni revoluciona la construcción de GPU casera con RISC-V desde su propio taller

## ARTICULO
Durante años, la idea de fabricar una GPU se ha asociado exclusivamente a grandes empresas tecnológicas con laboratorios de vanguardia, equipos de ingeniería masivos y presupuestos multimillonarios. Era una percepción lógica, dada la inmensa complejidad que exhibe cualquier tarjeta gráfica moderna. Sin embargo, Matthias Balwierz, más conocido como Bitluni en la comunidad maker, ha puesto en jaque esta creencia. Aunque su objetivo no es replicar una GeForce ni competir con gigantes como NVIDIA, Bitluni está demostrando la viabilidad de la **[construcción de GPU casera con RISC-V](https://www.xataka.com/componentes/creiamos-que-para-construir-gpu-hacian-falta-laboratorios-millones-maker-esta-montando-casa)** a gran escala, empleando miles de microcontroladores desde la comodidad de su hogar.

### El desafío de la GPU casera con RISC-V: Pixel a Pixel

La fase inicial del ambicioso proyecto de Bitluni involucra 8.192 microcontroladores, cada uno conectado directamente a un LED RGB. Esta singular aproximación diluye las fronteras tradicionales de diseño: el sistema no solo procesa gráficos, sino que también es la superficie donde se visualizan. En esencia, funciona simultáneamente como una tarjeta gráfica y una pantalla, eliminando la necesidad de un monitor externo. Este prototipo aún es una parte de la visión completa, pero ya evidencia el potencial de su concepto.

Curiosamente, esta arquitectura no fue el plan original. Bitluni comenzó explorando la creación de una pantalla, pero los altos costos y la dificultad de los componentes RGB direccionables lo llevaron a buscar una alternativa. La solución fue tan directa como ingeniosa: soldar un LED a cada microcontrolador, transformando cada chip en una unidad gráfica autónoma y visible. Si bien esta decisión optimizó el presupuesto, elevó considerablemente la complejidad en diseño, ensamblaje y programación para coordinar los miles de elementos.

### De la idea inicial a una resolución 'retro'

La verdadera magnitud del proyecto se comprende al observar su objetivo final. Una resolución Full HD (1920x1080 píxeles) habría requerido más de dos millones de microcontroladores, un costo y una complejidad inasumibles para un maker. Bitluni ajustó sus aspiraciones a una resolución de 320x200 píxeles, evocando la era de los videojuegos DOS, que aún demanda 64.000 chips. Los componentes ya instalados apenas representan el punto de partida de un sistema que, al completarse, multiplicaría su tamaño casi por ocho.

Para gestionar tal cantidad de hardware, Bitluni dividió el sistema en módulos de 16x32 "píxeles", funcionando como unidades independientes dentro de un conjunto mayor. Estos módulos se organizan en una disposición circular que, visualmente, rinde homenaje al superordenador Cray-1 de los años setenta. La coordinación interna también es jerárquica: cada grupo de 32 microcontroladores está bajo el control de una unidad CH32V más potente, que orquesta su operación y actúa como un nivel intermedio dentro de la estructura general.

La elección del microcontrolador QingKe CH570 es clave para la lógica económica del proyecto. Este chip incorpora una CPU RISC-V de 32 bits, un conjunto de instrucciones limitado y una frecuencia máxima de 100 MHz. Además, integra un controlador USB, un transceptor de 2,4 GHz y soporte para Bluetooth 5.0 LE. Bitluni pudo adquirir cada unidad por aproximadamente 0,13 dólares, aunque esta ventaja económica se reduce al multiplicar la cantidad por la matriz completa: solo los chips para la resolución de 320x200 píxeles superarían los 8.000 dólares.

### Superando los límites: Alimentación, Fabricación y Programación

Los desafíos no terminan con la adquisición de chips. La alimentación del sistema completo es monumental: se estima que la configuración final requeriría 2.161 W, equivalentes a unos 655 amperios a 3,3 V. Para manejar esta carga, Bitluni emplea una fuente de alimentación Corsair WS3000 y convertidores diseñados por él mismo, que transforman los 12 V de salida en los 3,3 V necesarios.

Gran parte del proyecto implica también la creación de la infraestructura subyacente. Bitluni diseñó personalmente las placas de circuito impreso (PCB), los circuitos de alimentación, las placas de interfaz y las de prueba, enfrentándose por primera vez al diseño de una PCB de seis capas. La complejidad lo llevó hasta los límites de los servicios de fabricación que utilizó. Incluso exploró una solución de refrigeración por inmersión, llegando a dimensionar el contenedor acrílico que sería necesario, aunque esta opción quedó en suspenso por motivos económicos y medioambientales.

La programación de miles de microcontroladores presentó otro problema de escala. Para evitar el tedioso proceso manual, Bitluni ideó una solución ingeniosa: imprimió en 3D una pequeña herramienta con tres contactos y la acopló al carro de una impresora 3D. Un script de Python se encargaba de enviar órdenes G-code, moviendo la herramienta a la posición exacta de cada chip para automatizar el proceso de carga del código. Así, la impresora pasó de ser un fabricante de piezas a una máquina de programación automatizada.

Esta innovadora creación no pretende competir en rendimiento, eficiencia o tamaño con las tarjetas gráficas comerciales actuales, y aún no ha alcanzado su escala proyectada. Su verdadero mérito radica en la capacidad de Bitluni para desglosar y reconstruir, mediante componentes discretos, las complejas tareas que una GPU comercial concentra en chips especializados: cálculo, control, alimentación, coordinación y visualización. Al reensamblar estos elementos con microcontroladores de bajo costo, este maker ha transformado una idea audaz en un sistema modular, susceptible de ser diseñado, probado y ampliado por etapas. Más allá de ser una GPU doméstica convencional, es un experimento de ingeniería que desafía los límites de lo que es posible construir en casa.

Fuente: Xataka

## ALT_TEXT
Módulo de la GPU casera de Bitluni, mostrando una matriz de microcontroladores RISC-V y LEDs RGB que actúan como píxeles.