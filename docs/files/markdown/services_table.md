| App | Archivo(s) | Funciones principales | Responsabilidad |
|---|---|---|---|
| **accounts** | `services/encryption.py` | `encrypt_dni`, `decrypt_dni` | Cifrado y descifrado AES‑256‑GCM del DNI |
| | `services/password.py` | `generate_secure_password`, `validate_password_complexity` | Generación de contraseñas seguras y validación de complejidad |
| | `services/user_service.py` | `create_user`, `update_user`, `deactivate_user`, `reset_user_password`, `get_decrypted_dni`, `UserFilter` | CRUD de usuarios, cifrado del DNI, reseteo de contraseña e invalidación de tokens |
| | `services/import_export.py` | `import_users`, `export_users`, `parse_csv_file`, `parse_json_file` | Importación y exportación masiva de usuarios (CSV/JSON) |
| | `jwt_plugin.py` | `authenticate`, `refresh`, `revoke`, `validate_access_token` | Emisión, rotación, revocación y validación de tokens JWT |
| | `jwt_blacklist.py` | `add_to_blacklist`, `is_blacklisted`, `blacklist_all_user_tokens`, `get_user_token_generation` | Blacklist de tokens en Redis |
| | `backends.py` | `JWTAuthentication.authenticate` | Backend DRF que integra el plugin JWT y establece el contexto de tenant |
| **annotations** | `services/annotations.py` | `create_annotation`, `update_annotation`, `delete_annotation`, `ocr_grade_annotation`, `ocr_stylus_annotation` | CRUD de anotaciones, validación de audio, OCR sobre calificaciones y trazos |
| **audit** | `services/auditlog.py` | `log_event`, `get_client_ip` | Creación de registros de auditoría inmutables y extracción de IP real |
| **core** | `services/health.py` | `check_postgresql`, `check_redis`, `check_minio`, `check_celery` | Sondas de salud para los componentes de infraestructura |
| | `tenancy/tenant_context.py` | `set_current_organization_id`, `get_current_organization_id`, `clear_current_organization_id` | Almacenamiento thread‑local del contexto de organización |
| | `tenancy/managers.py` | `TenantManager`, `UnfilteredManager` | Managers de Django para filtrado automático por organización y bypass |
| | `middleware.py` | `ClearTenantContextMiddleware`, `AntiCacheMiddleware` | Limpieza del contexto de tenant tras cada petición y cabeceras anti‑caché |
| | `exceptions.py` | `api_exception_handler` | Formateo uniforme de todas las respuestas de error de la API |
| | `pagination.py` | `StandardPagination` | Paginación estándar con conteo total y metadatos |
| | `throttling.py` | `LoginRateThrottle`, `StandardUserThrottle`, `ManagerThrottle` | Limitación de peticiones por IP y rol |
| | `logging.py` | `JsonFormatter` | Formateo de logs en JSON estructurado |
| **courses** | `services/courses.py` | `create_course`, `update_course`, `_execute_transition` | Creación de cursos con transición automática del curso anterior |
| **exams** | `services/exam_service.py` | `create_exam`, `update_exam`, `delete_exam`, `create_model`, `upload_blank_pdf`, `create_page_profile`, `create_zone`, `delete_zone`, `create_problem`, `set_rubric`, `set_convocation` | CRUD completo de exámenes, modelos, perfiles de página, zonas, problemas, rúbricas y convocatorias |
| | `services/instrumented_pdf.py` | `generate_instrumented_pdf`, `_build_qr_content`, `_create_qr_overlay` | Generación del PDF instrumentado con códigos QR superpuestos |
| | `services/storage.py` | `upload_to_minio`, `download_from_minio`, `delete_minio_object`, `generate_presigned_url` | Operaciones de almacenamiento en MinIO (subida, descarga, borrado, URLs prefirmadas) |
| | `services/grade_export.py` | `export_grades` (devuelve tupla con contenido y tipo MIME) | Exportación de notas a CSV o JSON con columnas configurables |
| **grading** | `services/grading.py` | `grade_problem`, `grade_by_rubric`, `create_assignment_rule`, `delete_assignment_rule`, `check_coverage`, `get_corrector_tasks`, `can_user_grade_instance`, `instance_matches_rule` | Calificación manual y por rúbrica con control de concurrencia optimista, reglas de asignación, cobertura y tareas del corrector |
| **ingestion** | `services/ingestion_service.py` | `process_ingest_file` | Normalización de archivos (PDF o imagen), almacenamiento en MinIO y encolado de tareas de reconocimiento |
| | `services/dispatcher.py` | `recognize_page` | Pipeline de reconocimiento por página: QR, recorte de zonas e invocación de reconocedores |
| | `services/assembler.py` | `assemble_page`, `_assemble_with_qr`, `_assemble_by_proximity` | Ensamblado de páginas en instancias, incluyendo fallback por proximidad temporal e identificación del estudiante |
| | `services/matching.py` | `match_student`, `_ocr_tolerant_match`, `_name_similarity` | Matching de atributos OCR (NIA, DNI, nombre) contra la lista de convocados |
| **instances** | `services/instance_service.py` | `create_instance`, `transition_instance`, `check_assembling_complete`, `update_instance`, `delete_instance`, `reorder_pages`, `discard_page`, `move_page`, `attach_orphan_page`, `accept_extra_page`, `recalculate_total_score`, `bulk_publish`, `compose_instance_pdf`, `encrypt_pdf`, `derive_user_key` | Máquina de estados, gestión de páginas, cálculo de nota total, publicación masiva, composición de PDF con marca de agua y cifrado |
| | `services/watermark.py` | `WatermarkService.apply_watermark` | Aplicación de marcas de agua dinámicas (varios estilos) a las páginas servidas a estudiantes |
| | `services/access_control.py` | `can_access_instance_data` | Control de acceso a los datos de una instancia según rol, permisos del examen y estado de la revisión |
| **notifications** | `services/notifications.py` | `create_notification`, `mark_read`, `mark_all_read`, `archive_by_course`, `notify_grades_published`, `notify_review_opened`, `notify_review_result`, `notify_review_requests_summary`, `notify_assignment_created`, `notify_ingestion_issue`, `notify_auto_deletion_reminder` | Creación de notificaciones in‑app, envío de espejos por email y archivado durante transiciones |
| | `email.py` | `email_language_for`, `render_email`, `send_templated_email` | Renderizado y envío de emails multi‑idioma utilizando plantillas del sistema |
| **organizations** | `services/organization_config.py` | `get_org_config`, `update_org_config` | Lectura y actualización de la configuración por organización con caché Redis |
| | `services/search.py` | `CourseFilter`, `SubjectFilter`, `ExamFilter`, `InstanceFilter` | Filtros para la búsqueda jerárquica (cursos → asignaturas → exámenes → instancias) |
| **reviews** | `services/reviews.py` | `create_review`, `submit_review_requests`, `resolve_request`, `open_review`, `close_review`, `get_pending_reviews_for_corrector` | Gestión de ventanas de revisión, solicitudes, resolución y notificaciones asociadas |
| **subjects** | `services/subjects.py` | `create_subject`, `update_subject`, `delete_subject`, `create_group`, `delete_group`, `assign_member`, `unassign_member` | CRUD de asignaturas, grupos y membresías |
| | `services/permissions.py` | `update_permissions`, `has_subject_permission` | Gestión de permisos personalizados por membresía y verificación de permisos |
| | `services/import_export.py` | `import_subjects`, `export_subjects`, `parse_csv_to_subject_list` | Importación y exportación masiva de asignaturas (JSON/CSV) |
