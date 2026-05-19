"""Asignacion globalmente optima de lotes a alumnos."""

import numpy as np
from scipy.optimize import linear_sum_assignment


def assign_lots_to_students(lots, students, strategy, threshold=0.0):
    """Resuelve la asignacion 1-a-1 entre lotes y alumnos que maximiza
    la suma global de puntuaciones, mediante el algoritmo hungaro.

    Lotes cuyo mejor matching no alcance `threshold` se devuelven como
    no asignados (student_id=None). Garantiza biyeccion por construccion.
    """
    if not lots:
        return {}
    if not students:
        return {lot.key: LotAssignment(None, 0.0) for lot in lots}

    cost_matrix = _build_cost_matrix(lots, students, strategy)
    row_ind, col_ind = linear_sum_assignment(cost_matrix)

    out = {lot.key: LotAssignment(None, 0.0) for lot in lots}
    for i, j in zip(row_ind, col_ind):
        if i >= len(lots) or j >= len(students):
            continue  # celda de padding en la matriz cuadrada
        score = float(-cost_matrix[i, j])
        if score >= threshold:
            out[lots[i].key] = LotAssignment(students[j].student_id, score)
    return out


def _build_cost_matrix(lots, students, strategy):
    """Matriz cuadrada de costes para linear_sum_assignment.

    El algoritmo minimiza coste, asi que se niegan las puntuaciones.
    El padding con ceros gestiona el caso rectangular: las celdas de
    padding solo se eligen cuando no queda otra opcion.
    """
    n = max(len(lots), len(students))
    cost = np.zeros((n, n))
    for i, lot in enumerate(lots):
        for j, student in enumerate(students):
            score = strategy.score_lot(
                ocr_values=lot.ocr_values,
                true_name=student.name,
                true_nia=student.nia,
                true_dni=student.dni,
            )
            cost[i, j] = -score
    return cost
