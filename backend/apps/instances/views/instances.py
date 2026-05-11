"""
Instance, grading, and assignment views.

All endpoints for Fase 5 in one module since they're tightly coupled.

References: RF-7, RF-8, RF-11
"""

from __future__ import annotations

import logging

from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, extend_schema
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.models.permissions import IsOrgManager
from apps.audit.services.auditlog import get_client_ip, log_event
from apps.core.openapi.errors import ErrorCode, error_response
from apps.core.openapi.serializers import BinaryFileResponseSerializer
from apps.core.utils.pagination import StandardPagination
from apps.grading.models.grading import AssignmentRule, Grade
from apps.grading.services.grading import can_user_grade_instance, instance_matches_rule
from apps.instances.models.instances import ExamInstance, ExamPage, InstanceStatus
from apps.instances.serializers.instances import (
    MANAGER_INSTANCE_EXAMPLE,
    MANUAL_GRADE_REQUEST_EXAMPLE,
    MANUAL_GRADE_RESPONSE_EXAMPLE,
    PUBLISH_RESPONSE_EXAMPLE,
    STUDENT_INSTANCE_AFTER_REVIEW_EXAMPLE,
    STUDENT_INSTANCE_BASIC_EXAMPLE,
    STUDENT_INSTANCE_DETAILED_EXAMPLE,
    TRANSITION_REQUEST_EXAMPLE,
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
    MyTasksResponseSerializer,
    PageResponseSerializer,
    PublishResultSerializer,
    ReorderPagesSerializer,
    RubricGradeSerializer,
    StudentInstanceSerializer,
    TransitionSerializer,
    UpdateInstanceSerializer,
)
from apps.instances.services.access_control import can_access_instance_data
from apps.instances.services.instance_service import (
    InstanceServiceError,
    accept_extra_page,
    attach_orphan_page,
    bulk_publish,
    compose_instance_pdf,
    create_instance,
    delete_instance,
    discard_page,
    move_page,
    reorder_pages,
    transition_instance,
    update_instance,
)
from apps.reviews.services.reviews import get_pending_reviews_for_corrector
from apps.subjects.models.permissions import MembershipRole, SubjectPermission
from apps.subjects.models.subjects import SubjectMembership
from apps.subjects.services.permissions import has_subject_permission

logger = logging.getLogger(__name__)


class InstanceCreateView(APIView):
    """Manual instance creation (manager only)."""

    permission_classes = [IsOrgManager]

    @extend_schema(
        tags=["Instances"],
        request=UpdateInstanceSerializer,
        responses={201: InstanceResponseSerializer},
        summary="Create a new exam instance manually",
    )
    def post(self, request: Request, exam_pk: str) -> Response:
        from apps.exams.models.exams import Exam

        try:
            exam = Exam.objects.get(pk=exam_pk)
        except Exam.DoesNotExist:
            return Response({"error_code": "NOT_FOUND"}, status=status.HTTP_404_NOT_FOUND)

        serializer = UpdateInstanceSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        instance = create_instance(
            exam=exam,
            organization=request.user.organization,
            student_id=str(serializer.validated_data.get("student_id"))
            if serializer.validated_data.get("student_id")
            else None,
            model_id=str(serializer.validated_data.get("model_id"))
            if serializer.validated_data.get("model_id")
            else None,
            expected_pages=0,
        )
        return Response(InstanceResponseSerializer(instance).data, status=status.HTTP_201_CREATED)


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
            OpenApiParameter("page", int),
            OpenApiParameter("page_size", int),
        ],
        responses={
            200: OpenApiResponse(
                response=InstanceResponseSerializer(many=True),
                examples=[
                    MANAGER_INSTANCE_EXAMPLE,
                    STUDENT_INSTANCE_BASIC_EXAMPLE,
                ],
            ),
        },
    )
    def get(self, request: Request, exam_pk: str) -> Response:
        from apps.exams.models.exams import Exam

        try:
            exam = Exam.objects.get(pk=exam_pk)
        except Exam.DoesNotExist:
            return Response({"error_code": "NOT_FOUND"}, status=status.HTTP_404_NOT_FOUND)

        user = request.user

        if has_subject_permission(request.user, exam.subject, "can_view_all_instances"):
            queryset = ExamInstance.objects.filter(exam=exam)
        else:
            membership = SubjectMembership.objects.filter(
                user=request.user, subject=exam.subject, is_active=True
            ).first()
            if membership and membership.role in (
                MembershipRole.COORDINATOR,
                MembershipRole.TEACHER,
            ):
                all_instances = list(ExamInstance.objects.filter(exam=exam))
                rules = list(AssignmentRule.objects.filter(exam=exam))
                allowed_ids = []
                for inst in all_instances:
                    for rule in rules:
                        if instance_matches_rule(inst, rule, user):
                            allowed_ids.append(inst.pk)
                            break
                queryset = ExamInstance.objects.filter(pk__in=allowed_ids)
            elif membership and membership.role == MembershipRole.STUDENT:
                try:
                    instance = ExamInstance.objects.get(exam=exam, student=user)
                except ExamInstance.DoesNotExist:
                    return Response([])  # o 200 con lista vacía

                if not can_access_instance_data(instance, user, None):
                    return Response([])

                serializer = StudentInstanceSerializer(instance, context={"request": request})
                return Response([serializer.data])

            else:
                return Response(
                    {"error_code": "PERMISSION_DENIED"}, status=status.HTTP_403_FORBIDDEN
                )

        # Filters.
        if s := request.query_params.get("status"):
            queryset = queryset.filter(status=s)
        if hi := request.query_params.get("has_issues"):
            queryset = queryset.filter(has_issues=hi.lower() == "true")
        if mid := request.query_params.get("model_id"):
            queryset = queryset.filter(model_id=mid)

        queryset = queryset.select_related("student", "model").prefetch_related("pages")

        # Pagination.
        paginator = self.pagination_class()
        page = paginator.paginate_queryset(queryset, request)

        for inst in page:
            pages = list(inst.pages.all())
            inst.page_ids = [p.pk for p in pages]
        return paginator.get_paginated_response(InstanceResponseSerializer(page, many=True).data)


class InstanceDetailView(APIView):
    """Retrieve, update, delete an instance (RF-7.2, RF-7.3, RF-7.10)."""

    permission_classes = [IsAuthenticated]
    serializer_class = InstanceResponseSerializer

    @extend_schema(
        tags=["Instances"],
        summary="Instance detail (RF-7.3) – response shape depends on user role and review window",
        description=(
            "**Return an exam instance with role-dependent detail level.**\n\n"
            "- **Staff / superadmins** → full response (``InstanceResponseSerializer``) "
            "including all metadata, page count, and scores.\n"
            "- **Teachers / coordinators** → same full response, provided they have "
            "``can_view_all_instances`` or are assigned to this instance as correctors.\n"
            "- **Students** → see their own instance only. The response uses "
            "``StudentInstanceSerializer`` and the *content* varies with the "
            "exam's review window and ``student_permissions``:\n"
            "  - **No active / upcoming / expired review** → basic fields + total_score.\n"
            "  - **Review is OPEN and instance is IN_REVIEW** → detailed view (pages, "
            "annotations, rubric, grades) for every permission enabled in "
            "``student_permissions``.\n"
            "  - **Review CLOSED / COMPLETED with ``can_view_pages_after_review=True``** → "
            "same detailed view (but review requests are no longer accepted).\n\n"
        ),
        responses={
            200: OpenApiResponse(
                response=InstanceResponseSerializer,
                examples=[
                    MANAGER_INSTANCE_EXAMPLE,
                    STUDENT_INSTANCE_BASIC_EXAMPLE,
                    STUDENT_INSTANCE_DETAILED_EXAMPLE,
                    STUDENT_INSTANCE_AFTER_REVIEW_EXAMPLE,
                ],
                description="See the individual example descriptions for details.",
            ),
        },
    )
    def get(self, request: Request, instance_pk: str) -> Response:
        try:
            inst = (
                ExamInstance.objects.select_related("student", "model", "exam__subject")
                .prefetch_related("pages")
                .get(pk=instance_pk)
            )
        except ExamInstance.DoesNotExist:
            return Response({"error_code": "NOT_FOUND"}, status=status.HTTP_404_NOT_FOUND)

        user = request.user

        # Check basic visibility.
        if not can_access_instance_data(inst, user, None):
            return Response(
                {"error_code": "PERMISSION_DENIED"},
                status=status.HTTP_403_FORBIDDEN,
            )

        # Choose serializer based on role.
        if user.is_staff or getattr(user, "is_superadmin", False):
            serializer = InstanceResponseSerializer(inst)
        elif inst.student_id == user.pk:
            # Student: use conditional serializer.
            serializer = StudentInstanceSerializer(inst, context={"request": request})
        else:
            serializer = InstanceResponseSerializer(inst)

        return Response(serializer.data)

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

        if not can_user_grade_instance(request.user, inst, None):
            return Response(
                {"error_code": "PERMISSION_DENIED"},
                status=status.HTTP_403_FORBIDDEN,
            )

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

        if not has_subject_permission(request.user, inst.exam.subject, "can_view_all_instances"):
            return Response({"error_code": "PERMISSION_DENIED"}, status=status.HTTP_403_FORBIDDEN)

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
            "- **``PENDING_GRADING → GRADED`` once every problem has a score.**\n"
            "- ``GRADED → PUBLISHED`` (usually via the bulk-publish endpoint).\n\n"
            "Reverse transitions (RF-7.12) are also supported by the service "
            "for error recovery; the view delegates to the same code path "
            "and lets the service decide validity.\n\n"
            "**Output**\n\n"
            "The updated instance with its new ``status`` and a fresh "
            "``page_count``. An audit row ``INSTANCE_TRANSITION`` is added "
            "with the target status in the payload (RF-16.1).\n\n"
            "The above applies if you have 'can_view_all_instances'. Otherwise, for a teacher "
            "who has the instance to grade, you can transition from PENDING_GRADING to GRADED "
            "and vice versa."
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
        target_status = serializer.validated_data["target_status"]

        if not has_subject_permission(request.user, inst.exam.subject, "can_view_all_instances"):  # noqa: SIM102
            if not can_user_grade_instance(request.user, inst, None) or (
                target_status != InstanceStatus.GRADED
                and target_status != InstanceStatus.PENDING_GRADING
            ):
                return Response(
                    {"error_code": "PERMISSION_DENIED"},
                    status=status.HTTP_403_FORBIDDEN,
                )
        try:
            transition_instance(inst, target_status)
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

        if not has_subject_permission(
            request.user, inst.exam.subject, "can_resolve_issues"
        ) and not can_user_grade_instance(request.user, inst, None):
            return Response({"error_code": "PERMISSION_DENIED"}, status=status.HTTP_403_FORBIDDEN)

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
        summary="Reorder pages ('can_resolve_issues' or assigned required)",
    )
    def patch(self, request: Request, instance_pk: str) -> Response:
        try:
            inst = ExamInstance.objects.get(pk=instance_pk)
        except ExamInstance.DoesNotExist:
            return Response({"error_code": "NOT_FOUND"}, status=status.HTTP_404_NOT_FOUND)

        if not has_subject_permission(
            request.user, inst.exam.subject, "can_resolve_issues"
        ) and not can_user_grade_instance(request.user, inst, None):
            return Response({"error_code": "PERMISSION_DENIED"}, status=status.HTTP_403_FORBIDDEN)

        serializer = ReorderPagesSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        reorder_pages(inst, [str(p) for p in serializer.validated_data["page_ids"]])
        return Response({"detail": "Pages reordered."})


class PageDeleteView(APIView):
    """Discard a page (RF-7.14)."""

    permission_classes = [IsAuthenticated]

    @extend_schema(
        tags=["Pages"],
        summary="Discard page ('can_resolve_issues' or assigned required)",
        responses={204: None},
    )
    def delete(self, request: Request, instance_pk: str, page_pk: str) -> Response:
        try:
            page = ExamPage.objects.get(pk=page_pk, instance_id=instance_pk)
        except ExamPage.DoesNotExist:
            return Response({"error_code": "NOT_FOUND"}, status=status.HTTP_404_NOT_FOUND)

        if not has_subject_permission(
            request.user, page.instance.exam.subject, "can_resolve_issues"
        ) and not can_user_grade_instance(request.user, page.instance, None):
            return Response({"error_code": "PERMISSION_DENIED"}, status=status.HTTP_403_FORBIDDEN)

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
        summary="Move page ('can_resolve_issues')",
    )
    def post(self, request: Request, instance_pk: str, page_pk: str) -> Response:
        try:
            page = ExamPage.objects.get(pk=page_pk, instance_id=instance_pk)
        except ExamPage.DoesNotExist:
            return Response({"error_code": "NOT_FOUND"}, status=status.HTTP_404_NOT_FOUND)

        if not has_subject_permission(
            request.user, page.instance.exam.subject, "can_resolve_issues"
        ):
            return Response({"error_code": "PERMISSION_DENIED"}, status=status.HTTP_403_FORBIDDEN)

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

        if not has_subject_permission(request.user, inst.exam.subject, "can_resolve_issues"):
            return Response({"error_code": "PERMISSION_DENIED"}, status=status.HTTP_403_FORBIDDEN)

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
        summary="Accept extra page('can_resolve_issues' or assigned required)",
        responses={200: DetailResponseSerializer},
    )
    def patch(self, request: Request, instance_pk: str, page_pk: str) -> Response:
        try:
            page = ExamPage.objects.get(pk=page_pk, instance_id=instance_pk)
        except ExamPage.DoesNotExist:
            return Response({"error_code": "NOT_FOUND"}, status=status.HTTP_404_NOT_FOUND)

        if not has_subject_permission(
            request.user, page.instance.exam.subject, "can_resolve_issues"
        ) and not can_user_grade_instance(request.user, page.instance, None):
            return Response({"error_code": "PERMISSION_DENIED"}, status=status.HTTP_403_FORBIDDEN)

        accept_extra_page(page)
        return Response({"detail": "Page accepted."})


class OrphanPageListView(APIView):
    """List orphan pages for the manager's organization (RF-7.14)."""

    permission_classes = [IsOrgManager]

    @extend_schema(
        tags=["Pages"],
        summary="List orphan pages in the organization",
        responses={200: PageResponseSerializer(many=True)},
    )
    def get(self, request: Request) -> Response:
        pages = ExamPage.objects.filter(
            instance__isnull=True,
            organization=request.user.organization,
        ).order_by("-created_at")
        return Response(PageResponseSerializer(pages, many=True).data)


class OrphanPageAttachView(APIView):
    """Attach an orphan page to a specific instance."""

    permission_classes = [IsOrgManager]

    @extend_schema(
        tags=["Pages"],
        request=AttachPageSerializer,
        responses={200: DetailResponseSerializer},
        summary="Attach an orphan page to an instance",
    )
    def post(self, request: Request, page_pk: str) -> Response:
        try:
            page = ExamPage.objects.get(pk=page_pk, instance__isnull=True)
        except ExamPage.DoesNotExist:
            return Response({"error_code": "PAGE_NOT_ORPHAN"}, status=status.HTTP_404_NOT_FOUND)

        serializer = AttachPageSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            target = ExamInstance.objects.get(pk=serializer.validated_data["instance_id"])
        except ExamInstance.DoesNotExist:
            return Response({"error_code": "TARGET_NOT_FOUND"}, status=status.HTTP_404_NOT_FOUND)
        try:
            attach_orphan_page(page, target)
        except InstanceServiceError as exc:
            return Response({"error_code": exc.code}, status=status.HTTP_409_CONFLICT)

        return Response({"detail": "Page attached."})


# ── Grading (RF-11.1, RF-11.2) ──────────────────────────────


class ManualGradeView(APIView):
    """Grade a problem manually (RF-11.1)."""

    permission_classes = [IsAuthenticated]

    @extend_schema(
        operation_id="instances_problems_grade_manual",
        tags=["Grading"],
        request=ManualGradeSerializer,
        summary="Manual grade with optimistic concurrency (RF-11.1) ",
        description=(
            "**Assign or update the numeric grade of one problem on one instance.**\n\n"
            "Implements optimistic concurrency: the request must include the "
            "instance ``old_score`` the corrector last saw. If a *different* "
            "corrector touched the instance in between, the call is rejected "
            "with HTTP **409 ``CONFLICT``** and the caller is expected to "
            "re-read the instance, reconcile, and retry.\n\n"
            "**Process**\n\n"
            "1. Resolve the instance and problem; reject with ``404`` if "
            "either is missing in the caller's organisation.\n"
            "2. Compare ``old_score`` against the stored value. Mismatch → "
            "``409`` (no write).\n"
            "3. Persist the new ``score`` (decimal, may be negative — see "
            "RF-11.1).\n"
            "4. Append a ``GRADE_UPDATED`` row to the audit log with old "
            "and new values (RF-16.1).\n\n"
            "**Output**\n\n"
            "The persisted grade."
        ),
        responses={
            200: GradeResponseSerializer,
            409: error_response(
                [ErrorCode.CONFLICT],
                status_code=409,
                description=(
                    "The instance was modified by another corrector since the client read it. "
                    "The user must be explicitly authorized by an AssignmentRule",
                ),
            ),
        },
        examples=[MANUAL_GRADE_REQUEST_EXAMPLE, MANUAL_GRADE_RESPONSE_EXAMPLE],
    )
    def put(self, request: Request, instance_pk: str, problem_pk: str) -> Response:
        from apps.exams.models.exams import Problem
        from apps.grading.services.grading import GradingServiceError, grade_problem

        try:
            inst = ExamInstance.objects.get(pk=instance_pk)
            problem = Problem.objects.get(pk=problem_pk)
        except (ExamInstance.DoesNotExist, Problem.DoesNotExist):
            return Response({"error_code": "NOT_FOUND"}, status=status.HTTP_404_NOT_FOUND)

        if not can_user_grade_instance(request.user, inst, problem):
            return Response({"error_code": "PERMISSION_DENIED"}, status=status.HTTP_403_FORBIDDEN)

        serializer = ManualGradeSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            old_grade = Grade.objects.filter(instance=inst, problem=problem).first()
            old_score = str(old_grade.score) if old_grade else None

            grade = grade_problem(
                instance=inst,
                problem=problem,
                score=serializer.validated_data["score"],
                grader=request.user,
                old_score=serializer.validated_data.get("old_score"),
            )

            log_event(
                event_type="GRADE_UPDATED",
                actor=request.user,
                organization=request.user.organization,
                entity=inst,
                ip_address=get_client_ip(request),
                payload={
                    "instance_id": str(inst.pk),
                    "problem_id": str(problem.pk),
                    "problem_name": problem.name,
                    "old_score": old_score,
                    "new_score": str(grade.score),
                },
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
            "grade endpoint: ``old_score`` is checked, mismatch → ``409``."
        ),
        responses={
            200: GradeResponseSerializer,
            409: error_response(
                [ErrorCode.CONFLICT],
                status_code=409,
                description="Concurrent modification — re-fetch and retry. "
                "The user must be explicitly authorized by an AssignmentRule",
            ),
        },
    )
    def put(self, request: Request, instance_pk: str, problem_pk: str) -> Response:
        from apps.exams.models.exams import Problem
        from apps.grading.services.grading import GradingServiceError, grade_by_rubric

        try:
            inst = ExamInstance.objects.get(pk=instance_pk)
            problem = Problem.objects.get(pk=problem_pk)
        except (ExamInstance.DoesNotExist, Problem.DoesNotExist):
            return Response({"error_code": "NOT_FOUND"}, status=status.HTTP_404_NOT_FOUND)

        if not can_user_grade_instance(request.user, inst, problem):
            return Response({"error_code": "PERMISSION_DENIED"}, status=status.HTTP_403_FORBIDDEN)

        serializer = RubricGradeSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            old_grade = Grade.objects.filter(instance=inst, problem=problem).first()
            old_score = str(old_grade.score) if old_grade else None

            grade = grade_by_rubric(
                instance=inst,
                problem=problem,
                criterion_ids=[str(c) for c in serializer.validated_data["criterion_ids"]],
                grader=request.user,
                old_score=serializer.validated_data.get("old_score"),
            )

            log_event(
                event_type="GRADE_UPDATED",
                actor=request.user,
                organization=request.user.organization,
                entity=inst,
                ip_address=get_client_ip(request),
                payload={
                    "instance_id": str(inst.pk),
                    "problem_id": str(problem.pk),
                    "problem_name": problem.name,
                    "old_score": old_score,
                    "new_score": str(grade.score),
                },
            )
        except GradingServiceError as exc:
            return Response({"error_code": exc.code}, status=status.HTTP_409_CONFLICT)

        return Response(GradeResponseSerializer(grade).data)


# ── Assignment rules (RF-8.1 through RF-8.3) ────────────────


class AssignmentRuleListCreateView(APIView):
    """Create and list assignment rules (RF-8.1)."""

    permission_classes = [IsAuthenticated, SubjectPermission("can_assign_correctors")]

    @extend_schema(
        tags=["Grading"],
        request=AssignmentRuleSerializer,
        responses={201: AssignmentRuleResponseSerializer},
        summary="Create rule (RF-8.1) 'can_assign_correctors' required",
    )
    def post(self, request: Request, exam_pk: str) -> Response:
        from apps.exams.models.exams import Exam
        from apps.grading.services.grading import create_assignment_rule

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
        tags=["Grading"],
        responses={200: AssignmentRuleResponseSerializer(many=True)},
        summary="List rules 'can_assign_correctors' required",
    )
    def get(self, request: Request, exam_pk: str) -> Response:
        from apps.grading.models.grading import AssignmentRule

        rules = AssignmentRule.objects.filter(exam_id=exam_pk)
        return Response(AssignmentRuleResponseSerializer(rules, many=True).data)


class AssignmentRuleDeleteView(APIView):
    """Delete an assignment rule (RF-8.3)."""

    permission_classes = [IsAuthenticated, SubjectPermission("can_assign_correctors")]

    @extend_schema(
        tags=["Grading"],
        summary="Delete rule (RF-8.3) 'can_assign_correctors' required",
        responses={204: None},
    )
    def delete(self, request: Request, rule_pk: str) -> Response:
        from apps.grading.models.grading import AssignmentRule
        from apps.grading.services.grading import delete_assignment_rule

        try:
            rule = AssignmentRule.objects.get(pk=rule_pk)
        except AssignmentRule.DoesNotExist:
            return Response({"error_code": "NOT_FOUND"}, status=status.HTTP_404_NOT_FOUND)

        delete_assignment_rule(rule)
        return Response(status=status.HTTP_204_NO_CONTENT)


# ── Coverage check (RF-8.5) ─────────────────────────────────


class CoverageView(APIView):
    """Check corrector coverage for an exam (RF-8.5)."""

    permission_classes = [IsAuthenticated, SubjectPermission("can_assign_correctors")]
    serializer_class = CoverageResponseSerializer

    @extend_schema(
        tags=["Grading"],
        summary="Coverage check (RF-8.5) 'can_assign_correctors' required",
        responses={200: CoverageResponseSerializer},
    )
    def get(self, request: Request, exam_pk: str) -> Response:
        from apps.exams.models.exams import Exam
        from apps.grading.services.grading import check_coverage

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
        summary="My grading tasks and pending reviews (RF-8.6)",
        parameters=[
            OpenApiParameter(
                name="exam_id",
                description="Filter tasks for a specific exam. When provided, all tasks "
                "(graded and ungraded) are returned.",
                required=False,
                type=str,
            ),
        ],
        responses={200: MyTasksResponseSerializer},
    )
    def get(self, request: Request) -> Response:
        from apps.exams.models.exams import Exam
        from apps.grading.services.grading import get_corrector_tasks

        exam_id = request.query_params.get("exam_id", None)
        if exam_id:
            try:
                exam = Exam.objects.get(pk=exam_id)
            except Exam.DoesNotExist:
                return Response({"error_code": "NOT_FOUND"}, status=status.HTTP_404_NOT_FOUND)
        else:
            exam = None

        tasks = get_corrector_tasks(request.user, exam=exam)
        reviews = get_pending_reviews_for_corrector(request.user, exam=exam)

        data = {
            "grading_tasks": tasks,
            "pending_reviews": reviews,
        }
        return Response(MyTasksResponseSerializer(data).data)


# ── Bulk publish (RF-7.9) ────────────────────────────────────


class PublishView(APIView):
    """Bulk publish grades for an exam (RF-7.9)."""

    permission_classes = [IsAuthenticated]
    serializer_class = PublishResultSerializer

    @extend_schema(
        operation_id="exams_publish_grades",
        tags=["Instances"],
        summary="Bulk-publish all graded instances of an exam (RF-7.9) "
        "'can_publish_grades' required",
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
        from apps.exams.models.exams import Exam

        try:
            exam = Exam.objects.get(pk=exam_pk)
        except Exam.DoesNotExist:
            return Response({"error_code": "NOT_FOUND"}, status=status.HTTP_404_NOT_FOUND)

        if not has_subject_permission(request.user, exam.subject, "can_publish_grades"):
            return Response({"error_code": "PERMISSION_DENIED"}, status=status.HTTP_403_FORBIDDEN)

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
        summary="Download composed instance PDF (RF-7.15) – with watermark and anti-download "
        "protections",
        description=(
            "**Serve the instance's composed PDF, with optional dynamic watermark and encryption.**\n\n"  # noqa: E501
            "Who can call this endpoint:\n"
            "- **Staff / superadmins** → always.\n"
            "- **Teachers / coordinators** → if they have `can_view_all_instances` or "
            "are assigned "
            "to this instance via an `AssignmentRule`.\n"
            "- **Students** → only for their own instance, and only if the exam's "
            "`student_permissions.can_view_pages` is `True` **and** a review window allows access "
            "(either `OPEN` with instance `IN_REVIEW`, or `CLOSED`/`COMPLETED` with "
            "`can_view_pages_after_review`).\n\n"
            "What the response contains:\n"
            "- **For staff/teachers** → plain `application/pdf`, `Content-Disposition: attachment`.\n"  # noqa: E501
            "- **For students with `can_download_pages=True`** → plain `application/pdf`, "
            "`Content-Disposition: inline` (view in browser).\n"
            "- **For students without `can_download_pages`** → encrypted "
            "`application/octet-stream`, "
            "`Content-Disposition: inline`. The PDF is encrypted with AES-256-GCM using a "
            "key derived "
            "from the user's ID, and must be decrypted client-side. Anti-cache headers are set.\n\n"  # noqa: E501
            "Every student download includes a dynamic watermark (name, NIA, date)."
        ),
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
            inst = ExamInstance.objects.select_related("exam", "student").get(pk=instance_pk)
        except ExamInstance.DoesNotExist:
            return Response({"error_code": "NOT_FOUND"}, status=status.HTTP_404_NOT_FOUND)

        user = request.user
        is_student_owner = inst.student_id is not None and inst.student_id == user.pk

        # Check access to pages.
        if not can_access_instance_data(inst, user, "can_view_pages"):
            return Response(
                {"error_code": "PERMISSION_DENIED"},
                status=status.HTTP_403_FORBIDDEN,
            )

        can_download = False
        if is_student_owner:
            can_download = inst.exam.student_permissions.get("can_download_pages", False)

        try:
            encrypt_for = str(user.pk) if (is_student_owner and not can_download) else None
            pdf_bytes = compose_instance_pdf(
                inst,
                watermark_for_student=is_student_owner,
                encrypt_for_user_id=encrypt_for,
            )
        except InstanceServiceError as exc:
            return Response({"error_code": exc.code}, status=status.HTTP_409_CONFLICT)

        content_type = "application/octet-stream" if encrypt_for else "application/pdf"
        disposition = "inline" if is_student_owner else "attachment"
        response = DjangoHttpResponse(pdf_bytes, content_type=content_type)
        response["Content-Disposition"] = f'{disposition}; filename="instance_{inst.pk}.pdf"'
        # Anti-cache headers for student access.
        if is_student_owner:
            response["Cache-Control"] = "no-store, no-cache, must-revalidate, private"
            response["Pragma"] = "no-cache"
            response["Expires"] = "0"
        return response
