"""
Grading and assignment models.

AssignmentRule: Defines which correctors grade which problems for
which groups/models. Rules are applied to generate concrete assignments.

Grade: A corrector's score for a specific problem on a specific instance.
Uses optimistic concurrency (version field on ExamInstance) to handle
concurrent grading.

References: RF-8.1 through RF-8.6, RF-11.1 through RF-11.6
"""

from django.conf import settings
from django.db import models

from apps.core.models import TimestampedModel


class AssignmentRule(TimestampedModel):
    """Rule defining corrector assignments for an exam.

    Each rule specifies:
    - Which exam it applies to.
    - Which groups to filter (empty = all groups).
    - Which models to filter (empty = all models).
    - Which problems to assign.
    - Which correctors (teachers) handle those problems.

    Multiple rules can exist per exam. The assignment service
    applies all rules to generate per-instance, per-problem
    corrector assignments.

    Attributes:
        exam: FK to the exam.
        groups: JSON list of SubjectGroup IDs (empty = all).
        models_filter: JSON list of ExamModel IDs (empty = all).
        problems: JSON list of Problem IDs to assign.
        correctors: JSON list of User IDs (teachers) as correctors.
    """

    exam = models.ForeignKey(
        "exams.Exam",
        on_delete=models.CASCADE,
        related_name="assignment_rules",
    )
    groups = models.JSONField(
        default=list,
        blank=True,
        help_text="SubjectGroup IDs to filter. Empty = all groups.",
    )
    models_filter = models.JSONField(
        default=list,
        blank=True,
        help_text="ExamModel IDs to filter. Empty = all models.",
    )
    problems = models.JSONField(
        default=list,
        help_text="Problem IDs this rule assigns correctors to.",
    )
    correctors = models.JSONField(
        default=list,
        help_text="User IDs of correctors assigned by this rule.",
    )

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"Rule for {self.exam.name} ({len(self.correctors)} correctors)"


class Grade(TimestampedModel):
    """A corrector's score for a problem on an instance.

    Each grade represents the score given by a specific corrector
    for a specific problem on a specific instance.

    Concurrency: the view checks ExamInstance.version before saving
    to detect concurrent edits (optimistic locking).

    Attributes:
        problem: FK to the problem being graded.
        instance: FK to the exam instance.
        score: The awarded score (positive, negative, or zero).
        grader: FK to the user who assigned this grade.
        rubric_selections: JSON list of selected RubricCriterion IDs
            (if graded by rubric). Empty for manual grading.
    """

    problem = models.ForeignKey(
        "exams.Problem",
        on_delete=models.CASCADE,
        related_name="grades",
    )
    instance = models.ForeignKey(
        "instances.ExamInstance",
        on_delete=models.CASCADE,
        related_name="grades",
    )
    score = models.DecimalField(
        max_digits=8,
        decimal_places=2,
    )
    grader = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="grades_given",
    )
    rubric_selections = models.JSONField(
        default=list,
        blank=True,
        help_text="List of selected RubricCriterion IDs (rubric-based grading).",
    )

    class Meta:
        constraints = [
            # One grade per problem per instance.
            models.UniqueConstraint(
                fields=["problem", "instance"],
                name="unique_grade_per_problem_instance",
            ),
        ]
        ordering = ["problem__order"]

    def __str__(self) -> str:
        return f"Grade {self.score} for {self.problem.name}"
