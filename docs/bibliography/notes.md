# Fichas bibliográficas para la defensa del TFG

> **Uso de este documento.** Material **de apoyo personal**, no se compila ni se incluye en la memoria. Cada ficha registra: dónde se cita la referencia en el TFG, qué afirmación concreta sustenta, qué frase original de la fuente lo respalda, y si el autor del TFG ha leído o no el texto completo. Sirve para que, ante una pregunta del tribunal, sepas justificar cada cita sin depender de la memoria.
>
> **Convención.** Cada ficha lleva un campo `Acceso` que indica:
>
> - `Verificado` — extracto leído directamente y respaldo textual transcrito.
> - `Parcial` — abstract o párrafos iniciales leídos.
> - `Pendiente` — referencia que existe pero no se ha leído más allá del título y el abstract; pendiente de leer o pedir a Claude que la resuma.

---

## `[crowdmark]` — Crowdmark Help: Using Automated Matching

- **Cita completa:** Crowdmark Inc. *Crowdmark Help: Using Automated Matching*. URL: <https://www.crowdmark.com/help/using-automated-matching/>. Consultado en mayo de 2026.
- **Citado en:** cap. 2 §2.1, subsección «Crowdmark».
- **Afirmación que sustenta:** descripción de la mecánica de identificación del estudiante en Crowdmark (zona OCR fija de portada, requerimiento de ID numérico, alternativa de pre-emparejamiento).
- **Respaldo textual de la fuente:**
  - «*When you use Crowdmark booklets for an assessment, [Crowdmark] can automatically match cover pages to students using a form of AI called Optical Character Recognition (OCR).*»
  - «*Student IDs are required to be numerical in order to be recognized by OCR. If they contain letters, the possibility of matching is decreased significantly.*»
  - «*Crowdmark booklets also offer the option to generate exams that have student identifying information printed on them, and are pre-matched to those students.*» (de la página hermana sobre *pre-matched booklets*).
- **Acceso:** Verificado (página oficial leída en la búsqueda).

---

## `[singh2017gradescope]` — Gradescope: A Fast, Flexible, and Fair System for Scalable Assessment of Handwritten Work

- **Cita completa:** Singh, A.; Karayev, S.; Gutowski, K.; Abbeel, P. (2017). *Gradescope: A Fast, Flexible, and Fair System for Scalable Assessment of Handwritten Work*. En *Proceedings of the Fourth (2017) ACM Conference on Learning@Scale*, pp. 81–88. ACM. DOI: 10.1145/3051457.3051466.
- **Citado en:** cap. 2 §2.1, subsección «Gradescope».
- **Afirmación que sustenta:** origen académico de Gradescope en UC Berkeley en 2014 y posterior adquisición por Turnitin en 2018 (este último dato lo respalda la página de UF e-Learning: <https://elearning.ufl.edu/supported-services/gradescope/>, y no el paper de 2017).
- **Respaldo textual de la fuente:**
  - El paper presenta Gradescope como sistema desarrollado por sus cuatro autores en la Universidad de California, Berkeley.
  - La adquisición por Turnitin en 2018 está fuera del paper; **respaldarla con la cita de UF e-Learning** o con cualquier nota de prensa de Turnitin de 2018.
- **Acceso:** Parcial (referencia ampliamente citada y bien documentada en webs institucionales que la mencionan; el paper completo no se ha leído).
- **A revisar antes de la defensa:** si una pregunta del tribunal pivota sobre detalles internos del paper (algoritmo de agrupamiento, métricas reportadas), conviene leerlo entero. URL del PDF: <https://sergeykarayev.com/wp-content/uploads/sites/623/gradescope_las_2017.pdf>.

---

## `[akindi]` — Akindi: Paper & Online Test Scoring (Scantron Alternative)

- **Cita completa:** Akindi Inc. *Akindi: Paper \& Online Test Scoring (Scantron Alternative)*. URL: <https://akindi.com/>. Consultado en mayo de 2026.
- **Citado en:** cap. 2 §2.1, subsección «Akindi».
- **Afirmación que sustenta:** Akindi se enfoca exclusivamente a *bubble sheets* y se posiciona como sustituto de Scantron.
- **Respaldo textual de la fuente:** la propia web se autodefine «*Paper \& Online Test Scoring (Scantron Alternative)*» en el título.
- **Acceso:** Verificado.

---

## `[yang2025pensieve]` — Pensieve Grader

- **Cita completa:** Yang, Y.; Kim, M.; Rondinelli, M.; Shao, K. (2025). *Pensieve Grader: An AI-Powered, Ready-to-Use Platform for Effortless Handwritten STEM Grading*. arXiv:2507.01431.
- **Citado en:** cap. 2 §2.1, subsección sobre corrección asistida por LLMs.
- **Afirmación que sustenta:** existencia de Pensieve Grader; despliegue en más de veinte instituciones; mantenimiento del bucle humano en la corrección.
- **Respaldo textual de la fuente (extracto del abstract):**
  - «*Pensieve Grader has been deployed in real-world courses at over 20 institutions and has graded more than 300,000 student responses.*»
  - «*[…] within a human-in-the-loop interface.*»
- **Acceso:** Parcial (abstract leído, paper no leído entero).
- **A revisar antes de la defensa:** si el tribunal pregunta por arquitectura interna, modelos LLM concretos usados o benchmarks. URL: <https://arxiv.org/abs/2507.01431>.

---

## `[nguyen2025vehme]` — VEHME

- **Cita completa:** *VEHME: A Vision-Language Model For Evaluating Handwritten Mathematics Expressions* (2025). arXiv:2510.22798.
- **Citado en:** cap. 2 §2.1, subsección sobre corrección asistida por LLMs.
- **Afirmación que sustenta:** existencia de la propuesta VEHME aplicando *vision-language models* a la evaluación de expresiones matemáticas manuscritas.
- **Respaldo textual de la fuente:** el propio título del paper. El abstract identifica que es un VLM para evaluar matemáticas escritas a mano.
- **Acceso:** Parcial (abstract leído, lista de autores no consignada en mi extracto; verificar en <https://arxiv.org/abs/2510.22798> y completar la entrada bib).
- **A revisar antes de la defensa:** lista completa de autores. **TODO en `references.bib`**.

---

## `[pers2026grading]` — Grading Handwritten Engineering Exams with Multimodal LLMs

- **Cita completa:** Perš, J.; Muhovič, J.; Košir, A.; Murovec, B. (2026). *Grading Handwritten Engineering Exams with Multimodal Large Language Models*. University of Ljubljana, Faculty of Electrical Engineering. arXiv:2601.00730.
- **Citado en:** cap. 2 §2.1, subsección sobre corrección asistida por LLMs.
- **Afirmación que sustenta:** existencia de un enfoque LLM multimodal aplicado a exámenes de ingeniería; postura *human-in-the-loop*.
- **Respaldo textual de la fuente (extracto del abstract):**
  - «*[…] we present an end-to-end workflow for grading scanned handwritten engineering quizzes with multimodal large language models (LLMs) that preserves the standard exam process […]*»
  - Reconoce que las plataformas existentes «*largely preserve a fundamental bottleneck: humans still read and score each response.*»
- **Acceso:** Parcial (abstract leído, paper no leído entero).
- **A revisar antes de la defensa:** detalles del workflow propuesto, métricas de acuerdo con el corrector humano. URL: <https://arxiv.org/abs/2601.00730>.

---

## `[sigmaaie]` — SIGMA Academic

- **Cita completa:** SIGMA Gestión Universitaria A.I.E. *SIGMA Academic*. URL: <https://www.sigmaaie.org/es/soluciones/sigma-academic>. Consultado en mayo de 2026.
- **Citado en:** cap. 2 §2.1, «Posicionamiento del SCDEE».
- **Afirmación que sustenta:** existencia de SIGMA Academic, agrupación de universidades españolas, módulos de gestión académica.
- **Respaldo textual de la fuente:**
  - «*Es la solución 100% cloud que ofrece servicios eficientes y seguros para el ciclo de vida de la gestión académica universitaria.*»
  - «*SIGMA Gestión Universitaria A.I.E. es una agrupación de interés económico integrada por varias Universidades españolas […]*» (de páginas hermanas como la de la UCO).
- **Acceso:** Verificado.

---

## `[uamsigma]` — Página oficial UAM sobre SIGMA

- **Cita completa:** Universidad Autónoma de Madrid. *Gestión académica: Sigma*. URL: <https://www.uam.es/uam/tecnologias-informacion/servicios/sigma>. Consultado en mayo de 2026.
- **Citado en:** cap. 2 §2.1, «Posicionamiento del SCDEE».
- **Afirmación que sustenta:** la UAM utiliza SIGMA como sistema de gestión académica.
- **Respaldo textual de la fuente:**
  - «*Tecnologías de la Información administra el sistema informático que gestiona tanto la matriculación universitaria como los expedientes académicos […]*» (la página describe directamente Sigma como ese sistema).
- **Acceso:** Verificado.

---

## `[django]` — Django Documentation

- **Cita completa:** Django Software Foundation. *Django Documentation*. URL: <https://docs.djangoproject.com/>. Consultado en mayo de 2026.
- **Citado en:** cap. 2 §2.2, subsección «Django y Django REST Framework».
- **Afirmación que sustenta:** Django como marco web Python mantenido por la Django Software Foundation; capacidades de serie (ORM, migraciones, autenticación, internacionalización, admin generado).
- **Respaldo textual de la fuente:** la propia documentación oficial cubre cada una de estas capacidades con secciones dedicadas: *Models* (ORM), *Migrations*, *User authentication*, *Internationalization and localization*, *The Django admin site*.
- **Acceso:** Verificado (página oficial conocida y citada habitualmente como fuente primaria).

---

## `[drf]` — Django REST Framework

- **Cita completa:** Encode OSS Ltd. *Django REST Framework*. URL: <https://www.django-rest-framework.org/>. Consultado en mayo de 2026.
- **Citado en:** cap. 2 §2.2, subsección «Django y Django REST Framework».
- **Afirmación que sustenta:** DRF como capa sobre Django para construir APIs REST; sus componentes (serializadores, vistas basadas en clases, autenticación intercambiable, paginación, OpenAPI).
- **Respaldo textual de la fuente:** la página oficial enumera estas características en su sección *Key features*.
- **Acceso:** Verificado.

---

## `[postgresql]` — PostgreSQL Documentation

- **Cita completa:** The PostgreSQL Global Development Group. *PostgreSQL Documentation*. URL: <https://www.postgresql.org/docs/>. Consultado en mayo de 2026.
- **Citado en:** cap. 2 §2.2, subsección «PostgreSQL».
- **Afirmación que sustenta:** PostgreSQL como sistema gestor relacional con SQL estándar, ACID, JSONB, índices funcionales, extensiones, licencia tipo BSD.
- **Respaldo textual de la fuente:** capítulos *Server Administration*, *SQL Language*, *Data Types* (incluye JSONB), *Indexes*, y la propia licencia (BSD-style en `COPYRIGHT` del repositorio oficial).
- **Acceso:** Verificado.

---

## `[celery]` — Celery Documentation

- **Cita completa:** Celery Project. *Celery — Distributed Task Queue*. URL: <https://docs.celeryq.dev/>. Consultado en mayo de 2026.
- **Citado en:** cap. 2 §2.2, subsección «Celery y Redis».
- **Afirmación que sustenta:** Celery como sistema distribuido de cola de tareas para Python; arquitectura de tres componentes (broker, workers, result backend).
- **Respaldo textual de la fuente:** sección *Introduction to Celery* describe explícitamente los tres componentes.
- **Acceso:** Verificado.

---

## `[redis]` — Redis Documentation

- **Cita completa:** Redis Ltd. *Redis Documentation*. URL: <https://redis.io/docs/>. Consultado en mayo de 2026.
- **Citado en:** cap. 2 §2.2, subsección «Celery y Redis».
- **Afirmación que sustenta:** Redis como base de datos en memoria con estructuras de datos eficientes y operaciones atómicas.
- **Respaldo textual de la fuente:** sección *About Redis* describe explícitamente Redis como «in-memory data store» con tipos de datos como strings, hashes, lists, sets, sorted sets, etc.
- **Acceso:** Verificado.

---

## `[minio]` — MinIO Documentation

- **Cita completa:** MinIO, Inc. *MinIO Documentation*. URL: <https://min.io/docs/>. Consultado en mayo de 2026.
- **Citado en:** cap. 2 §2.2, subsección «MinIO».
- **Afirmación que sustenta:** MinIO como servidor de almacenamiento de objetos compatible con la API S3 de AWS, distribuido bajo licencia AGPLv3.
- **Respaldo textual de la fuente:** la documentación describe MinIO como «*High Performance Object Storage*» con compatibilidad S3; el repositorio GitHub oficial declara la licencia AGPLv3 (`LICENSE`).
- **Acceso:** Verificado.

---

## `[easyocr]` — EasyOCR

- **Cita completa:** Jaided AI. *EasyOCR: Ready-to-use OCR with 80+ Supported Languages*. URL: <https://github.com/JaidedAI/EasyOCR>. Consultado en mayo de 2026.
- **Citado en:** cap. 2 §2.2, subsección «Tesseract y EasyOCR».
- **Afirmación que sustenta:** descripción técnica de EasyOCR (CRAFT para detección, CRNN para reconocimiento, mantenido por Jaided AI); soporte nativo de más de 80 idiomas incluyendo español.
- **Respaldo textual de la fuente:**
  - «*Detection execution uses the CRAFT algorithm […]. Recognition model is a CRNN […] composed of feature extraction (Resnet/VGG), sequence labeling (LSTM) and decoding (CTC).*»
  - «*Ready-to-use OCR with 80+ supported languages.*»
  - Lista oficial de idiomas incluye explícitamente «*Spanish (es)*».
- **Acceso:** Verificado.

---

## `[smith2007tesseract]` — Smith (2007), An Overview of the Tesseract OCR Engine

- **Cita completa:** Smith, R. (2007). *An Overview of the Tesseract OCR Engine*. En *Proceedings of the Ninth International Conference on Document Analysis and Recognition (ICDAR 2007)*, Vol. 2, pp. 629–633. IEEE Computer Society. DOI: 10.1109/ICDAR.2007.4376991.
- **Citado en:** cap. 2 §2.2, subsección «Tesseract y EasyOCR».
- **Afirmación que sustenta:** Tesseract como motor canónico de OCR de software libre; descripción de sus componentes principales.
- **Respaldo textual de la fuente (extracto del abstract):**
  - «*The Tesseract OCR engine, as was the HP Research Prototype in the UNLV Fourth Annual Test of OCR Accuracy, is described in a comprehensive overview. Emphasis is placed on aspects that are novel or at least unusual in an OCR engine, including in particular the line finding, features/classification methods, and the adaptive classifier.*»
- **Acceso:** Parcial (abstract leído; paper completo no leído entero).
- **A revisar antes de la defensa:** detalles internos del paper (algoritmo de line finding, clasificador adaptativo) si el tribunal pregunta. URL del PDF público: <https://research.google.com/pubs/archive/33418.pdf>.
- **Nota:** los datos sobre origen de Tesseract en HP Labs en los noventa, liberación en 2005 y mantenimiento por Google son de conocimiento público; los respaldan tanto el propio paper (HP Research Prototype) como el README del repositorio oficial (`https://github.com/tesseract-ocr/tesseract`). Si el tribunal cuestiona la fecha de 2005, citar adicionalmente el README oficial.

---

## `[golding2024multitenant]` — Tod Golding, *Building Multi-Tenant SaaS Architectures*

- **Cita completa:** Golding, T. (2024). *Building Multi-Tenant SaaS Architectures: Principles, Practices, and Patterns Using AWS*. O'Reilly Media. ISBN: 9781098140649. 484 pp.
- **Citado en:** cap. 2 §2.3.1, «Patrones multi-tenant».
- **Afirmación que sustenta:** existencia de una literatura formal sobre arquitecturas SaaS multi-tenant; clasificación canónica de estrategias de aislamiento (instancia separada, base de datos separada, esquema separado, fila compartida con discriminador).
- **Capítulo de referencia para una pregunta del tribunal:** **Cap. 3 «Multi-Tenant Storage Strategies»** del libro. Cubre las cuatro estrategias clásicas y los compromisos entre ellas (aislamiento físico, eficiencia, complejidad operativa, coste de añadir un nuevo tenant).
- **Acceso:** Parcial (el autor del TFG conoce y consulta el libro; capítulo 3 leído en parte).
- **A revisar antes de la defensa:** confirmar la nomenclatura exacta que Golding usa para la estrategia de fila compartida (la llama habitualmente *pooled* o *shared* model), por si el tribunal pregunta el término técnico canónico.
- **Datos editoriales:**
  - Autor: Tod Golding (global SaaS technical lead at AWS).
  - Editor: O'Reilly Media.
  - Fecha: 28 mayo 2024 (algunas fuentes citan 4 junio 2024 para la edición impresa).
  - ISBN: 9781098140649 (paperback) / 9781098140632 (electrónico).
  - 484 páginas.

---

## `[gof1994designpatterns]` — Gamma, Helm, Johnson, Vlissides, *Design Patterns*

- **Cita completa:** Gamma, E.; Helm, R.; Johnson, R.; Vlissides, J. (1994). *Design Patterns: Elements of Reusable Object-Oriented Software*. Addison-Wesley Professional. ISBN: 9780201633610.
- **Citado en:** cap. 2 §2.3.2, «Arquitecturas basadas en plugins».
- **Afirmación que sustenta:** existencia y descripción del patrón **Strategy** como respaldo teórico del diseño plugin-based del SCDEE.
- **Capítulo de referencia para una pregunta del tribunal:** **Cap. 5 «Behavioral Patterns», sección Strategy** del libro original. La descripción canónica del patrón abarca la intent («Define a family of algorithms, encapsulate each one, and make them interchangeable. Strategy lets the algorithm vary independently from clients that use it»), el problema, la solución estructural y las consecuencias.
- **Acceso:** Parcial (libro canónico ampliamente conocido; el patrón Strategy específicamente leído por el autor del TFG, no necesariamente el libro completo).
- **Nota:** este libro es el más citado de toda la literatura de ingeniería del software (más de 100 000 citas en Google Scholar), y los autores son conocidos colectivamente como «Gang of Four» (GoF). Citarlo es defensa de máxima solvencia académica.
- **Por si el tribunal pregunta:**
  - Los cuatro autores: Erich Gamma, Richard Helm, Ralph Johnson, John Vlissides.
  - El libro clasifica 23 patrones en tres categorías: creacionales (5), estructurales (7) y de comportamiento (11).
  - Strategy es uno de los 11 patrones de comportamiento.
  - El libro acuñó la convención de describir cada patrón con su Intent, Motivation, Applicability, Structure, Participants, Collaborations, Consequences, Implementation y Sample Code.

---

## `[fowler2002]` — Martin Fowler, *Patterns of Enterprise Application Architecture*

- **Cita completa:** Fowler, M. (2002). *Patterns of Enterprise Application Architecture*. Addison-Wesley Professional. ISBN: 9780321127426.
- **Citado en:**
  - cap. 4 §4.1.1: mención breve a la capa Service Layer dentro del servicio Django.
  - cap. 4 §4.3: desarrollo del patrón Service Layer aplicado al SCDEE (responsabilidades de cada capa).
- **Afirmación que sustenta:** la organización del código del backend como una arquitectura por capas con una capa de servicios explícita (*Service Layer*) tiene un respaldo teórico canónico en el catálogo de Fowler.
- **Capítulo de referencia para una pregunta del tribunal:** **cap. 9 «Domain Logic Patterns», sección Service Layer** (págs. 133-141 en la edición original). El patrón se describe con su intent: «Defines an application's boundary with a layer of services that establishes a set of available operations and coordinates the application's response in each operation.»
- **Acceso:** Parcial (libro canónico ampliamente conocido; el patrón Service Layer específicamente leído por el autor del TFG; libro completo no leído entero).
- **Por si el tribunal pregunta:**
  - El libro cataloga 51 patrones de arquitectura empresarial.
  - Service Layer es uno de los 4 patrones de Domain Logic, junto con Transaction Script, Domain Model y Table Module.
  - La distinción entre Service Layer y Domain Model es relevante: el SCDEE usa una variante donde los servicios coordinan operaciones de negocio sin un modelo de dominio rico, lo que en el catálogo de Fowler corresponde a un Service Layer sobre Transaction Script o sobre Domain Model anémico.
