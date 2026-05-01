"""
Object storage – MinIO / S3 (RNF-14).
"""

from ._paths import env

AWS_ACCESS_KEY_ID = env("MINIO_ACCESS_KEY", default="minioadmin")
AWS_SECRET_ACCESS_KEY = env("MINIO_SECRET_KEY", default="minioadmin")
AWS_STORAGE_BUCKET_NAME = env("MINIO_BUCKET_NAME", default="scdee")
AWS_S3_ENDPOINT_URL = env("MINIO_ENDPOINT_URL", default="http://minio:9000")
AWS_S3_REGION_NAME = env("MINIO_REGION", default="us-east-1")
AWS_S3_FILE_OVERWRITE = False
AWS_DEFAULT_ACL = None
AWS_QUERYSTRING_AUTH = True
AWS_S3_SIGNATURE_VERSION = "s3v4"
AWS_PRESIGNED_URL_EXPIRY = env.int("AWS_PRESIGNED_URL_EXPIRY", default=900)
