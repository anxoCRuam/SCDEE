"""
Instance, grading, and assignment views.

All endpoints for Fase 5 in one module since they're tightly coupled.

References: RF-7, RF-8, RF-11
"""

from __future__ import annotations

import logging

from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.audit.services import get_client_ip, log_event
from apps.core.openapi.openapi import ErrorCode, error_response
from apps.core.openapi.serializers import BinaryFileResponseSerializer
from apps.core.pagination import StandardPagination
from apps.instances.models import ExamInstance, ExamPage
from apps.instances.openapi import (
    MANUAL_GRADE_REQUEST_EXAMPLE,
    MANUAL_GRADE_RESPONSE_EXAMPLE,
    PUBLISH_RESPONSE_EXAMPLE,
    TRANSITION_REQUEST_EXAMPLE,
)
from apps.instances.serializers import (
    AssignmentRuleResponseSerializer,
    AssignmentRuleSerializer,
    AttachPageSerializer,
    CorrectorTaskSerializer,
    CoverageResponseSerializer,
    DetailResponseSerializer,
    GradeResponseSerializer,
    InstanceResponseSerializer,
    ManualGradeSerializer,
    MovePageSerializer,
    PageResponseSerializer,
    PublishResultSerializer,
    ReorderPagesSerializer,
    RubricGradeSerializer,
    TransitionSerializer,
    UpdateInstanceSerializer,
)
from apps.instances.services.instance_service import (
    InstanceServiceError,
    accept_extra_page,
    attach_orphan_page,
    bulk_publish,
    compose_instance_pdf,
    delete_instance,
    discard_page,
    move_page,
    reorder_pages,
    transition_instance,
    update_instance,
)

logger = logging.getLogger(__name__)


# ── Instance list/detail (RF-7.3, RF-7.4) ───────────────────


class InstanceListView(APIView):
    """List instances for an exam with filters (RF-7.4)."""

    permission_classes = [IsAuthenticated]
    serializer_class = InstanceResponseSerializer
    pagination_class = StandardPagination

    @extend_schema(
        tags=["Instances"],
        summary="List instances (RF-7.4)",
        parameters=[
            OpenApiParameter("status", str),
            OpenApiParameter("has_issues", bool),
            OpenApiParameter("model_id", str),
        ],
        responses={200: InstanceResponseSerializer(many=True)},
    )
    def get(self, request: Request, exam_pk: str) -> Response:
        from apps.exams.models import Exam

        try:
            exam = Exam.objects.get(pk=exam_pk)
        except Exam.DoesNotExist:
            return Response({"error_code": "NOT_FOUND"}, status=status.HTTP_404_NOT_FOUND)

        queryset = ExamInstance.objects.filter(exam=exam).select_related("student", "model")

        # Filters.
        if s := request.query_params.get("status"):
            queryset = queryset.filter(status=s)
        if hi := request.query_params.get("has_issues"):
            queryset = queryset.filter(has_issues=hi.lower() == "true")
        if mid := request.query_params.get("model_id"):
            queryset = queryset.filter(model_id=mid)

        for inst in queryset:
            inst.page_count = inst.pages.count()

        paginator = self.pagination_class()
        page = paginator.paginate_queryset(queryset, request)
        if page is not None:
            serializer = InstanceResponseSerializer(page, many=True)
            return paginator.get_paginated_response(serializer.data)

        serializer = InstanceResponseSerializer(queryset, many=True)
        return Response(serializer.data)


class InstanceDetailView(APIView):
    """Retrieve, update, delete an instance (RF-7.2, RF-7.3, RF-7.10)."""

    permission_classes = [IsAuthenticated]
    serializer_class = InstanceResponseSerializer

    @extend_schema(
        tags=["Instances"],
        summary="Instance detail (RF-7.3)",
        responses={200: InstanceResponseSerializer},
    )
    def get(self, request: Request, instance_pk: str) -> Response:
        try:
            inst = ExamInstance.objects.select_related("student", "model").get(pk=instance_pk)
        except ExamInstance.DoesNotExist:
            return Response({"error_code": "NOT_FOUND"}, status=status.HTTP_404_NOT_FOUND)

        inst.page_count = inst.pages.count()
        return Response(InstanceResponseSerializer(inst).data)

    @extend_schema(
        tags=["Instances"],
        request=UpdateInstanceSerializer,
        responses={200: InstanceResponseSerializer},
        summary="Update instance (RF-7.2)",
    )
    def patch(self, request: Request, instance_pk: str) -> Response:
        try:
            inst = ExamInstance.objects.select_related("exam").get(pk=instance_pk)
        except ExamInstance.DoesNotExist:
            return Response({"error_code": "NOT_FOUND"}, status=status.HTTP_404_NOT_FOUND)

        serializer = UpdateInstanceSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            changes = update_instance(inst, data=serializer.validated_data)
        except InstanceServiceError as exc:
            return Response({"error_code": exc.code}, status=status.HTTP_409_CONFLICT)

        if changes:
            log_event(
                event_type="INSTANCE_UPDATED",
                actor=request.user,
                organization=request.user.organization,
                entity=inst,
                ip_address=get_client_ip(request),
                payload={"changes": changes},
            )

        inst.page_count = inst.pages.count()
        return Response(InstanceResponseSerializer(inst).data)

    @extend_schema(
        tags=["Instances"],
        summary="Delete instance (RF-7.10)",
        responses={204: None},
    )
    def delete(self, request: Request, instance_pk: str) -> Response:
        try:
            inst = ExamInstance.objects.get(pk=instance_pk)
        except ExamInstance.DoesNotExist:
            return Response({"error_code": "NOT_FOUND"}, status=status.HTTP_404_NOT_FOUND)

        delete_instance(inst)
        return Response(status=status.HTTP_204_NO_CONTENT)


# ── State transition (RF-7.5) ───────────────────────────────


class TransitionView(APIView):
    """Execute a state transition (RF-7.5)."""

    permission_classes = [IsAuthenticated]
    serializer_class = TransitionSerializer

    @extend_schema(
        operation_id="instances_transition",
        tags=["Instances"],
        request=TransitionSerializer,
        summary="Move an instance through its state machine (RF-7.5)",
        description=(
            "**Advance (or roll back) the lifecycle state of one instance.**\n\n"
            "Allowed transitions are encoded in the service layer "
            "(``InstanceStatus`` machine). Common forward edges:\n"
            "- ``ASSEMBLING → RECEIVED`` once every expected page has arrived.\n"
            "- ``RECEIVED → PENDING_GRADING`` after recognition completes.\n"
            "- ``PENDING_GRADING → GRADED`` once every problem has a score.\n"
            "- ``GRADED → PUBLISHED`` (usually via the bulk-publish endpoint).\n\n"
            "Reverse transitions (RF-7.12) are also supported by the service "
            "for error recovery; the view delegates to the same code path "
            "and lets the service decide validity.\n\n"
            "**Output**\n\n"
            "The updated instance with its new ``status`` and a fresh "
            "``page_count``. An audit row ``INSTANCE_TRANSITION`` is added "
            "with the target status in the payload (RF-16.1)."
        ),
        responses={
            200: InstanceResponseSerializer,
            409: error_response(
                [ErrorCode.CONFLICT],
                status_code=409,
                description=(
                    "The requested transition is not allowed from the "
                    "instance's current state. The body carries the offending "
                    "``error_code``; the audit log records the attempted target."
                ),
            ),
        },
        examples=[TRANSITION_REQUEST_EXAMPLE],
    )
    def patch(self, request: Request, instance_pk: str) -> Response:
        try:
            inst = ExamInstance.objects.get(pk=instance_pk)
        except ExamInstance.DoesNotExist:
            return Response({"error_code": "NOT_FOUND"}, status=status.HTTP_404_NOT_FOUND)

        serializer = TransitionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            transition_instance(inst, serializer.validated_data["target_status"])
        except InstanceServiceError as exc:
            return Response({"error_code": exc.code}, status=status.HTTP_409_CONFLICT)

        log_event(
            event_type="INSTANCE_TRANSITION",
            actor=request.user,
            organization=request.user.organization,
            entity=inst,
            ip_address=get_client_ip(request),
            payload={"target_status": inst.status},
        )

        inst.page_count = inst.pages.count()
        return Response(InstanceResponseSerializer(inst).data)


# ── Page management (RF-7.14) ────────────────────────────────


class PageListView(APIView):
    """List pages of an instance."""

    permission_classes = [IsAuthenticated]
    serializer_class = PageResponseSerializer

    @extend_schema(
        tags=["Pages"],
        summary="List pages of instance",
        responses={200: PageResponseSerializer(many=True)},
    )
    def get(self, request: Request, instance_pk: str) -> Response:
        try:
            inst = ExamInstance.objects.get(pk=instance_pk)
        except ExamInstance.DoesNotExist:
            return Response({"error_code": "NOT_FOUND"}, status=status.HTTP_404_NOT_FOUND)

        pages = inst.pages.order_by("page_number")
        return Response(PageResponseSerializer(pages, many=True).data)


class PageReorderView(APIView):
    """Reorder pages (RF-7.14)."""

    permission_classes = [IsAuthenticated]
    serializer_class = ReorderPagesSerializer

    @extend_schema(
        tags=["Pages"],
        request=ReorderPagesSerializer,
        responses={200: DetailResponseSerializer},
        summary="Reorder pages",
    )
    def patch(self, request: Request, instance_pk: str) -> Response:
        try:
            inst = ExamInstance.objects.get(pk=instance_pk)
        except ExamInstance.DoesNotExist:
            return Response({"error_code": "NOT_FOUND"}, status=status.HTTP_404_NOT_FOUND)

        serializer = ReorderPagesSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        reorder_pages(inst, [str(p) for p in serializer.validated_data["page_ids"]])
        return Response({"detail": "Pages reordered."})


class PageDeleteView(APIView):
    """Discard a page (RF-7.14)."""

    permission_classes = [IsAuthenticated]

    @extend_schema(
        tags=["Pages"],
        summary="Discard page",
        responses={204: None},
    )
    def delete(self, request: Request, instance_pk: str, page_pk: str) -> Response:
        try:
            page = ExamPage.objects.get(pk=page_pk, instance_id=instance_pk)
        except ExamPage.DoesNotExist:
            return Response({"error_code": "NOT_FOUND"}, status=status.HTTP_404_NOT_FOUND)

        discard_page(page)
        return Response(status=status.HTTP_204_NO_CONTENT)


class PageMoveView(APIView):
    """Move page to another instance (RF-7.14)."""

    permission_classes = [IsAuthenticated]
    serializer_class = MovePageSerializer

    @extend_schema(
        tags=["Pages"],
        request=MovePageSerializer,
        responses={200: DetailResponseSerializer},
        summary="Move page",
    )
    def post(self, request: Request, instance_pk: str, page_pk: str) -> Response:
        try:
            page = ExamPage.objects.get(pk=page_pk, instance_id=instance_pk)
        except ExamPage.DoesNotExist:
            return Response({"error_code": "NOT_FOUND"}, status=status.HTTP_404_NOT_FOUND)

        serializer = MovePageSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            target = ExamInstance.objects.get(pk=serializer.validated_data["target_instance_id"])
        except ExamInstance.DoesNotExist:
            return Response({"error_code": "TARGET_NOT_FOUND"}, status=status.HTTP_404_NOT_FOUND)

        try:
            move_page(page, target)
        except InstanceServiceError as exc:
            return Response({"error_code": exc.code}, status=status.HTTP_409_CONFLICT)

        return Response({"detail": "Page moved."})


class PageAttachView(APIView):
    """Attach an orphan page (RF-7.14)."""

    permission_classes = [IsAuthenticated]
    serializer_class = AttachPageSerializer

    @extend_schema(
        tags=["Pages"],
        request=AttachPageSerializer,
        responses={200: DetailResponseSerializer},
        summary="Attach orphan page",
    )
    def post(self, request: Request, instance_pk: str) -> Response:
        try:
            inst = ExamInstance.objects.get(pk=instance_pk)
        except ExamInstance.DoesNotExist:
            return Response({"error_code": "NOT_FOUND"}, status=status.HTTP_404_NOT_FOUND)

        serializer = AttachPageSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            page = ExamPage.objects.get(
                pk=serializer.validated_data["page_id"], instance__isnull=True
            )
        except ExamPage.DoesNotExist:
            return Response({"error_code": "PAGE_NOT_ORPHAN"}, status=status.HTTP_404_NOT_FOUND)

        try:
            attach_orphan_page(page, inst)
        except InstanceServiceError as exc:
            return Response({"error_code": exc.code}, status=status.HTTP_409_CONFLICT)

        return Response({"detail": "Page attached."})


class PageAcceptExtraView(APIView):
    """Accept an extra page, clearing its issue (RF-7.14)."""

    permission_classes = [IsAuthenticated]
    serializer_class = DetailResponseSerializer

    @extend_schema(
        tags=["Pages"],
        request=None,
        summary="Accept extra page",
        responses={200: DetailResponseSerializer},
    )
    def patch(self, request: Request, instance_pk: str, page_pk: str) -> Response:
        try:
            page = ExamPage.objects.get(pk=page_pk, instance_id=instance_pk)
        except ExamPage.DoesNotExist:
            return Response({"error_code": "NOT_FOUND"}, status=status.HTTP_404_NOT_FOUND)

        accept_extra_page(page)
        return Response({"detail": "Page accepted."})


# ── Grading (RF-11.1, RF-11.2) ──────────────────────────────


class ManualGradeView(APIView):
    """Grade a problem manually (RF-11.1)."""

    permission_classes = [IsAuthenticated]

    @extend_schema(
        operation_id="instances_problems_grade_manual",
        tags=["Grading"],
        request=ManualGradeSerializer,
        summary="Manual grade with optimistic concurrency (RF-11.1)",
        description=(
            "**Assign or update the numeric grade of one problem on one instance.**\n\n"
            "Implements optimistic concurrency: the request must include the "
            "instance ``version`` the corrector last saw. If a *different* "
            "corrector touched the instance in between, the call is rejected "
            "with HTTP **409 ``CONFLICT``** and the caller is expected to "
            "re-read the instance, reconcile, and retry.\n\n"
            "**Process**\n\n"
            "1. Resolve the instance and problem; reject with ``404`` if "
            "either is missing in the caller's organisation.\n"
            "2. Compare ``score`` against the stored value. Mismatch → "
            "``409`` (no write).\n"
            "3. Persist the new ``score`` (decimal, may be negative — see "
            "RF-11.1) and bump ``version`` atomically.\n"
            "4. Append a ``GRADE_UPDATED`` row to the audit log with old "
            "and new values (RF-16.1).\n\n"
            "**Output**\n\n"
            "The persisted details."
        ),
        responses={
            200: GradeResponseSerializer,
            409: error_response(
                [ErrorCode.CONFLICT],
                status_code=409,
                description=(
                    "The instance was modified by another corrector since the "
                    "client read it. Re-fetch and retry with the updated "
                    "``score``."
                ),
            ),
        },
        examples=[MANUAL_GRADE_REQUEST_EXAMPLE, MANUAL_GRADE_RESPONSE_EXAMPLE],
    )
    def put(self, request: Request, instance_pk: str, problem_pk: str) -> Response:
        from apps.exams.models import Problem
        from apps.grading.services import GradingServiceError, grade_problem

        try:
            inst = ExamInstance.objects.get(pk=instance_pk)
            problem = Problem.objects.get(pk=problem_pk)
        except (ExamInstance.DoesNotExist, Problem.DoesNotExist):
            return Response({"error_code": "NOT_FOUND"}, status=status.HTTP_404_NOT_FOUND)

        serializer = ManualGradeSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            grade = grade_problem(
                instance=inst,
                problem=problem,
                score=serializer.validated_data["score"],
                grader=request.user,
                expected_score=serializer.validated_data["prev_score"],
            )
        except GradingServiceError as exc:
            return Response({"error_code": exc.code}, status=status.HTTP_409_CONFLICT)

        return Response(GradeResponseSerializer(grade).data)


class RubricGradeView(APIView):
    """Grade a problem by rubric (RF-11.2)."""

    permission_classes = [IsAuthenticated]
    serializer_class = RubricGradeSerializer

    @extend_schema(
        operation_id="instances_problems_grade_rubric",
        tags=["Grading"],
        request=RubricGradeSerializer,
        summary="Rubric grade with optimistic concurrency (RF-11.2)",
        description=(
            "**Calculate a problem's grade from a checklist of rubric criteria.**\n\n"
            "The corrector sends the IDs of the criteria they ticked; the "
            "service sums the criterion scores (positive *and* negative — "
            "negative criteria express penalties, see RF-11.2) and stores "
            "the result as the problem's grade.\n\n"
            "Optimistic-concurrency semantics are identical to the manual "
            "grade endpoint: ``score`` is checked, mismatch → ``409``."
        ),
        responses={
            200: GradeResponseSerializer,
            409: error_response(
                [ErrorCode.CONFLICT],
                status_code=409,
                description="Concurrent modification — re-fetch and retry.",
            ),
        },
    )
    def put(self, request: Request, instance_pk: str, problem_pk: str) -> Response:
        from apps.exams.models import Problem
        from apps.grading.services import GradingServiceError, grade_by_rubric

        try:
            inst = ExamInstance.objects.get(pk=instance_pk)
            problem = Problem.objects.get(pk=problem_pk)
        except (ExamInstance.DoesNotExist, Problem.DoesNotExist):
            return Response({"error_code": "NOT_FOUND"}, status=status.HTTP_404_NOT_FOUND)

        serializer = RubricGradeSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            grade = grade_by_rubric(
                instance=inst,
                problem=problem,
                criterion_ids=[str(c) for c in serializer.validated_data["criterion_ids"]],
                grader=request.user,
                expected_score=serializer.validated_data["prev_score"],
            )
        except GradingServiceError as exc:
            return Response({"error_code": exc.code}, status=status.HTTP_409_CONFLICT)

        grade.new_version = inst.version
        return Response(GradeResponseSerializer(grade).data)


# ── Assignment rules (RF-8.1 through RF-8.3) ────────────────


class AssignmentRuleListCreateView(APIView):
    """Create and list assignment rules (RF-8.1)."""

    permission_classes = [IsAuthenticated]

    @extend_schema(
        tags=["Assignments"],
        request=AssignmentRuleSerializer,
        responses={201: AssignmentRuleResponseSerializer},
        summary="Create rule (RF-8.1)",
    )
    def post(self, request: Request, exam_pk: str) -> Response:
        from apps.exams.models import Exam
        from apps.grading.services import create_assignment_rule

        try:
            exam = Exam.objects.get(pk=exam_pk)
        except Exam.DoesNotExist:
            return Response({"error_code": "NOT_FOUND"}, status=status.HTTP_404_NOT_FOUND)

        serializer = AssignmentRuleSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        data = serializer.validated_data
        rule = create_assignment_rule(
            exam=exam,
            groups=[str(g) for g in data.get("groups", [])],
            models_filter=[str(m) for m in data.get("models_filter", [])],
            problems=[str(p) for p in data["problems"]],
            correctors=[str(c) for c in data["correctors"]],
        )

        return Response(
            AssignmentRuleResponseSerializer(rule).data, status=status.HTTP_201_CREATED
        )

    @extend_schema(
        tags=["Assignments"],
        responses={200: AssignmentRuleResponseSerializer(many=True)},
        summary="List rules",
    )
    def get(self, request: Request, exam_pk: str) -> Response:
        from apps.grading.models import AssignmentRule

        rules = AssignmentRule.objects.filter(exam_id=exam_pk)
        return Response(AssignmentRuleResponseSerializer(rules, many=True).data)


class AssignmentRuleDeleteView(APIView):
    """Delete an assignment rule (RF-8.3)."""

    permission_classes = [IsAuthenticated]

    @extend_schema(
        tags=["Assignments"],
        summary="Delete rule (RF-8.3)",
        responses={204: None},
    )
    def delete(self, request: Request, rule_pk: str) -> Response:
        from apps.grading.models import AssignmentRule
        from apps.grading.services import delete_assignment_rule

        try:
            rule = AssignmentRule.objects.get(pk=rule_pk)
        except AssignmentRule.DoesNotExist:
            return Response({"error_code": "NOT_FOUND"}, status=status.HTTP_404_NOT_FOUND)

        delete_assignment_rule(rule)
        return Response(status=status.HTTP_204_NO_CONTENT)


# ── Coverage check (RF-8.5) ─────────────────────────────────


class CoverageView(APIView):
    """Check corrector coverage for an exam (RF-8.5)."""

    permission_classes = [IsAuthenticated]
    serializer_class = CoverageResponseSerializer

    @extend_schema(
        tags=["Assignments"],
        summary="Coverage check (RF-8.5)",
        responses={200: CoverageResponseSerializer},
    )
    def get(self, request: Request, exam_pk: str) -> Response:
        from apps.exams.models import Exam
        from apps.grading.services import check_coverage

        try:
            exam = Exam.objects.get(pk=exam_pk)
        except Exam.DoesNotExist:
            return Response({"error_code": "NOT_FOUND"}, status=status.HTTP_404_NOT_FOUND)

        result = check_coverage(exam)
        return Response(result)


# ── My tasks (RF-8.6) ───────────────────────────────────────


class MyTasksView(APIView):
    """List grading tasks for the authenticated corrector (RF-8.6)."""

    permission_classes = [IsAuthenticated]
    serializer_class = CorrectorTaskSerializer

    @extend_schema(
        tags=["Grading"],
        summary="My grading tasks (RF-8.6)",
        responses={200: CorrectorTaskSerializer(many=True)},
    )
    def get(self, request: Request) -> Response:
        from apps.grading.services import get_corrector_tasks

        tasks = get_corrector_tasks(request.user)
        return Response(tasks)


# ── Bulk publish (RF-7.9) ────────────────────────────────────


class PublishView(APIView):
    """Bulk publish grades for an exam (RF-7.9)."""

    permission_classes = [IsAuthenticated]
    serializer_class = PublishResultSerializer

    @extend_schema(
        operation_id="exams_publish_grades",
        tags=["Instances"],
        summary="Bulk-publish all graded instances of an exam (RF-7.9)",
        description=(
            "**Move every ``GRADED`` instance of the exam to ``PUBLISHED`` and "
            "(optionally) notify the affected students.**\n\n"
            "**Process**\n\n"
            "1. Iterate over every instance of the exam.\n"
            "2. Skip those still in ``PENDING_GRADING`` and those with "
            "``has_issues=True`` — the response reports the count and "
            "reason in ``skipped_reasons`` so the coordinator knows what's "
            "left to do.\n"
            "3. Transition the remaining instances to ``PUBLISHED`` "
            "atomically (one transaction per instance).\n"
            "4. Enqueue ``GRADES_PUBLISHED`` notifications (in-app + email "
            "mirror) for the students whose instance was published, "
            "subject to their ``email_notifications_enabled`` preference "
            "(RF-2.11, RF-13.1).\n"
            "5. Append a ``GRADES_PUBLISHED`` audit row with the totals.\n\n"
            "**Idempotency**\n\n"
            "Already-``PUBLISHED`` instances are counted under ``skipped`` "
            "(reason ``ALREADY_PUBLISHED``) and not re-notified. Calling the "
            "endpoint twice is therefore safe.\n\n"
            "**Output**\n\n"
            "An aggregate report with the totals and the reason breakdown "
            "for the skips."
        ),
        request=None,
        responses={
            200: PublishResultSerializer,
        },
        examples=[PUBLISH_RESPONSE_EXAMPLE],
    )
    def post(self, request: Request, exam_pk: str) -> Response:
        from apps.exams.models import Exam

        try:
            exam = Exam.objects.get(pk=exam_pk)
        except Exam.DoesNotExist:
            return Response({"error_code": "NOT_FOUND"}, status=status.HTTP_404_NOT_FOUND)

        result = bulk_publish(exam)

        log_event(
            event_type="GRADES_PUBLISHED",
            actor=request.user,
            organization=request.user.organization,
            ip_address=get_client_ip(request),
            payload=result,
        )

        return Response(result)


# ── PDF download (RF-7.15) ──────────────────────────────────


class InstanceDownloadView(APIView):
    """Download composed PDF of instance (RF-7.11, RF-7.15, RF-16.6, RF-16.7)."""

    permission_classes = [IsAuthenticated]
    serializer_class = BinaryFileResponseSerializer

    @extend_schema(
        tags=["Instances"],
        summary="Download instance PDF (RF-7.15)",
        responses={
            (200, "application/pdf"): {
                "type": "string",
                "format": "binary",
                "description": "Composed PDF of all the instance's pages.",
            }
        },
    )
    def get(self, request: Request, instance_pk: str) -> Response:
        from django.http import HttpResponse as DjangoHttpResponse

        try:
            inst = ExamInstance.objects.get(pk=instance_pk)
        except ExamInstance.DoesNotExist:
            return Response({"error_code": "NOT_FOUND"}, status=status.HTTP_404_NOT_FOUND)

        # The student of the instance is the only role that gets a watermark
        # (RF-16.6). Managers, coordinators and graders see the raw PDF.
        is_student_owner = (
            inst.student_id is not None
            and inst.student_id == request.user.pk
            and not request.user.is_staff
        )

        try:
            pdf_bytes = compose_instance_pdf(
                inst,
                watermark_for_student=is_student_owner,
            )
        except InstanceServiceError as exc:
            return Response({"error_code": exc.code}, status=status.HTTP_409_CONFLICT)

        response = DjangoHttpResponse(pdf_bytes, content_type="application/pdf")
        # RF-16.7: students view inline (no download); other roles can save.
        disposition = "inline" if is_student_owner else "attachment"
        response["Content-Disposition"] = f'{disposition}; filename="instance_{inst.pk}.pdf"'
        # Anti-cache headers for student access (defense in depth alongside
        # the global AntiCacheMiddleware, which may be bypassed if disabled).
        if is_student_owner:
            response["Cache-Control"] = "no-store, no-cache, must-revalidate, private"
            response["Pragma"] = "no-cache"
            response["Expires"] = "0"
        return response
