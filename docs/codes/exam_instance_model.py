# codes/exam_instance_model.py
# TODO: Sustituir por el modelo real de ExamInstance.
# Este fichero es un placeholder para el fragmento de código
# referenciado en el capítulo de implementación.

from django.db import models


class ExamInstance(models.Model):
    """Instancia de examen asociada a un alumno."""

    class State(models.TextChoices):
        CREATED = "created", "Creado"
        ASSIGNED = "assigned", "Asignado"
        IN_CORRECTION = "in_correction", "En corrección"
        CORRECTED = "corrected", "Corregido"
        PUBLISHED = "published", "Publicado"
        IN_REVIEW = "in_review", "En revisión"
        FINAL = "final", "Definitivo"
        ARCHIVED = "archived", "Archivado"

    state = models.CharField(
        max_length=20,
        choices=State.choices,
        default=State.CREATED,
    )
    version = models.PositiveIntegerField(default=0)
    # ... más campos ...
