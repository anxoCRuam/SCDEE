# codes/test_exam_state.py
# TODO: Sustituir por un test real de la máquina de estados.
# Placeholder para el fragmento de código del capítulo de pruebas.

import pytest
from exams.models import ExamInstance


class TestExamStateTransitions:
    """Pruebas unitarias de la máquina de estados del examen."""

    def test_created_to_assigned(self, exam_instance):
        """Una instancia creada puede transicionar a asignada."""
        assert exam_instance.state == ExamInstance.State.CREATED
        exam_instance.assign_correctors(correctors=[...])
        assert exam_instance.state == ExamInstance.State.ASSIGNED

    def test_invalid_transition_raises(self, exam_instance):
        """Una transición inválida lanza una excepción."""
        exam_instance.state = ExamInstance.State.CREATED
        with pytest.raises(ValueError):
            exam_instance.publish_grades()
