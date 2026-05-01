"""
Vistas de la aplicación Organizations.

Contiene:
- CreateOrganizationView:   POST /organizations/          (solo superadmin)
- OrgConfigView:            GET/PATCH /config/            (org manager)
- HierarchicalSearchView:   GET /search/                  (org manager)

Todas las operaciones documentan exhaustivamente sus respuestas de error
siguiendo RNF-8 y utilizando los helpers de apps.core.openapi.
"""

from __future__ import annotations

import logging

from django.db import IntegrityError, transaction
from drf_spectacular.utils import OpenApiExample, OpenApiParameter, OpenApiResponse, extend_schema
from rest_framework import status
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.permissions import IsOrgManager, IsSuperAdmin
from apps.accounts.services.user_service import create_user
from apps.accounts.tasks import send_welcome_email
from apps.audit.services import ORG_CREATED, USER_CREATED, get_client_ip, log_event
from apps.core.openapi.openapi import (
    error_response,
    validation_error_response,
)
from apps.organizations.models import Organization
from apps.organizations.serializers import (
    CreateOrganizationSerializer,
    OrganizationResponseSerializer,
    OrgConfigSerializer,
)

logger = logging.getLogger(__name__)


# ── 1. Creación de organización con manager inicial ─────────────


class OrganizationCreateView(APIView):
    """Crear una organización y su primer usuario gestor.

    **Permisos**: solo superadministradores (IsSuperAdmin).
    **Atomía**: la organización y el manager se crean en una única
    transacción. Si el subdominio ya existe, se devuelve 409.

    La respuesta 201 incluye los datos de la organización recién creada.
    """

    permission_classes = [IsSuperAdmin]

    @extend_schema(
        request=CreateOrganizationSerializer,
        responses={
            201: OpenApiResponse(
                response=OrganizationResponseSerializer,
                description="Organización y gestor inicial creados exitosamente.",
                examples=[
                    OpenApiExample(
                        "EjemploCreacion",
                        value={
                            "id": "a1b2c3d4-...",
                            "name": "Universidad de Ejemplo",
                            "subdomain": "uejemplo",
                            "plan": "FREE",
                            "auth_mode": "JWT",
                            "is_active": True,
                            "created_at": "2026-01-01T00:00:00Z",
                        },
                    )
                ],
            ),
            400: validation_error_response(
                field_examples={
                    "name": ["FIELD_REQUIRED"],
                    "subdomain": ["FIELD_REQUIRED"],
                    "initial_manager.email": ["FIELD_REQUIRED"],
                },
                description="Error de validación. Revisar ``errors`` para detalle por campo.",
            ),
            401: error_response(["AUTHENTICATION_REQUIRED"], status_code=401),
            403: error_response(["PERMISSION_DENIED"], status_code=403),
            409: error_response(
                ["SUBDOMAIN_ALREADY_EXISTS", "ORGANIZATION_CREATION_FAILED"],
                status_code=409,
                description="Conflicto: el subdominio ya existe o no se pudo crear la "
                "organización.",
            ),
            429: error_response(["RATE_LIMIT_EXCEEDED"], status_code=429),
            500: error_response(["INTERNAL_ERROR"], status_code=500),
        },
        tags=["Organizations"],
        operation_id="create_organization",
        summary="Crear organización con gestor inicial",
        description=(
            "**Solo superadmin**.\n\n"
            "Crea una nueva organización y el usuario que actuará como primer "
            "gestor (`is_staff=True`) en una sola transacción atómica. "
            "La contraseña del gestor se genera aleatoriamente y se envía "
            "por correo electrónico.\n\n"
            "Este endpoint no requiere organización en el token JWT porque "
            "es una operación de sistema."
        ),
    )
    def post(self, request: Request) -> Response:
        serializer = CreateOrganizationSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        data = serializer.validated_data
        manager_data = data.pop("initial_manager")

        ip_address = get_client_ip(request)

        try:
            with transaction.atomic():
                org = Organization.objects.create(
                    name=data["name"],
                    subdomain=data["subdomain"],
                    plan=data.get("plan", "FREE"),
                )

                manager, raw_password = create_user(
                    organization=org,
                    email=manager_data["email"],
                    first_name=manager_data["first_name"],
                    last_name=manager_data["last_name"],
                    dni=manager_data.get("dni", ""),
                    nia=manager_data.get("nia", ""),
                    is_staff=True,
                )

        except IntegrityError as exc:
            error_msg = str(exc).lower()
            if "subdomain" in error_msg:
                return Response(
                    {
                        "error_code": "SUBDOMAIN_ALREADY_EXISTS",
                        "errors": {"subdomain": ["SUBDOMAIN_ALREADY_EXISTS"]},
                    },
                    status=status.HTTP_409_CONFLICT,
                )
            return Response(
                {"error_code": "ORGANIZATION_CREATION_FAILED"},
                status=status.HTTP_409_CONFLICT,
            )

        # Auditoría
        log_event(
            event_type=ORG_CREATED,
            actor=request.user,
            entity=org,
            ip_address=ip_address,
            payload={
                "name": org.name,
                "subdomain": org.subdomain,
                "plan": org.plan,
            },
        )
        log_event(
            event_type=USER_CREATED,
            actor=request.user,
            organization=org,
            entity=manager,
            ip_address=ip_address,
            payload={
                "email": manager.email,
                "is_staff": True,
                "created_with_org": True,
            },
        )

        send_welcome_email.delay(manager.email, raw_password, manager.first_name)

        return Response(
            OrganizationResponseSerializer(org).data,
            status=status.HTTP_201_CREATED,
        )


# ── 2. Configuración de organización ────────────────────────────


class OrgConfigView(APIView):
    """Leer y actualizar la configuración de la organización actual.

    **Permisos**: solo gestores de la organización (IsOrgManager).
    """

    permission_classes = [IsOrgManager]
    serializer_class = OrgConfigSerializer

    @extend_schema(
        tags=["Config"],
        summary="Obtener configuración de la organización (RF-15.1)",
        description=(
            "Devuelve todos los parámetros configurables para la organización "
            "del usuario autenticado. Si aún no existe un registro de "
            "configuración, se crea uno por defecto en este momento."
        ),
        responses={
            200: OrgConfigSerializer,
            401: error_response(["AUTHENTICATION_REQUIRED"], status_code=401),
            403: error_response(["PERMISSION_DENIED"], status_code=403),
            429: error_response(["RATE_LIMIT_EXCEEDED"], status_code=429),
        },
    )
    def get(self, request: Request) -> Response:
        from apps.organizations.services import get_org_config

        config = get_org_config(request.user.organization)

        data = {
            "default_ocr_engine": config.default_ocr_engine,
            "recognition_confidence_threshold": config.recognition_confidence_threshold,
            "assembly_timeout_seconds": config.assembly_timeout_seconds,
            "max_audio_duration_minutes": config.max_audio_duration_minutes,
            "max_page_size_mb": config.max_page_size_mb,
            "auto_delete_frequency_days": config.auto_delete_frequency_days,
            "delete_notice_days": config.delete_notice_days,
            "default_language": config.default_language,
            "temp_url_expiration_minutes": config.temp_url_expiration_minutes,
            "role_permission_defaults": config.role_permission_defaults,
            "grade_export_columns": config.grade_export_columns,
        }
        return Response(data)

    @extend_schema(
        tags=["Config"],
        summary="Actualizar configuración (RF-15.2)",
        description=(
            "Actualiza uno o varios parámetros de configuración de la "
            "organización. Solo los campos incluidos en el cuerpo serán "
            "modificados (PATCH parcial). Los cambios se registran en "
            "la auditoría y la caché se invalida inmediatamente."
        ),
        request=OrgConfigSerializer,
        responses={
            200: OrgConfigSerializer,
            400: validation_error_response(
                field_examples={
                    "recognition_confidence_threshold": ["MIN_VALUE"],
                    "max_page_size_mb": ["MIN_VALUE", "MAX_VALUE"],
                },
                description="Error de validación con códigos por campo.",
            ),
            401: error_response(["AUTHENTICATION_REQUIRED"], status_code=401),
            403: error_response(["PERMISSION_DENIED"], status_code=403),
            429: error_response(["RATE_LIMIT_EXCEEDED"], status_code=429),
        },
    )
    def patch(self, request: Request) -> Response:
        from apps.organizations.services import update_org_config

        changes = update_org_config(request.user.organization, request.data)

        if changes:
            log_event(
                event_type="CONFIG_UPDATED",
                actor=request.user,
                organization=request.user.organization,
                ip_address=get_client_ip(request),
                payload={"changes": changes},
            )

        from apps.organizations.services import get_org_config

        config = get_org_config(request.user.organization)
        data = {
            "default_ocr_engine": config.default_ocr_engine,
            "recognition_confidence_threshold": config.recognition_confidence_threshold,
            "assembly_timeout_seconds": config.assembly_timeout_seconds,
            "max_audio_duration_minutes": config.max_audio_duration_minutes,
            "max_page_size_mb": config.max_page_size_mb,
            "auto_delete_frequency_days": config.auto_delete_frequency_days,
            "delete_notice_days": config.delete_notice_days,
            "default_language": config.default_language,
            "temp_url_expiration_minutes": config.temp_url_expiration_minutes,
            "role_permission_defaults": config.role_permission_defaults,
            "grade_export_columns": config.grade_export_columns,
        }
        return Response(data)


# ── 3. Búsqueda jerárquica ─────────────────────────────────────


class HierarchicalSearchView(APIView):
    """Búsqueda jerárquica: cursos → asignaturas → exámenes → instancias.

    **Permisos**: solo gestores de la organización (IsOrgManager).

    Los parámetros de consulta permiten filtrar en cada nivel.
    Si no se pasa ningún filtro, se listan todos los cursos activos.
    Si se pasa solo ``course_id``, se listan las asignaturas de ese curso,
    etc.
    """

    permission_classes = [IsOrgManager]

    @extend_schema(
        tags=["Search"],
        summary="Búsqueda jerárquica (RF-14.3)",
        description=(
            "Navega por el árbol de datos académicos: **cursos → asignaturas "
            "→ exámenes → instancias**. El nivel de detalle depende de los "
            "parámetros de consulta enviados.\n\n"
            "- Sin filtros: lista de cursos.\n"
            "- `course_id`: lista de asignaturas del curso.\n"
            "- `subject_id`: lista de exámenes de la asignatura.\n"
            "- `exam_id`: lista de instancias del examen.\n\n"
            "Se pueden combinar con `search` para búsqueda textual en nombre "
            "o código, y con `status`/`has_issues` para instancias."
        ),
        parameters=[
            OpenApiParameter(
                name="course_id",
                description="UUID del curso para listar sus asignaturas.",
                required=False,
                type=str,
            ),
            OpenApiParameter(
                name="subject_id",
                description="UUID de la asignatura para listar sus exámenes.",
                required=False,
                type=str,
            ),
            OpenApiParameter(
                name="exam_id",
                description="UUID del examen para listar sus instancias.",
                required=False,
                type=str,
            ),
            OpenApiParameter(
                name="search",
                description="Texto libre para filtrar por nombre o código "
                "(en el nivel correspondiente).",
                required=False,
                type=str,
            ),
            OpenApiParameter(
                name="status",
                description="Solo para nivel de instancias: filtra por estado "
                "(ej. PUBLISHED, PENDING_REVIEW).",
                required=False,
                type=str,
            ),
            OpenApiParameter(
                name="has_issues",
                description="Solo para nivel de instancias: si true, solo instancias con "
                "incidencias.",
                required=False,
                type=bool,
            ),
        ],
        responses={
            200: OpenApiResponse(
                description="Resultado del nivel solicitado. La forma concreta "
                "varía según el nivel. Ver ejemplos.",
                examples=[
                    OpenApiExample(
                        "Cursos",
                        value=[
                            {
                                "id": "uuid",
                                "label": "2025-2026",
                                "is_active": True,
                                "status": "ACTIVE",
                            }
                        ],
                        description="Ejemplo de listado de cursos.",
                    ),
                    OpenApiExample(
                        "Instancias",
                        value=[
                            {
                                "id": "uuid",
                                "student_email": "alumno@example.com",
                                "model_label": "A",
                                "status": "PUBLISHED",
                                "has_issues": False,
                                "total_score": "8.50",
                            }
                        ],
                        description="Ejemplo de listado de instancias.",
                    ),
                ],
            ),
            401: error_response(["AUTHENTICATION_REQUIRED"], status_code=401),
            403: error_response(["PERMISSION_DENIED"], status_code=403),
            429: error_response(["RATE_LIMIT_EXCEEDED"], status_code=429),
        },
    )
    def get(self, request: Request) -> Response:
        course_id = request.query_params.get("course_id")
        subject_id = request.query_params.get("subject_id")
        exam_id = request.query_params.get("exam_id")
        search = request.query_params.get("search", "")

        if exam_id:
            return self._list_instances(request, exam_id)
        if subject_id:
            return self._list_exams(request, subject_id, search)
        if course_id:
            return self._list_subjects(request, course_id, search)
        return self._list_courses(request)

    def _list_courses(self, request: Request) -> Response:
        from apps.courses.models import AcademicCourse

        courses = AcademicCourse.objects.all().order_by("-is_active", "-created_at")
        data = [
            {
                "id": str(c.pk),
                "label": c.label,
                "is_active": c.is_active,
                "status": c.status,
            }
            for c in courses
        ]
        return Response(data)

    def _list_subjects(self, request, course_id, search) -> Response:
        from apps.subjects.models import Subject

        queryset = Subject.objects.filter(course_id=course_id).order_by("name")
        if search:
            from django.db.models import Q

            queryset = queryset.filter(Q(name__icontains=search) | Q(code__icontains=search))

        data = [
            {
                "id": str(s.pk),
                "name": s.name,
                "code": s.code,
                "semester": s.semester,
                "coordinator_email": s.coordinator.email,
            }
            for s in queryset.select_related("coordinator")
        ]
        return Response(data)

    def _list_exams(self, request, subject_id, search) -> Response:
        from apps.exams.models import Exam

        queryset = Exam.objects.filter(subject_id=subject_id)
        if search:
            queryset = queryset.filter(name__icontains=search)

        data = [
            {
                "id": str(e.pk),
                "name": e.name,
                "model_count": e.models.count(),
                "instance_count": e.instances.count(),
            }
            for e in queryset
        ]
        return Response(data)

    def _list_instances(self, request, exam_id) -> Response:
        from apps.instances.models import ExamInstance

        queryset = ExamInstance.objects.filter(exam_id=exam_id).select_related("student", "model")

        if s := request.query_params.get("status"):
            queryset = queryset.filter(status=s)
        if hi := request.query_params.get("has_issues"):
            queryset = queryset.filter(has_issues=hi.lower() == "true")

        data = [
            {
                "id": str(i.pk),
                "student_email": i.student.email if i.student else "",
                "model_label": i.model.label if i.model else "",
                "status": i.status,
                "has_issues": i.has_issues,
                "total_score": str(i.total_score) if i.total_score is not None else None,
            }
            for i in queryset
        ]
        return Response(data)
