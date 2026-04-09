# TFG: Backend de Sistema de Corrección Digital de Exámenes Escritos (SCDEE/B)

**Autor:** Anxo Canay Reguera
**Tutor:** Eloy Anguiano Rey
**Grado:** Ingeniería Informática — EPS, UAM
**Plantilla LaTeX:** `tfgtfmthesisuam` (Eloy Anguiano Rey)

---

## Requisitos previos

1. Distribución LaTeX completa (TexLive 2024+ recomendado).
2. La clase `tfgtfmthesisuam.cls` y sus ficheros auxiliares instalados
   (en `TEXMFHOME` o en la misma carpeta del proyecto).
3. VS Code + LaTeX Workshop (receta configurada) o compilación manual.
4. Paquete `IEEEtran.bst` (incluido en TexLive por defecto).

## Estructura del proyecto

```
main.tex                          ← Documento principal
bibliography.bib                  ← Bibliografía BibTeX
preamble/
  acronyms.tex                    ← Definición de acrónimos (\ac{...})
  definitions.tex                 ← Definición de términos (\dfn{...})
frontmatter/
  resumen.tex                     ← Resumen en castellano + \palabrasclave
  abstract.tex                    ← Abstract en inglés + \keywords
  acknowledgements.tex            ← Agradecimientos
chapters/
  introduction/                   ← Cap. 1: Introducción
  state_of_art/                   ← Cap. 2: Estado del arte
  requirements/                   ← Cap. 3: Análisis de requisitos
  design/                         ← Cap. 4: Diseño del sistema
  implementation/                 ← Cap. 5: Implementación
  testing/                        ← Cap. 6: Pruebas y validación
  conclusions/                    ← Cap. 7: Conclusiones y trabajo futuro
appendices/
  global_requirements.tex         ← Ap. A: Requisitos globales
  deployment_manual.tex           ← Ap. B: Manual de despliegue
  detailed_results.tex            ← Ap. C: Resultados detallados
figures/                          ← Imágenes (PNG, PDF, JPG)
codes/                            ← Fragmentos de código fuente
data/                             ← Datos para gráficas (pgfplots)
```

## Compilación

### Con LaTeX Workshop (VS Code)

La receta `tfgtfmthesisuam` debe estar configurada en `settings.json`:
```
pdflatex → bibtex → makeglossaries → makeindex → pdflatex → pdflatex
```

### Manual (terminal)

```bash
pdflatex -shell-escape main
bibtex main
makeglossaries main
makeindex -s main.ist main
pdflatex -shell-escape main
pdflatex -shell-escape main
```

## Cómo trabajar

1. **Cada fichero `.tex` tiene comentarios `% GUÍA:`** que explican qué
   escribir en esa sección, con ejemplos y referencias a requisitos.
2. **Busca `TODO:`** en todos los ficheros para encontrar las tareas pendientes.
3. **Figuras:** Sustituye los PNG placeholder en `figures/` por los
   diagramas reales. Mantén los mismos nombres de fichero.
4. **Código:** Añade fragmentos reales de código en `codes/` y referéncialos
   con `\PythonCode`, `\Code`, etc.
5. **Bibliografía:** Añade entradas a `bibliography.bib` y cítalas con `\cite{}`.
6. **Acrónimos:** Añade nuevos en `preamble/acronyms.tex`.

## TODOs rápidos

```bash
grep -rn "TODO" --include="*.tex" --include="*.bib" .
```
