"""
Business logic services for user accounts.

Services are the primary layer for business operations. Views delegate
to services, which coordinate models, external systems, and audit logging.

Modules:
    encryption: AES-256-GCM encryption/decryption for DNI (RF-16.3).
    password: Secure password generation (RF-2.7).
    user_service: User CRUD operations (RF-2.2 through RF-2.13).
"""
